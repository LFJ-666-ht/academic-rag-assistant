"""Persistent, tenant-scoped RAG; SQLite is the source of truth for chunk text."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from qdrant_client import QdrantClient, models

from backend import lexical
from backend.lexical import tokens
from backend.local_models import LocalModels
from backend.pdf_text import PARSER_VERSION, parse_pdf


@dataclass
class Settings:
    data_dir: Path
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    reranker_model: str = "BAAI/bge-reranker-base"
    chunk_size: int = 700
    chunk_overlap: int = 100
    max_pages: int = 500
    max_chunks: int = 10000
    max_upload_mb: int = 30
    chunk_tokens: int = 320
    query_instruction: str = "auto"
    embedding_batch_size: int = 16
    reranker_max_tokens: int = 512
    query_max_tokens: int = 128
    context_neighbors: int = 1
    context_chars: int = 1800
    rerank_min_score: float | None = None

    def __post_init__(self):
        if not 0 <= self.chunk_overlap < self.chunk_size or self.chunk_tokens < 16:
            raise ValueError("分块配置无效：重叠必须小于分块大小，token 上限至少为 16")
        if not 1 <= self.embedding_batch_size <= 128:
            raise ValueError("EMBEDDING_BATCH_SIZE 必须在 1–128 之间")
        if not 0 <= self.context_neighbors <= 2 or self.context_chars < self.chunk_size:
            raise ValueError("上下文扩展配置无效")
        if not 16 <= self.query_max_tokens < self.reranker_max_tokens - 16:
            raise ValueError("Reranker 问题 token 预算必须给文段留出空间")
        if self.rerank_min_score is not None and not math.isfinite(self.rerank_min_score):
            raise ValueError("RERANK_MIN_SCORE 必须是有限数值或留空")

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "data")).resolve(),
            embedding_model=os.getenv("EMBEDDING_MODEL", cls.embedding_model),
            reranker_model=os.getenv("RERANKER_MODEL", cls.reranker_model),
            query_instruction=os.getenv("EMBEDDING_QUERY_INSTRUCTION", "auto"),
            embedding_batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "16")),
            chunk_tokens=int(os.getenv("CHUNK_MAX_TOKENS", "320")),
            chunk_size=int(os.getenv("CHUNK_MAX_CHARS", "700")),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP_CHARS", "100")),
            context_neighbors=int(os.getenv("RETRIEVAL_CONTEXT_NEIGHBORS", "1")),
            context_chars=int(os.getenv("RETRIEVAL_CONTEXT_CHARS", "1800")),
            rerank_min_score=float(os.environ["RERANK_MIN_SCORE"])
            if os.getenv("RERANK_MIN_SCORE", "").strip()
            else None,
        )


def bm25(question, chunks, limit=20):
    """Actual BM25 with term frequency, IDF and length normalization."""
    if not chunks:
        return []
    docs = [Counter(tokens(c["text"])) for c in chunks]
    lengths = [sum(d.values()) for d in docs]
    average = sum(lengths) / len(docs) or 1
    terms = set(tokens(question))
    frequency = {t: sum(t in d for d in docs) for t in terms}
    scored = []
    for chunk, doc, length in zip(chunks, docs, lengths):
        score = 0.0
        for term in terms:
            tf = doc.get(term, 0)
            if tf:
                idf = math.log(1 + (len(docs) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / average))
        if score > 0:
            scored.append((score, chunk))
    return [c for _, c in sorted(scored, key=lambda x: x[0], reverse=True)[:limit]]


def rrf(*rankings, limit=30):
    scores, by_id = {}, {}
    for ranking in rankings:
        seen = set()
        for rank, chunk in enumerate(ranking, start=1):
            cid = chunk["chunk_id"]
            if cid in seen:
                continue
            seen.add(cid)
            by_id[cid] = chunk
            scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
    return [by_id[cid] for cid in sorted(scores, key=scores.get, reverse=True)[:limit]]


def locked(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)

    return wrapper


def active_operation(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.lifecycle:
            if self.closed:
                raise RuntimeError("知识库已关闭")
            self.active += 1
        try:
            return method(self, *args, **kwargs)
        finally:
            with self.lifecycle:
                self.active -= 1
                self.lifecycle.notify_all()

    return wrapper


class KnowledgeBase:
    collection = "academic_chunks_v1"

    def __init__(self, settings, model_provider=None):
        self.settings = settings
        self.lock = threading.RLock()
        self.ingest_lock = threading.Lock()
        self.query_lock = threading.RLock()
        self.lifecycle = threading.Condition()
        self.active, self.closed = 0, False
        self.query_cache = OrderedDict()
        self.models = model_provider or LocalModels(settings)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir = settings.data_dir / "pdfs"
        self.pdf_dir.mkdir(exist_ok=True)
        self.db = sqlite3.connect(settings.data_dir / "metadata.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents(
              document_id TEXT PRIMARY KEY, owner TEXT NOT NULL, sha256 TEXT NOT NULL,
              filename TEXT NOT NULL, pages INTEGER NOT NULL, chunk_count INTEGER NOT NULL,
              empty_pages TEXT NOT NULL, status TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(owner, sha256));
            CREATE TABLE IF NOT EXISTS chunks(
              chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(document_id)
              ON DELETE CASCADE, page INTEGER NOT NULL, text TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_owner ON documents(owner);
            CREATE INDEX IF NOT EXISTS idx_document ON chunks(document_id);
            CREATE TABLE IF NOT EXISTS reading_progress(
              document_id TEXT PRIMARY KEY REFERENCES documents(document_id) ON DELETE CASCADE,
              status TEXT NOT NULL CHECK(status IN ('unread','reading','completed')),
              last_page INTEGER NOT NULL DEFAULT 0,
              furthest_page INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL,
              completed_at TEXT);
        """)
        stored = self.db.execute("SELECT value FROM config WHERE key='embedding'").fetchone()
        if stored and stored[0] != settings.embedding_model:
            self.db.close()
            raise ValueError(
                "Embedding 模型与现有索引不一致；请先运行 scripts/reindex.py 在新目录重建，再一起切换 DATA_DIR 和 EMBEDDING_MODEL。原数据无需删除。"
            )
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO config VALUES ('embedding', ?)", (settings.embedding_model,)
            )
        try:
            self.vectors = QdrantClient(path=str(settings.data_dir / "qdrant"))
        except Exception:
            self.db.close()
            raise
        try:
            self._initialize_retrieval()
        except Exception:
            self.vectors.close()
            self.db.close()
            raise
        # Recover interrupted ingestion/deletion before serving requests.
        for row in self.db.execute("SELECT * FROM documents WHERE status!='ready'").fetchall():
            self._purge(row["document_id"])

    def _initialize_retrieval(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS chunk_details(
                chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL, section TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS idx_chunk_page ON chunks(document_id,page);
        """)
        with self.db:
            # Preserve old chunk IDs; old insertion order is the document order.
            self.db.execute("""
                INSERT OR IGNORE INTO chunk_details(chunk_id,ordinal,section)
                SELECT chunk_id,ROW_NUMBER() OVER(PARTITION BY document_id ORDER BY rowid)-1,'' FROM chunks
            """)
        lexical.initialize(self.db)

    def _filter(self, document_id):
        return models.Filter(
            must=[
                models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))
            ]
        )

    def _purge(self, document_id):
        if self.vectors.collection_exists(self.collection):
            self.vectors.delete(
                self.collection, models.FilterSelector(filter=self._filter(document_id)), wait=True
            )
        (self.pdf_dir / f"{document_id}.pdf").unlink(missing_ok=True)
        with self.db:
            self.db.execute("DELETE FROM documents WHERE document_id=?", (document_id,))

    def _index_chunks(self, owner, document_id, chunks, on_progress=None):
        size = self.settings.embedding_batch_size
        for offset in range(0, len(chunks), size):
            batch = chunks[offset : offset + size]
            if on_progress:
                on_progress("embedding", offset, len(chunks))
            vectors = self.models.encode([c["text"] for c in batch])
            if len(vectors) != len(batch) or not vectors or not vectors[0]:
                raise ValueError("Embedding 返回的向量数量或维度无效")
            # Model inference above deliberately runs outside the DB/Qdrant lock.
            with self.lock:
                if not self.vectors.collection_exists(self.collection):
                    self.vectors.create_collection(
                        self.collection,
                        vectors_config=models.VectorParams(
                            size=len(vectors[0]), distance=models.Distance.COSINE
                        ),
                    )
                points = []
                with self.db:
                    for number, (chunk, vector) in enumerate(
                        zip(batch, vectors, strict=True), offset
                    ):
                        signature = hashlib.sha256(chunk["text"].encode()).hexdigest()
                        cid = str(
                            uuid.uuid5(
                                uuid.UUID(document_id), f"{PARSER_VERSION}:{number}:{signature}"
                            )
                        )
                        self.db.execute(
                            "INSERT INTO chunks VALUES(?,?,?,?)",
                            (cid, document_id, chunk["page"], chunk["text"]),
                        )
                        self.db.execute(
                            "INSERT INTO chunk_details VALUES(?,?,?)",
                            (cid, number, chunk.get("section", "")),
                        )
                        lexical.insert(self.db, cid, document_id, owner, chunk["text"])
                        points.append(
                            models.PointStruct(
                                id=cid,
                                vector=vector,
                                payload={"owner": owner, "document_id": document_id},
                            )
                        )
                if on_progress:
                    on_progress("indexing", offset, len(chunks))
                self.vectors.upsert(self.collection, points=points, wait=True)

    @active_operation
    def ingest(self, owner, filename, data, on_progress=None):
        if len(data) > self.settings.max_upload_mb * 1024 * 1024:
            raise ValueError("PDF 超过上传大小限制")
        # One ingestion writer avoids duplicate-upload races. Ordinary reads and
        # retrieval do not acquire this lock, even during long model encoding.
        with self.ingest_lock:
            digest = hashlib.sha256(data).hexdigest()
            with self.lock:
                old = self.db.execute(
                    "SELECT * FROM documents WHERE owner=? AND sha256=?", (owner, digest)
                ).fetchone()
                if old and old["status"] == "ready":
                    return {**self.document(owner, old["document_id"]), "duplicate": True}
                if old:
                    self._purge(old["document_id"])
            if on_progress:
                on_progress("parsing", 0, 0)
            chunks, pages, empty_pages = parse_pdf(
                data, self.settings, getattr(self.models, "count_tokens", None)
            )
            document_id = str(uuid.uuid4())
            filename = filename.replace("\\", "/").split("/")[-1][:255] or "paper.pdf"
            with self.lock, self.db:
                self.db.execute(
                    "INSERT INTO documents(document_id,owner,sha256,filename,pages,chunk_count,empty_pages,status) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        document_id,
                        owner,
                        digest,
                        filename,
                        pages,
                        len(chunks),
                        json.dumps(empty_pages),
                        "indexing",
                    ),
                )
            try:
                (self.pdf_dir / f"{document_id}.pdf").write_bytes(data)
                self._index_chunks(owner, document_id, chunks, on_progress)
                with self.lock, self.db:
                    self.db.execute(
                        "UPDATE documents SET status='ready' WHERE document_id=?", (document_id,)
                    )
                return {**self.document(owner, document_id), "duplicate": False}
            except Exception:
                with self.lock:
                    self._purge(document_id)
                raise

    @locked
    def documents(self, owner):
        return [
            {**dict(r), "reading": self._reading(r["document_id"], r["pages"])}
            for r in self.db.execute(
                "SELECT * FROM documents WHERE owner=? AND status='ready' ORDER BY created_at DESC",
                (owner,),
            )
        ]

    @locked
    def document(self, owner, document_id):
        row = self.db.execute(
            "SELECT * FROM documents WHERE owner=? AND document_id=? AND status='ready'",
            (owner, document_id),
        ).fetchone()
        if not row:
            raise KeyError("论文不存在")
        return {**dict(row), "reading": self._reading(document_id, row["pages"])}

    def _reading(self, document_id, pages):
        """Caller holds kb.lock and has checked the document owner."""
        row = self.db.execute(
            "SELECT status,last_page,furthest_page,updated_at,completed_at "
            "FROM reading_progress WHERE document_id=?",
            (document_id,),
        ).fetchone()
        result = (
            dict(row)
            if row
            else {
                "status": "unread",
                "last_page": 0,
                "furthest_page": 0,
                "updated_at": None,
                "completed_at": None,
            }
        )
        result["progress_percent"] = result["furthest_page"] * 100 // pages if pages else 0
        return result

    @locked
    def update_reading(self, owner, document_id, *, page=None, status=None):
        """A page visit saves a bookmark; only an explicit action completes a paper.

        Browsing backward changes the resume page but never reduces the furthest
        page. Resetting to unread is explicit. The document foreign key makes
        deletion and reading cleanup atomic in the existing metadata database.
        """
        document = self.document(owner, document_id)
        if (page is None) == (status is None):
            raise ValueError("每次只提交 page 或 status 其中一项")
        current = document["reading"]
        timestamp = datetime.now(timezone.utc).isoformat()
        last, furthest, completed = (
            current["last_page"],
            current["furthest_page"],
            current["completed_at"],
        )
        if page is not None:
            if type(page) is not int or not 1 <= page <= document["pages"]:
                raise ValueError("阅读页码超出 PDF 范围")
            last, furthest = page, max(furthest, page)
            state = "reading" if current["status"] == "unread" else current["status"]
        else:
            if status not in {"unread", "reading", "completed"}:
                raise ValueError("阅读状态无效")
            state = status
            if status == "unread":
                last, furthest, completed = 0, 0, None
            elif status == "completed":
                last = furthest = document["pages"]
                completed = completed or timestamp
            else:
                completed = None
        with self.db:
            self.db.execute(
                "INSERT INTO reading_progress VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(document_id) DO UPDATE SET status=excluded.status, "
                "last_page=excluded.last_page,furthest_page=excluded.furthest_page, "
                "updated_at=excluded.updated_at,completed_at=excluded.completed_at",
                (document_id, state, last, furthest, timestamp, completed),
            )
        return self._reading(document_id, document["pages"])

    @locked
    def delete(self, owner, document_id):
        self.document(owner, document_id)
        with self.db:
            self.db.execute(
                "UPDATE documents SET status='deleting' WHERE document_id=?", (document_id,)
            )
        self._purge(document_id)

    def _query_vectors(self, questions):
        # Bounded, process-local cache. The model identity prevents stale vectors
        # if an injected provider changes; nothing from queries is written to disk.
        with self.query_lock:
            missing = list(
                dict.fromkeys(q for q in questions if (id(self.models), q) not in self.query_cache)
            )
            if missing:
                encoded = self.models.encode(missing, query=True)
                for q, vector in zip(missing, encoded, strict=True):
                    self.query_cache[(id(self.models), q)] = vector
            result = [self.query_cache[(id(self.models), q)] for q in questions]
            for q in questions:
                self.query_cache.move_to_end((id(self.models), q))
            while len(self.query_cache) > 128:
                self.query_cache.popitem(last=False)
            return result

    def _rows_by_ids(self, owner, ids):
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT c.*,d.filename,m.ordinal,m.section FROM chunks c JOIN documents d USING(document_id) "
                "LEFT JOIN chunk_details m USING(chunk_id) WHERE d.owner=? AND d.status='ready' "
                f"AND c.chunk_id IN ({placeholders})",
                [owner, *ids],
            )
        ]

    def _expand(self, chunk):
        radius = self.settings.context_neighbors
        if not radius or chunk.get("ordinal") is None:
            return {**chunk, "context_chunk_ids": [chunk["chunk_id"]]}
        nearby = [
            dict(r)
            for r in self.db.execute(
                "SELECT c.*,m.ordinal,m.section FROM chunks c JOIN chunk_details m USING(chunk_id) "
                "WHERE c.document_id=? AND c.page=? AND m.ordinal BETWEEN ? AND ? ORDER BY m.ordinal",
                (
                    chunk["document_id"],
                    chunk["page"],
                    chunk["ordinal"] - radius,
                    chunk["ordinal"] + radius,
                ),
            )
        ]
        selected = [r for r in nearby if r["chunk_id"] == chunk["chunk_id"]]
        used = len(chunk["text"])
        for row in sorted(nearby, key=lambda r: abs(r["ordinal"] - chunk["ordinal"])):
            if row["chunk_id"] == chunk["chunk_id"] or row["section"] != chunk.get("section", ""):
                continue
            if used + len(row["text"]) + 2 <= self.settings.context_chars:
                selected.append(row)
                used += len(row["text"]) + 2
        selected.sort(key=lambda r: r["ordinal"])
        text = ""
        for row in selected:
            value = row["text"]
            overlap = next(
                (n for n in range(min(len(text), len(value)), 15, -1) if text.endswith(value[:n])),
                0,
            )
            text = text + value[overlap:] if overlap else (text + "\n\n" + value if text else value)
        return {**chunk, "text": text, "context_chunk_ids": [r["chunk_id"] for r in selected]}

    def _select(self, ranked, top_k):
        selected, covered = [], set()
        for chunk, score in ranked:
            if not math.isfinite(float(score)):
                continue
            if (
                self.settings.rerank_min_score is not None
                and score < self.settings.rerank_min_score
            ):
                continue
            if chunk["chunk_id"] in covered:
                continue
            expanded = self._expand(chunk)
            # Only deduplicate within the same paper and page. Two papers making
            # the same claim are still two independent, useful sources.
            normalized = " ".join(expanded["text"].split())
            duplicate = False
            for prior in selected:
                if (prior["document_id"], prior["page"]) != (chunk["document_id"], chunk["page"]):
                    continue
                previous = " ".join(prior["text"].split())
                a = {normalized[i : i + 16] for i in range(max(0, len(normalized) - 15))}
                b = {previous[i : i + 16] for i in range(max(0, len(previous) - 15))}
                if normalized == previous or (a and b and len(a & b) / min(len(a), len(b)) >= 0.85):
                    duplicate = True
                    break
            if duplicate:
                continue
            covered.update(expanded["context_chunk_ids"])
            selected.append({**expanded, "rerank_score": float(score)})
            if len(selected) >= top_k:
                break
        return selected

    def retrieve(self, owner, question, top_k=5, document_ids=None):
        return self.retrieve_many(owner, [question], top_k, document_ids)[0]

    @active_operation
    def retrieve_many(self, owner, questions, top_k=5, document_ids=None):
        """Batch query encoding, persistent lexical lookup, bounded candidate reads."""
        if not 1 <= top_k <= 30 or not 1 <= len(questions) <= 32:
            raise ValueError("检索数量超出允许范围")
        with self.lock:
            if document_ids is not None:
                for did in document_ids:
                    self.document(owner, did)
                ready_ids = list(dict.fromkeys(document_ids))
            else:
                ready_ids = [
                    r[0]
                    for r in self.db.execute(
                        "SELECT document_id FROM documents WHERE owner=? AND status='ready'",
                        (owner,),
                    )
                ]
        if not ready_ids:
            return [[] for _ in questions]
        query_vectors = self._query_vectors(questions)
        conditions = [
            models.FieldCondition(key="owner", match=models.MatchValue(value=owner)),
            models.FieldCondition(key="document_id", match=models.MatchAny(any=ready_ids)),
        ]
        result = []
        for question, vector in zip(questions, query_vectors, strict=True):
            with self.lock:
                hits = self.vectors.query_points(
                    self.collection,
                    query=vector,
                    query_filter=models.Filter(must=conditions),
                    limit=40,
                ).points
                by_id = {
                    c["chunk_id"]: c for c in self._rows_by_ids(owner, [str(h.id) for h in hits])
                }
                semantic = [by_id[str(h.id)] for h in hits if str(h.id) in by_id]
                keyword = lexical.search(self.db, owner, question, document_ids, 40)
                candidates = rrf(semantic, keyword, limit=40)
            if not candidates:
                result.append([])
                continue
            scores = self.models.score(question, [c["text"] for c in candidates])
            ranked = sorted(
                zip(candidates, scores, strict=True), key=lambda pair: pair[1], reverse=True
            )
            with self.lock:
                # A paper may have been deleted while model inference was running.
                live = {
                    c["chunk_id"]
                    for c in self._rows_by_ids(owner, [c["chunk_id"] for c in candidates])
                }
                result.append(
                    self._select([(c, s) for c, s in ranked if c["chunk_id"] in live], top_k)
                )
        # A later query in the batch may have overlapped a deletion after an
        # earlier query finished. Revalidate the complete batch before returning.
        with self.lock:
            ids = [c["chunk_id"] for matches in result for c in matches]
            live = {c["chunk_id"] for c in self._rows_by_ids(owner, ids)}
            return [[c for c in matches if c["chunk_id"] in live] for matches in result]

    def close(self):
        with self.lifecycle:
            if self.closed:
                return
            self.closed = True
            self.lifecycle.wait_for(lambda: self.active == 0)
        with self.lock:
            self.vectors.close()
            self.db.close()
