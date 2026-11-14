"""Offline, copy-on-write index migration. The source data directory is retained.

Only publish the destination after every PDF has been indexed and verified. A
separate complete directory makes rollback an explicit configuration switch,
without trying to roll back research notes written after the migration.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import portalocker

from backend.core import KnowledgeBase
from backend.pdf_text import PARSER_VERSION, parse_pdf


@contextmanager
def stopped_source(source):
    lock_path = source / "qdrant" / ".lock"
    if not lock_path.is_file():
        raise ValueError("源知识库缺少 Qdrant 锁文件；请先确认 DATA_DIR 是原项目的知识库目录")
    handle = lock_path.open("r+b")
    acquired = False
    try:
        try:
            portalocker.lock(
                handle, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING
            )
            acquired = True
        except portalocker.exceptions.LockException as exc:
            raise RuntimeError(
                "原知识库仍在使用；请先停止后端和其他访问该 DATA_DIR 的 Python 进程"
            ) from exc
        yield
    finally:
        if acquired:
            portalocker.unlock(handle)
        handle.close()


def snapshot_data(source, staging):
    for item in source.rglob("*"):
        if item.is_symlink():
            raise ValueError("知识库包含符号链接，无法安全复制；请先使用普通数据目录")

    def ignore(directory, names):
        return [
            n
            for n in names
            if n.endswith(("-wal", "-shm"))
            or n == ".lock"
            or (Path(directory) == source and n == "qdrant")
        ]

    shutil.copytree(source, staging, ignore=ignore)
    # SQLite's backup API also includes any committed WAL contents. Copying only
    # the .sqlite3 file could silently lose the latest notes or reading progress.
    for database in source.rglob("*.sqlite3"):
        if "qdrant" in database.relative_to(source).parts:
            continue
        destination = staging / database.relative_to(source)
        # sqlite3's connection context manager commits but does not close the
        # handle. Keep them closed here or Windows cannot rename/delete the
        # staging directory while metadata.sqlite3 is still open.
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                with dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("知识库备份完整性检查失败，未切换任何数据")


def rebuild(settings, output, model_provider=None, progress=None):
    source, output = settings.data_dir.resolve(), Path(output).expanduser().resolve()
    if not (source / "metadata.sqlite3").is_file():
        raise ValueError("源知识库 metadata.sqlite3 不存在，请核对 --source-data-dir")
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("输出目录必须尚不存在，且不能与源目录互相包含")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name("." + output.name + ".building-" + uuid.uuid4().hex)
    source_model, documents = None, []
    with stopped_source(source):
        try:
            if progress:
                progress("复制现有文献和研究记录；不复制旧向量索引")
            snapshot_data(source, staging)
            with closing(sqlite3.connect(staging / "metadata.sqlite3")) as db:
                with db:
                    previous = db.execute(
                        "SELECT value FROM config WHERE key='embedding'"
                    ).fetchone()
                    source_model = previous[0] if previous else None
                    # Existing document UUIDs, ownership, filenames, reading tables,
                    # and all other databases remain in the copied snapshot.
                    db.execute("PRAGMA foreign_keys=ON")
                    db.execute("DELETE FROM chunks")
                    db.execute("DELETE FROM config WHERE key='lexical_version'")
                    db.execute(
                        "INSERT OR REPLACE INTO config VALUES('embedding',?)",
                        (settings.embedding_model,),
                    )
            kb = KnowledgeBase(replace(settings, data_dir=staging), model_provider)
            try:
                rows = kb.db.execute(
                    "SELECT * FROM documents WHERE status='ready' ORDER BY document_id"
                ).fetchall()
                if not rows:
                    raise ValueError("源知识库没有已入库论文，无需重建")
                for index, row in enumerate(rows, 1):
                    if progress:
                        progress(f"重建 {index}/{len(rows)}：{row['filename']}")
                    data = (kb.pdf_dir / (row["document_id"] + ".pdf")).read_bytes()
                    if hashlib.sha256(data).hexdigest() != row["sha256"]:
                        raise ValueError(
                            "PDF 内容与上传时的校验值不同；已停止，避免历史引用指向变化后的原文"
                        )
                    chunks, pages, empty = parse_pdf(
                        data, kb.settings, getattr(kb.models, "count_tokens", None)
                    )
                    if pages != row["pages"]:
                        raise ValueError(
                            "PDF 页数与原文献记录不同；已停止，避免破坏阅读页码和历史引用"
                        )
                    kb._index_chunks(row["owner"], row["document_id"], chunks)
                    with kb.db:
                        kb.db.execute(
                            "UPDATE documents SET chunk_count=?,empty_pages=? WHERE document_id=?",
                            (len(chunks), json.dumps(empty), row["document_id"]),
                        )
                    documents.append(
                        {"document_id": row["document_id"], "chunks": len(chunks), "pages": pages}
                    )
                total = sum(item["chunks"] for item in documents)
                if (
                    kb.vectors.count(kb.collection, exact=True).count != total
                    or kb.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] != total
                    or kb.db.execute("SELECT COUNT(*) FROM chunk_terms").fetchone()[0] != total
                    or kb.db.execute("PRAGMA foreign_key_check").fetchall()
                ):
                    raise RuntimeError("重建后的文本、关键词或向量记录不一致，未发布新目录")
                with kb.db:
                    kb.db.execute(
                        "INSERT OR REPLACE INTO config VALUES('parser_version',?)",
                        (PARSER_VERSION,),
                    )
            finally:
                kb.close()
            report = {
                "version": "0.8.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_data_dir": str(source),
                "source_embedding_model": source_model,
                "embedding_model": settings.embedding_model,
                "parser": PARSER_VERSION,
                "chunk_tokens": settings.chunk_tokens,
                "chunk_chars": settings.chunk_size,
                "documents": documents,
                "total_chunks": sum(d["chunks"] for d in documents),
                "preserved": [
                    "document UUIDs",
                    "PDF files",
                    "reading progress",
                    "notes",
                    "conversation snapshots",
                    "translations",
                    "images",
                    "task records",
                ],
            }
            (staging / "reindex-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if output.exists():
                raise FileExistsError("输出目录在重建期间已出现，未覆盖该目录")
            os.rename(staging, output)
            return report
        finally:
            if staging.exists():
                shutil.rmtree(staging)
