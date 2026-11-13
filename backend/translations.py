"""Durable, owner-scoped PDF translation with explicit jobs and completed-page reuse.

Read source pages directly from PDF, not overlapping retrieval chunks. Model calls
run outside all storage locks. Locks that also touch the knowledge base always use
kb.lock before self.lock, including deletion, so workers cannot resurrect data.
"""

import hashlib
import os
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pymupdf

from backend.generation import DeepSeek, GenerationError

MAX_PAGES = 50
MAX_PAGE_CHARS = 20000
MAX_JOB_CHARS = 200000
SEGMENT_CHARS = 1800
PENDING = ("queued", "running")


class TranslationConflict(Exception):
    pass


class TranslationBusy(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def split_segments(text, limit=SEGMENT_CHARS):
    """Partition every character exactly once, preferring paragraph/sentence ends."""
    parts = []
    while text:
        end = min(limit, len(text))
        if end < len(text):
            ends = [m.end() for m in re.finditer(r"\n|[。！？.!?]\s+", text[:end])]
            if ends and ends[-1] >= limit // 2:
                end = ends[-1]
        parts.append(text[:end])
        text = text[end:]
    return parts


class TranslationService:
    def __init__(self, kb, provider=None):
        self.kb = kb
        self.provider = provider if provider is not None else DeepSeek()
        self.model = (
            getattr(self.provider, "model", None) or os.getenv("DEEPSEEK_MODEL", "").strip()
        )
        endpoint = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
        self.cache_model = f"{endpoint}|{self.model}"
        self.enabled = callable(getattr(self.provider, "translate", None)) and (
            not isinstance(self.provider, DeepSeek)
            or bool(self.model and os.getenv("DEEPSEEK_API_KEY", "").strip())
        )
        self.lock = threading.RLock()
        self.closed = False
        self.db = sqlite3.connect(
            kb.settings.data_dir / "translations.sqlite3", check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS translation_jobs(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                document_id TEXT NOT NULL, filename TEXT NOT NULL,
                start_page INTEGER NOT NULL, end_page INTEGER NOT NULL,
                target_language TEXT NOT NULL, model TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(owner, request_id)
            );
            CREATE INDEX IF NOT EXISTS translation_owner ON translation_jobs(owner, created_at);
            CREATE TABLE IF NOT EXISTS translation_parts(
                job_id TEXT NOT NULL REFERENCES translation_jobs(id) ON DELETE CASCADE,
                page INTEGER NOT NULL, original TEXT NOT NULL, translation TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, reused INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(job_id, page)
            );
            CREATE TABLE IF NOT EXISTS translation_cache(
                owner TEXT NOT NULL, document_id TEXT NOT NULL, page INTEGER NOT NULL,
                target_language TEXT NOT NULL, model TEXT NOT NULL, source_hash TEXT NOT NULL,
                translation TEXT NOT NULL,
                PRIMARY KEY(owner, document_id, page, target_language, model, source_hash)
            );
        """)
        with self.db:
            self.db.execute(
                "UPDATE translation_jobs SET status='failed', error=?, updated_at=? "
                "WHERE status IN ('queued','running')",
                ("后端在翻译完成前停止；已完成页保留，检查平台记录后可手动重新提交", now()),
            )
            self.db.execute("UPDATE translation_parts SET status='pending' WHERE status='running'")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-translation")

    def config(self):
        return {"enabled": self.enabled, "model": self.model, "max_pages": MAX_PAGES}

    def source_pages(self, owner, document_id, start_page, end_page):
        with self.kb.lock:
            document = self.kb.document(owner, document_id)
            if not 1 <= start_page <= end_page <= document["pages"]:
                raise ValueError("页码范围无效，请使用 PDF 物理页码")
            if end_page - start_page + 1 > MAX_PAGES:
                raise ValueError(f"单次最多翻译 {MAX_PAGES} 页，请分批选择")
            with pymupdf.open(self.kb.pdf_dir / f"{document_id}.pdf") as pdf:
                pages = [
                    {"page": n, "original": pdf[n - 1].get_text("text", sort=True).strip()}
                    for n in range(start_page, end_page + 1)
                ]
            if any(len(p["original"]) > MAX_PAGE_CHARS for p in pages):
                raise ValueError("所选页面文本过多，请拆分 PDF 后上传；未截断原文")
            if sum(len(p["original"]) for p in pages) > MAX_JOB_CHARS:
                raise ValueError("所选文本超过 20 万字符，请缩小页码范围")
            return document, pages

    def _row(self, owner, job_id):
        row = self.db.execute(
            "SELECT * FROM translation_jobs WHERE id=? AND owner=?", (job_id, owner)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        self.kb.document(owner, row["document_id"])
        return row

    def _public(self, row, detail=False):
        result = {k: row[k] for k in row.keys() if k != "owner"}
        # History polling needs counts, not every historical source and translated page.
        counts = self.db.execute(
            "SELECT COUNT(*) AS total_pages, COALESCE(SUM(status='completed'),0) AS completed_pages, "
            "COALESCE(SUM(status='skipped'),0) AS skipped_pages, COALESCE(SUM(reused),0) AS reused_pages "
            "FROM translation_parts WHERE job_id=?",
            (row["id"],),
        ).fetchone()
        result.update(dict(counts))
        if detail:
            result["pages"] = [
                dict(p)
                for p in self.db.execute(
                    "SELECT page,original,translation,status,reused FROM translation_parts "
                    "WHERE job_id=? ORDER BY page",
                    (row["id"],),
                )
            ]
        return result

    def get(self, owner, job_id):
        with self.kb.lock, self.lock:
            return self._public(self._row(owner, job_id), True)

    def list(self, owner):
        with self.kb.lock, self.lock:
            available = {d["document_id"] for d in self.kb.documents(owner)}
            rows = self.db.execute(
                "SELECT * FROM translation_jobs WHERE owner=? ORDER BY created_at DESC LIMIT 50",
                (owner,),
            ).fetchall()
            return [self._public(r) for r in rows if r["document_id"] in available]

    def submit(self, owner, request_id, document_id, start_page, end_page, target_language):
        if target_language not in {"zh", "en"}:
            raise ValueError("翻译目标只支持简体中文或英语")
        with self.kb.lock, self.lock:
            self.kb.document(owner, document_id)
            existing = self.db.execute(
                "SELECT * FROM translation_jobs WHERE owner=? AND request_id=?", (owner, request_id)
            ).fetchone()
            if existing:
                params = (document_id, start_page, end_page, target_language)
                if (
                    tuple(
                        existing[k]
                        for k in ("document_id", "start_page", "end_page", "target_language")
                    )
                    != params
                ):
                    raise TranslationConflict("该请求编号已用于不同翻译参数")
                return self._public(existing, True)
            if self.closed or not self.enabled:
                raise ValueError(
                    "论文翻译尚未启用，请配置后端 DEEPSEEK_API_KEY 和 DEEPSEEK_MODEL 后重启"
                )
            if self.db.execute(
                "SELECT 1 FROM translation_jobs WHERE owner=? AND status IN ('queued','running')",
                (owner,),
            ).fetchone():
                raise TranslationBusy("你已有翻译任务进行中，请等待完成或先停止该任务")
            if (
                self.db.execute(
                    "SELECT COUNT(*) FROM translation_jobs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                >= 4
            ):
                raise TranslationBusy("翻译队列已满，请稍后再试")
            document, pages = self.source_pages(owner, document_id, start_page, end_page)
            if not any(p["original"] for p in pages):
                raise ValueError("所选页面没有可提取文本；扫描页请先进行 OCR")
            job_id, timestamp = str(uuid4()), now()
            with self.db:
                self.db.execute(
                    "INSERT INTO translation_jobs VALUES(?,?,?,?,?,?,?,?,?,'queued',NULL,?,?)",
                    (
                        job_id,
                        owner,
                        request_id,
                        document_id,
                        document["filename"],
                        start_page,
                        end_page,
                        target_language,
                        self.model,
                        timestamp,
                        timestamp,
                    ),
                )
                for p in pages:
                    source_hash = hashlib.sha256(p["original"].encode()).hexdigest()
                    cached = self.db.execute(
                        "SELECT translation FROM translation_cache WHERE owner=? AND document_id=? "
                        "AND page=? AND target_language=? AND model=? AND source_hash=?",
                        (
                            owner,
                            document_id,
                            p["page"],
                            target_language,
                            self.cache_model,
                            source_hash,
                        ),
                    ).fetchone()
                    status = (
                        "skipped" if not p["original"] else "completed" if cached else "pending"
                    )
                    self.db.execute(
                        "INSERT INTO translation_parts VALUES(?,?,?,?,?,?)",
                        (
                            job_id,
                            p["page"],
                            p["original"],
                            cached[0] if cached else "",
                            status,
                            bool(cached),
                        ),
                    )
            try:
                self.executor.submit(self._work, owner, job_id)
            except RuntimeError:
                self._finish(job_id, "failed", "后端正在关闭，请稍后重新提交")
            return self._public(self._row(owner, job_id), True)

    def _active(self, owner, job_id):
        with self.kb.lock, self.lock:
            try:
                row = self._row(owner, job_id)
                return dict(row) if not self.closed and row["status"] in PENDING else None
            except KeyError:
                return None

    def _finish(self, job_id, status, error=None):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE translation_jobs SET status=?,error=?,updated_at=? "
                "WHERE id=? AND status IN ('queued','running')",
                (status, error, now(), job_id),
            )
            if status not in PENDING:
                self.db.execute(
                    "UPDATE translation_parts SET status='pending' "
                    "WHERE job_id=? AND status='running'",
                    (job_id,),
                )

    def _work(self, owner, job_id):
        if not self._active(owner, job_id):
            return
        self._finish(job_id, "running")
        try:
            with self.lock:
                pages = [
                    dict(p)
                    for p in self.db.execute(
                        "SELECT * FROM translation_parts WHERE job_id=? ORDER BY page", (job_id,)
                    )
                ]
            for page in pages:
                row = self._active(owner, job_id)
                if not row:
                    return
                if page["status"] != "pending":
                    continue
                with self.kb.lock, self.lock, self.db:
                    if not self._active(owner, job_id):
                        return
                    self.db.execute(
                        "UPDATE translation_parts SET status='running' WHERE job_id=? AND page=?",
                        (job_id, page["page"]),
                    )
                pieces = []
                for part in split_segments(page["original"]):
                    if not self._active(owner, job_id):
                        return
                    if not part.strip():
                        continue
                    translated = self.provider.translate(part, row["target_language"])
                    if not isinstance(translated, str) or not translated.strip():
                        raise GenerationError("模型返回空译文")
                    if getattr(translated, "finish_reason", "stop") != "stop":
                        raise GenerationError("本段译文未完成，未保存为完整翻译")
                    if len(translated) > 30000:
                        raise GenerationError("模型返回的译文过长，请核对平台输出")
                    pieces.append(translated.strip())
                translation = "\n\n".join(pieces)
                with self.kb.lock, self.lock:
                    if not self._active(owner, job_id):
                        return
                    with self.db:
                        self.db.execute(
                            "UPDATE translation_parts SET translation=?,status='completed' "
                            "WHERE job_id=? AND page=?",
                            (translation, job_id, page["page"]),
                        )
                        self.db.execute(
                            "INSERT OR REPLACE INTO translation_cache VALUES(?,?,?,?,?,?,?)",
                            (
                                owner,
                                row["document_id"],
                                page["page"],
                                row["target_language"],
                                self.cache_model,
                                hashlib.sha256(page["original"].encode()).hexdigest(),
                                translation,
                            ),
                        )
            self._finish(job_id, "succeeded")
        except GenerationError as exc:
            self._finish(job_id, "failed", f"翻译未完成：{exc}。已完成页保留，系统不会自动重试")
        except Exception:
            self._finish(job_id, "failed", "翻译或保存失败，请检查磁盘和平台记录；已完成页保留")

    def cancel(self, owner, job_id):
        with self.kb.lock, self.lock:
            self._row(owner, job_id)
            self._finish(job_id, "cancelled", "已停止后续翻译；已发送的平台请求可能仍会计费")
            return self._public(self._row(owner, job_id), True)

    def purge(self, owner, document_id):
        """Called while kb.lock is held, after an authorized document deletion."""
        with self.lock, self.db:
            self.db.execute(
                "DELETE FROM translation_jobs WHERE owner=? AND document_id=?", (owner, document_id)
            )
            self.db.execute(
                "DELETE FROM translation_cache WHERE owner=? AND document_id=?",
                (owner, document_id),
            )

    def export(self, owner, job_id):
        job = self.get(owner, job_id)
        if job["status"] in PENDING or not job["completed_pages"]:
            raise TranslationConflict("任务尚未结束或还没有完成的译文")
        language = {"zh": "简体中文", "en": "英语"}[job["target_language"]]
        lines = [
            "研迹 PaperTrail 论文翻译",
            f"文献：{job['filename']}",
            f"目标语言：{language}；模型：{job['model']}",
            f"任务状态：{job['status']}；完成 {job['completed_pages']}/{job['total_pages']} 页",
            "页码为 PDF 物理页码。AI 译文仅供辅助阅读，请核对术语、公式及原文。",
        ]
        if job["error"]:
            lines.append(job["error"])
        for page in job["pages"]:
            lines.extend(
                [
                    "",
                    f"===== 第 {page['page']} 页 =====",
                    "【原文】",
                    page["original"],
                    "【译文】",
                    page["translation"]
                    if page["status"] == "completed"
                    else "[无可提取文本，未翻译]"
                    if page["status"] == "skipped"
                    else "[本页未完成]",
                ]
            )
        return "\n".join(lines).encode("utf-8-sig")

    def close(self):
        with self.lock, self.db:
            self.closed = True
            self.db.execute(
                "UPDATE translation_jobs SET status='cancelled',error=?,updated_at=? "
                "WHERE status IN ('queued','running')",
                ("后端关闭，未完成页没有自动重试；已完成页保留", now()),
            )
        self.executor.shutdown(wait=True)
        with self.db:
            self.db.execute("UPDATE translation_parts SET status='pending' WHERE status='running'")
        self.db.close()
