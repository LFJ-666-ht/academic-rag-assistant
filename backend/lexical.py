"""Persistent, pre-segmented FTS5 index; all reads are scoped before LIMIT."""

import re

import jieba

VERSION = "jieba-unicode61-v1"


def tokens(text):
    return [t for t in jieba.lcut(text.lower()) if re.search(r"\w", t)]


def initialize(db):
    # FTS5 ships with standard CPython builds used by this project. Fail clearly
    # instead of silently falling back to an unbounded per-query full scan.
    try:
        db.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_terms USING fts5(
                chunk_id UNINDEXED, document_id UNINDEXED, owner UNINDEXED, terms,
                tokenize='unicode61');
            CREATE TRIGGER IF NOT EXISTS chunk_terms_delete AFTER DELETE ON chunks BEGIN
                DELETE FROM chunk_terms WHERE rowid=old.rowid;
            END;
        """)
    except Exception as exc:
        raise RuntimeError(
            "当前 Python SQLite 缺少 FTS5；请使用支持 FTS5 的 Python 3.12+ 环境"
        ) from exc
    version = db.execute("SELECT value FROM config WHERE key='lexical_version'").fetchone()
    if version and version[0] == VERSION:
        return
    with db:
        db.execute("DELETE FROM chunk_terms")
        cursor = db.execute(
            "SELECT c.rowid,c.chunk_id,c.document_id,d.owner,c.text FROM chunks c JOIN documents d USING(document_id)"
        )
        while batch := cursor.fetchmany(128):
            db.executemany(
                "INSERT INTO chunk_terms(rowid,chunk_id,document_id,owner,terms) VALUES(?,?,?,?,?)",
                [(r[0], r[1], r[2], r[3], " ".join(tokens(r[4]))) for r in batch],
            )
        db.execute("INSERT OR REPLACE INTO config VALUES('lexical_version',?)", (VERSION,))


def insert(db, chunk_id, document_id, owner, text):
    db.execute(
        "INSERT INTO chunk_terms(rowid,chunk_id,document_id,owner,terms) VALUES((SELECT rowid FROM chunks WHERE chunk_id=?),?,?,?,?)",
        (chunk_id, chunk_id, document_id, owner, " ".join(tokens(text))),
    )


def search(db, owner, question, document_ids, limit):
    terms = list(dict.fromkeys(tokens(question)))[:64]
    if not terms or document_ids == []:
        return []
    expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    scope, params = "", [expression, owner]
    if document_ids is not None:
        scope = " AND d.document_id IN (" + ",".join("?" for _ in document_ids) + ")"
        params.extend(document_ids)
    params.append(limit)
    return [
        dict(r)
        for r in db.execute(
            "SELECT c.*,d.filename,m.ordinal,m.section,bm25(chunk_terms) AS lexical_score "
            "FROM chunk_terms JOIN chunks c ON c.chunk_id=chunk_terms.chunk_id "
            "JOIN documents d ON d.document_id=c.document_id LEFT JOIN chunk_details m ON m.chunk_id=c.chunk_id "
            "WHERE chunk_terms MATCH ? AND d.owner=? AND d.status='ready'"
            + scope
            + " ORDER BY bm25(chunk_terms) LIMIT ?",
            params,
        )
    ]
