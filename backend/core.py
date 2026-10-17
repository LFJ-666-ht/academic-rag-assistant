"""Persistent, tenant-scoped RAG; SQLite is the source of truth for chunk text."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

import jieba
import pymupdf
from qdrant_client import QdrantClient, models


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

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "data")).resolve(),
            embedding_model=os.getenv("EMBEDDING_MODEL", cls.embedding_model),
            reranker_model=os.getenv("RERANKER_MODEL", cls.reranker_model),
        )


class LocalModels:
    """Load only when needed; an empty knowledge base starts without downloading models."""
    def __init__(self, settings):
        self.settings = settings
        self.embedder = None
        self.reranker = None

    def encode(self, texts, query=False):
        from sentence_transformers import SentenceTransformer
        if self.embedder is None:
            self.embedder = SentenceTransformer(self.settings.embedding_model)
        if query:
            texts = ["为这个句子生成表示以用于检索相关文章：" + t for t in texts]
        return self.embedder.encode(
            texts, batch_size=32, normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

    def score(self, question, texts):
        from sentence_transformers import CrossEncoder
        if self.reranker is None:
            self.reranker = CrossEncoder(self.settings.reranker_model, max_length=512)
        # CrossEncoder.predict handles evaluation / inference mode internally.
        return self.reranker.predict(
            [(question, text) for text in texts], batch_size=16,
            show_progress_bar=False,
        ).reshape(-1).tolist()


def tokens(text):
    return [t for t in jieba.lcut(text.lower()) if re.search(r"\w", t)]


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


def parse_pdf(data, settings):
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ValueError("分块重叠必须小于分块大小")
    chunks, empty_pages = [], []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError("无法解析 PDF，请检查文件是否损坏") from exc
    with doc:
        if doc.needs_pass:
            raise ValueError("暂不支持加密 PDF，请先解密")
        if len(doc) > settings.max_pages:
            raise ValueError(f"PDF 不能超过 {settings.max_pages} 页")
        for number, page in enumerate(doc, 1):
            text = page.get_text("text", sort=True).strip()
            if not text:
                empty_pages.append(number)
                continue
            # Never cross a page boundary, so every citation has one unambiguous page.
            for start in range(0, len(text), settings.chunk_size - settings.chunk_overlap):
                excerpt = text[start:start + settings.chunk_size]
                if excerpt.strip():
                    chunks.append({"page": number, "text": excerpt})
                    if len(chunks) > settings.max_chunks:
                        raise ValueError("文档文本过多，请拆分 PDF 后上传")
                if start + settings.chunk_size >= len(text):
                    break
        if not chunks:
            raise ValueError("没有可提取文本；扫描版 PDF 请先进行 OCR")
        return chunks, len(doc), empty_pages


def locked(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapper


class KnowledgeBase:
    collection = "academic_chunks_v1"

    def __init__(self, settings, model_provider=None):
        self.settings = settings
        self.lock = threading.RLock()
        self.models = model_provider or LocalModels(settings)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir = settings.data_dir / "pdfs"
        self.pdf_dir.mkdir(exist_ok=True)
        self.db = sqlite3.connect(settings.data_dir / "metadata.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
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
        ''')
        stored = self.db.execute("SELECT value FROM config WHERE key='embedding'").fetchone()
        if stored and stored[0] != settings.embedding_model:
            self.db.close()
            raise ValueError("Embedding 模型与现有索引不一致；请使用新的 DATA_DIR 并重新上传")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO config VALUES ('embedding', ?)", (settings.embedding_model,))
        self.vectors = QdrantClient(path=str(settings.data_dir / "qdrant"))
        # Recover interrupted ingestion/deletion before serving requests.
        for row in self.db.execute("SELECT * FROM documents WHERE status!='ready'").fetchall():
            self._purge(row["document_id"])

    def _filter(self, document_id):
        return models.Filter(must=[models.FieldCondition(
            key="document_id", match=models.MatchValue(value=document_id))])

    def _purge(self, document_id):
        if self.vectors.collection_exists(self.collection):
            self.vectors.delete(self.collection, models.FilterSelector(filter=self._filter(document_id)), wait=True)
        (self.pdf_dir / f"{document_id}.pdf").unlink(missing_ok=True)
        with self.db:
            self.db.execute("DELETE FROM documents WHERE document_id=?", (document_id,))

    @locked
    def ingest(self, owner, filename, data):
        if len(data) > self.settings.max_upload_mb * 1024 * 1024:
            raise ValueError("PDF 超过上传大小限制")
        digest = hashlib.sha256(data).hexdigest()
        old = self.db.execute("SELECT * FROM documents WHERE owner=? AND sha256=?", (owner, digest)).fetchone()
        if old and old["status"] == "ready":
            return {**dict(old), "duplicate": True}
        if old:
            self._purge(old["document_id"])
        chunks, pages, empty_pages = parse_pdf(data, self.settings)
        document_id = str(uuid.uuid4())
        filename = filename.replace("\\", "/").split("/")[-1][:255] or "paper.pdf"
        with self.db:
            self.db.execute(
                "INSERT INTO documents(document_id,owner,sha256,filename,pages,chunk_count,empty_pages,status) VALUES(?,?,?,?,?,?,?,?)",
                (document_id, owner, digest, filename, pages, len(chunks), json.dumps(empty_pages), "indexing"),
            )
        try:
            (self.pdf_dir / f"{document_id}.pdf").write_bytes(data)
            for offset in range(0, len(chunks), 32):
                batch = chunks[offset:offset + 32]
                vectors = self.models.encode([c["text"] for c in batch])
                if not self.vectors.collection_exists(self.collection):
                    self.vectors.create_collection(self.collection, vectors_config=models.VectorParams(
                        size=len(vectors[0]), distance=models.Distance.COSINE))
                points = []
                with self.db:
                    for chunk, vector in zip(batch, vectors, strict=True):
                        cid = str(uuid.uuid5(uuid.UUID(document_id), str(offset + len(points))))
                        self.db.execute("INSERT INTO chunks VALUES(?,?,?,?)", (cid, document_id, chunk["page"], chunk["text"]))
                        points.append(models.PointStruct(id=cid, vector=vector, payload={"owner": owner, "document_id": document_id}))
                self.vectors.upsert(self.collection, points=points, wait=True)
            with self.db:
                self.db.execute("UPDATE documents SET status='ready' WHERE document_id=?", (document_id,))
        except Exception:
            self._purge(document_id)
            raise
        return {**self.document(owner, document_id), "duplicate": False}

    @locked
    def documents(self, owner):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM documents WHERE owner=? AND status='ready' ORDER BY created_at DESC", (owner,))]

    @locked
    def document(self, owner, document_id):
        row = self.db.execute("SELECT * FROM documents WHERE owner=? AND document_id=? AND status='ready'", (owner, document_id)).fetchone()
        if not row:
            raise KeyError("论文不存在")
        return dict(row)

    @locked
    def delete(self, owner, document_id):
        self.document(owner, document_id)
        with self.db:
            self.db.execute("UPDATE documents SET status='deleting' WHERE document_id=?", (document_id,))
        self._purge(document_id)

    @locked
    def retrieve(self, owner, question, top_k=5, document_ids=None):
        if document_ids is not None:
            for did in document_ids:
                self.document(owner, did)
        rows = self.db.execute('''SELECT c.*,d.filename FROM chunks c JOIN documents d
            ON c.document_id=d.document_id WHERE d.owner=? AND d.status='ready' ''', (owner,)).fetchall()
        chunks = [dict(r) for r in rows if document_ids is None or r["document_id"] in document_ids]
        if not chunks:
            return []
        by_id = {c["chunk_id"]: c for c in chunks}
        conditions = [models.FieldCondition(key="owner", match=models.MatchValue(value=owner))]
        if document_ids is not None:
            conditions.append(models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids)))
        hits = self.vectors.query_points(
            self.collection, query=self.models.encode([question], query=True)[0],
            query_filter=models.Filter(must=conditions), limit=20,
        ).points
        semantic = [by_id[str(h.id)] for h in hits if str(h.id) in by_id]
        candidates = rrf(semantic, bm25(question, chunks), limit=30)
        if not candidates:
            return []
        scores = self.models.score(question, [c["text"] for c in candidates])
        ranked = sorted(zip(candidates, scores, strict=True), key=lambda pair: pair[1], reverse=True)
        return [{**chunk, "rerank_score": float(score)} for chunk, score in ranked[:top_k]]

    @locked
    def close(self):
        self.vectors.close()
        self.db.close()
