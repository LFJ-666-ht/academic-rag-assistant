"""Owner-scoped reading notes, conversation history and versioned terminology.

Locks touching both stores always acquire kb.lock before self.lock. Remote model calls
never run while either lock is held. Existing knowledge-base tables are untouched.
"""

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from uuid import uuid4


class ResearchConflict(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def clean_tags(tags):
    return list(dict.fromkeys(t.strip() for t in tags if t.strip()))[:10]


class ResearchStore:
    def __init__(self, kb):
        self.kb = kb
        self.lock = threading.RLock()
        self.db = sqlite3.connect(
            kb.settings.data_dir / "research.sqlite3", check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS notes(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, document_id TEXT NOT NULL,
                page INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
                quote TEXT NOT NULL, tags TEXT NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS notes_owner ON notes(owner, document_id);
            CREATE TABLE IF NOT EXISTS conversations(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                document_ids TEXT NOT NULL, filenames TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(owner, updated_at);
            CREATE TABLE IF NOT EXISTS turns(
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id)
                ON DELETE CASCADE, question TEXT NOT NULL, top_k INTEGER NOT NULL,
                status TEXT NOT NULL, answer TEXT, preview TEXT NOT NULL DEFAULT '',
                error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS turns_conversation ON turns(conversation_id, created_at);
            CREATE TABLE IF NOT EXISTS glossaries(
                owner TEXT NOT NULL, language TEXT NOT NULL, entries TEXT NOT NULL,
                revision INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(owner, language));
        """)
        with self.db:
            self.db.execute(
                "UPDATE turns SET status='interrupted', error=?, updated_at=? WHERE status='running'",
                ("上次后端停止时回答尚未完成；保留草稿，未自动重新调用模型", now()),
            )

    def _owned(self, table, owner, item_id):
        # table is only supplied by this module, never from a request.
        row = self.db.execute(
            f"SELECT * FROM {table} WHERE id=? AND owner=?", (item_id, owner)
        ).fetchone()
        if row is None:
            raise KeyError(item_id)
        return row

    def _note(self, row, filename):
        return {
            **{k: row[k] for k in row.keys() if k != "owner"},
            "tags": json.loads(row["tags"]),
            "filename": filename,
        }

    def notes(self, owner, document_id=None):
        with self.kb.lock, self.lock:
            if document_id:
                self.kb.document(owner, document_id)
            papers = {p["document_id"]: p["filename"] for p in self.kb.documents(owner)}
            rows = self.db.execute(
                "SELECT * FROM notes WHERE owner=? ORDER BY updated_at DESC,id", (owner,)
            )
            return [
                self._note(r, papers[r["document_id"]])
                for r in rows
                if r["document_id"] in papers
                and (not document_id or r["document_id"] == document_id)
            ]

    def save_note(self, owner, note_id, document_id, page, kind, text, quote, tags, revision):
        text, quote = text.strip(), quote.strip()
        if kind not in {"note", "bookmark"} or (kind == "note" and not text):
            raise ValueError("笔记内容不能为空")
        if type(page) is not int or page < 1:
            raise ValueError("页码无效")
        if (
            len(text) > 10000
            or len(quote) > 6000
            or len(tags) > 10
            or any(len(t) > 30 for t in tags)
        ):
            raise ValueError("笔记或标签过长")
        tags = clean_tags(tags)
        with self.kb.lock, self.lock:
            doc = self.kb.document(owner, document_id)
            if page > doc["pages"]:
                raise ValueError("页码超出论文范围")
            old = self.db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if old and old["owner"] != owner:
                raise KeyError(note_id)
            fields = (document_id, page, kind, text, quote, packed(tags))
            if old:
                previous = tuple(
                    old[k] for k in ("document_id", "page", "kind", "text", "quote", "tags")
                )
                if old["revision"] != revision:
                    # A retry after a lost acknowledgement is safe if it is identical.
                    if previous == fields and old["revision"] == revision + 1:
                        return self._note(old, doc["filename"])
                    raise ResearchConflict("笔记已被其他窗口修改，请重新载入后编辑")
            elif revision != 0:
                raise KeyError(note_id)
            stamp = now()
            with self.db:
                if old:
                    self.db.execute(
                        "UPDATE notes SET document_id=?,page=?,kind=?,text=?,quote=?,tags=?,revision=revision+1,updated_at=? WHERE id=?",
                        (*fields, stamp, note_id),
                    )
                else:
                    self.db.execute(
                        "INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,1,?,?)",
                        (note_id, owner, *fields, stamp, stamp),
                    )
            return self._note(self._owned("notes", owner, note_id), doc["filename"])

    def delete_note(self, owner, note_id):
        with self.lock, self.db:
            self._owned("notes", owner, note_id)
            self.db.execute("DELETE FROM notes WHERE id=?", (note_id,))

    def export_notes(self, owner, document_id=None):
        lines = ["研迹 PaperTrail · 阅读笔记", "页码均为 PDF 物理页码。摘录及笔记由用户记录。", ""]
        for n in self.notes(owner, document_id):
            lines.extend(
                [
                    f"{n['filename']} · 第 {n['page']} 页 · {'书签' if n['kind'] == 'bookmark' else '笔记'}",
                    f"标签：{'、'.join(n['tags'])}",
                    f"摘录：{n['quote']}",
                    f"笔记：{n['text']}",
                    f"更新时间：{n['updated_at']}",
                    "",
                ]
            )
        return "\n".join(lines).encode("utf-8-sig")

    def create_conversation(self, owner, document_ids, title="新问答", conversation_id=None):
        with self.kb.lock, self.lock:
            if document_ids is None:
                documents = self.kb.documents(owner)
                if len(documents) > 100:
                    raise ValueError("知识库超过 100 篇，请指定本次会话的论文范围")
            else:
                documents = [self.kb.document(owner, d) for d in dict.fromkeys(document_ids)]
            if not documents:
                raise ValueError("先上传或选择论文，再开始会话")
            ids = [d["document_id"] for d in documents]
            filenames = [d["filename"] for d in documents]
            cid, stamp = conversation_id or str(uuid4()), now()
            existing = self.db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
            if existing:
                if existing["owner"] != owner:
                    raise KeyError(cid)
                if json.loads(existing["document_ids"]) != ids:
                    raise ResearchConflict("该会话编号已用于另一组论文")
                return self._conversation(existing)
            with self.db:
                self.db.execute(
                    "INSERT INTO conversations VALUES(?,?,?,?,?,?,?)",
                    (
                        cid,
                        owner,
                        title.strip()[:120] or "新问答",
                        packed(ids),
                        packed(filenames),
                        stamp,
                        stamp,
                    ),
                )
            return self._conversation(self._owned("conversations", owner, cid))

    def _conversation(self, row):
        return {
            **{k: row[k] for k in row.keys() if k not in {"owner", "document_ids", "filenames"}},
            "document_ids": json.loads(row["document_ids"]),
            "filenames": json.loads(row["filenames"]),
        }

    def conversations(self, owner):
        with self.lock:
            return [
                self._conversation(r)
                for r in self.db.execute(
                    "SELECT * FROM conversations WHERE owner=? ORDER BY updated_at DESC,id",
                    (owner,),
                )
            ]

    def conversation(self, owner, cid):
        with self.kb.lock, self.lock:
            result = self._conversation(self._owned("conversations", owner, cid))
            available = {d["document_id"] for d in self.kb.documents(owner)}
            result["missing_document_ids"] = [
                d for d in result["document_ids"] if d not in available
            ]
            result["turns"] = [
                self._turn(r)
                for r in self.db.execute(
                    "SELECT * FROM turns WHERE conversation_id=? ORDER BY created_at,id", (cid,)
                )
            ]
            return result

    def _turn(self, row):
        result = dict(row)
        result["answer"] = json.loads(row["answer"]) if row["answer"] else None
        return result

    def rename_conversation(self, owner, cid, title):
        with self.lock, self.db:
            self._owned("conversations", owner, cid)
            self.db.execute(
                "UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                (title.strip()[:120] or "新问答", now(), cid),
            )

    def delete_conversation(self, owner, cid):
        with self.lock, self.db:
            self._owned("conversations", owner, cid)
            if self.db.execute(
                "SELECT 1 FROM turns WHERE conversation_id=? AND status='running'", (cid,)
            ).fetchone():
                raise ResearchConflict("回答仍在生成，请结束后再删除会话")
            self.db.execute("DELETE FROM conversations WHERE id=?", (cid,))

    def start_turn(self, owner, cid, request_id, question, top_k):
        with self.kb.lock, self.lock:
            conv = self._owned("conversations", owner, cid)
            ids = json.loads(conv["document_ids"])
            for doc_id in ids:
                self.kb.document(owner, doc_id)
            old = self.db.execute("SELECT * FROM turns WHERE id=?", (request_id,)).fetchone()
            if old:
                if old["conversation_id"] != cid:
                    raise ResearchConflict("请求编号已使用")
                if old["question"] != question or old["top_k"] != top_k:
                    raise ResearchConflict("该请求编号已用于其他问题")
                return self._turn(old), ids, []
            if self.db.execute(
                "SELECT 1 FROM turns t JOIN conversations c ON c.id=t.conversation_id WHERE c.owner=? AND t.status='running'",
                (owner,),
            ).fetchone():
                raise ResearchConflict("你有回答仍在生成，请先读取该会话的最新状态")
            history = []
            for row in reversed(
                self.db.execute(
                    "SELECT question,answer FROM turns WHERE conversation_id=? AND status='completed' ORDER BY created_at DESC,id DESC LIMIT 4",
                    (cid,),
                ).fetchall()
            ):
                answer = json.loads(row["answer"])
                if answer.get("incomplete") or answer.get("citation_status") != "ids_valid":
                    continue
                # Historical citation numbers cannot become this turn's evidence IDs.
                history.append(
                    {
                        "question": row["question"][:1000],
                        "answer": re.sub(r"\[\d+\]", "", answer["answer"])[:2000],
                    }
                )
            stamp = now()
            with self.db:
                self.db.execute(
                    "INSERT INTO turns(id,conversation_id,question,top_k,status,created_at,updated_at) VALUES(?,?,?,?,'running',?,?)",
                    (request_id, cid, question, top_k, stamp, stamp),
                )
                self.db.execute(
                    "UPDATE conversations SET title=CASE WHEN title='新问答' THEN ? ELSE title END,updated_at=? WHERE id=?",
                    (question[:60], stamp, cid),
                )
            return None, ids, history

    def turn(self, owner, cid, request_id):
        with self.lock:
            self._owned("conversations", owner, cid)
            row = self.db.execute(
                "SELECT * FROM turns WHERE id=? AND conversation_id=?", (request_id, cid)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            return self._turn(row)

    def update_turn(self, request_id, *, preview=None, answer=None, error=None, status=None):
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM turns WHERE id=?", (request_id,)).fetchone()
            if row is None or row["status"] != "running":
                return
            stamp = now()
            if answer is not None:
                self.db.execute(
                    "UPDATE turns SET answer=?,preview='',status='completed',updated_at=? WHERE id=?",
                    (packed(answer), stamp, request_id),
                )
            elif error is not None:
                self.db.execute(
                    "UPDATE turns SET error=?,status=?,updated_at=? WHERE id=?",
                    (error, status or "failed", stamp, request_id),
                )
            elif preview is not None:
                self.db.execute(
                    "UPDATE turns SET preview=?,updated_at=? WHERE id=?",
                    (preview, stamp, request_id),
                )
            self.db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (stamp, row["conversation_id"])
            )

    def glossary(self, owner, language):
        if language not in {"zh", "en"}:
            raise ValueError("目标语言无效")
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM glossaries WHERE owner=? AND language=?", (owner, language)
            ).fetchone()
            entries = json.loads(row["entries"]) if row else []
            return {
                "language": language,
                "entries": entries,
                "revision": row["revision"] if row else 0,
                "version": hashlib.sha256(packed(entries).encode()).hexdigest(),
                "updated_at": row["updated_at"] if row else None,
            }

    def save_glossary(self, owner, language, entries, revision):
        cleaned = sorted(
            [{"source": e["source"].strip(), "target": e["target"].strip()} for e in entries],
            key=lambda e: e["source"].casefold(),
        )
        if len(cleaned) > 100 or any(
            not e["source"] or not e["target"] or len(e["source"]) > 120 or len(e["target"]) > 160
            for e in cleaned
        ):
            raise ValueError("最多 100 个术语，原词和译法不能为空或过长")
        if len({e["source"].casefold() for e in cleaned}) != len(cleaned):
            raise ValueError("原词重复，请合并相同术语")
        with self.lock, self.db:
            current = self.glossary(owner, language)
            if revision != current["revision"]:
                if current["entries"] == cleaned:
                    return current
                raise ResearchConflict("术语表已被修改，请重新载入后编辑")
            if cleaned == current["entries"]:
                return current
            self.db.execute(
                "INSERT INTO glossaries VALUES(?,?,?,?,?) ON CONFLICT(owner,language) DO UPDATE SET entries=excluded.entries,revision=excluded.revision,updated_at=excluded.updated_at",
                (owner, language, packed(cleaned), revision + 1, now()),
            )
            return self.glossary(owner, language)

    def purge_document(self, owner, document_id):
        with self.lock, self.db:
            self.db.execute(
                "DELETE FROM notes WHERE owner=? AND document_id=?", (owner, document_id)
            )

    def close(self):
        with self.lock:
            self.db.close()
