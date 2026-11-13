"""Bounded background work; reconnecting reads state and never starts paid retries.

Task reads use only the task lock so upload progress stays visible while the KB is
indexing. Work that accesses papers acquires kb.lock before the task/store locks.
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from backend.generation import GenerationError, sources_from_chunks, stream_question
from backend.research_store import ResearchConflict, now, packed

logger = logging.getLogger(__name__)
FIELDS = {
    "question": "研究问题",
    "method": "研究方法",
    "data": "数据与实验",
    "conclusion": "主要结论",
    "limitations": "局限与边界",
}
ACTIVE = {"queued", "running"}


def validate_card(raw, evidence):
    """Validate local IDs per paper; ID validity does not prove factual entailment."""
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("```"):
            text = "\n".join(text.splitlines()[1:-1])
        try:
            raw = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise GenerationError("阅读卡片不是有效 JSON；未自动重试") from exc
    if not isinstance(raw, dict) or set(raw) != set(FIELDS):
        raise GenerationError("阅读卡片字段不完整")
    allowed = {e["id"] for e in evidence}
    fields = {}
    for key, label in FIELDS.items():
        item = raw[key]
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("text"), str)
            or not isinstance(item.get("source_ids"), list)
        ):
            raise GenerationError("阅读卡片字段格式错误")
        text, ids = item["text"].strip(), item["source_ids"]
        if (
            len(text) > 4000
            or len(ids) > len(evidence)
            or any(type(i) is not int or i not in allowed for i in ids)
        ):
            raise GenerationError("阅读卡片包含无效引用")
        if bool(text) != bool(ids):
            raise GenerationError("阅读卡片描述必须关联本篇原文")
        fields[key] = {
            "label": label,
            "text": text or "未找到相关说明",
            "source_ids": list(dict.fromkeys(ids)),
            "missing": not text,
        }
    return fields


class ResearchTasks:
    def __init__(self, kb, store, generator):
        self.kb, self.store, self.generator = kb, store, generator
        self.lock = threading.RLock()
        self.closed = False
        self.db = sqlite3.connect(kb.settings.data_dir / "tasks.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                params TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT '{}', error TEXT, revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS tasks_owner ON tasks(owner, kind, created_at);
        """)
        with self.db:
            self.db.execute(
                "UPDATE tasks SET status='interrupted',error=?,updated_at=? WHERE status IN ('queued','running')",
                ("后端在任务结束前停止；已完成结果保留，未自动重试", now()),
            )
        self.analysis_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="paper-analysis"
        )
        self.upload_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-upload")
        self.chat_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="paper-chat")
        self.chat_active = 0

    def _row(self, owner, job_id):
        row = self.db.execute(
            "SELECT * FROM tasks WHERE owner=? AND id=?", (owner, job_id)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return row

    def _public(self, row):
        return {
            **{k: row[k] for k in row.keys() if k not in {"owner", "params", "result"}},
            "params": json.loads(row["params"]),
            "result": json.loads(row["result"]),
        }

    def get(self, owner, job_id):
        with self.lock:
            return self._public(self._row(owner, job_id))

    def list(self, owner, kind):
        with self.lock:
            return [
                self._public(r)
                for r in self.db.execute(
                    "SELECT * FROM tasks WHERE owner=? AND kind=? ORDER BY created_at DESC LIMIT 50",
                    (owner, kind),
                )
            ]

    def _create(self, owner, job_id, kind, params):
        # Caller holds self.lock. Request UUID provides idempotency across lost replies.
        old = self.db.execute("SELECT * FROM tasks WHERE id=?", (job_id,)).fetchone()
        if old:
            if old["owner"] != owner:
                raise KeyError(job_id)
            if old["kind"] != kind or json.loads(old["params"]) != params:
                raise ResearchConflict("请求编号已用于不同任务")
            return False
        if self.closed:
            raise ResearchConflict("后端正在关闭")
        if self.db.execute(
            "SELECT 1 FROM tasks WHERE owner=? AND kind=? AND status IN ('queued','running')",
            (owner, kind),
        ).fetchone():
            raise ResearchConflict("已有同类任务进行中，请先查看任务记录")
        if (
            self.db.execute(
                "SELECT COUNT(*) FROM tasks WHERE kind=? AND status IN ('queued','running')",
                (kind,),
            ).fetchone()[0]
            >= 4
        ):
            raise ResearchConflict("任务队列已满，请稍后再试")
        with self.db:
            self.db.execute(
                "INSERT INTO tasks(id,owner,kind,params,status,stage,created_at,updated_at) VALUES(?,?,?,?,'queued','等待处理',?,?)",
                (job_id, owner, kind, packed(params), now(), now()),
            )
        return True

    def _update(self, job_id, stage, *, status="running", result=None, error=None):
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] not in ACTIVE:
                return False
            self.db.execute(
                "UPDATE tasks SET stage=?,status=?,result=?,error=?,updated_at=? WHERE id=?",
                (
                    stage,
                    status,
                    packed(result) if result is not None else row["result"],
                    error,
                    now(),
                    job_id,
                ),
            )
            return True

    def submit_upload(self, owner, request_id, filename, data):
        params = {"filename": filename, "sha256": hashlib.sha256(data).hexdigest()}
        with self.lock:
            if self._create(owner, request_id, "upload", params):
                self.upload_executor.submit(self._upload, owner, request_id, filename, data)
            return self.get(owner, request_id)

    def _upload(self, owner, job_id, filename, data):
        labels = {
            "parsing": "解析 PDF 文本",
            "embedding": "计算文献向量",
            "indexing": "写入检索索引",
        }
        try:
            if not self._update(job_id, "等待知识库可用"):
                return

            def progress(stage, done, total):
                self._update(
                    job_id, labels[stage] + (f" · 已完成 {done}/{total} 个片段" if total else "")
                )

            with self.kb.lock:
                paper = self.kb.ingest(owner, filename, data, on_progress=progress)
                self._update(
                    job_id,
                    "已入库" if not paper["duplicate"] else "文献已存在，未重复入库",
                    status="succeeded",
                    result=paper,
                )
        except ValueError as exc:
            self._update(job_id, "入库失败", status="failed", error=str(exc))
        except Exception:
            logger.exception("Upload task failed")
            self._update(
                job_id,
                "入库失败",
                status="failed",
                error="解析或索引失败，请检查后端日志；未自动重试",
            )

    def submit_analysis(self, owner, request_id, document_ids):
        ids = list(dict.fromkeys(document_ids))
        if not 1 <= len(ids) <= 5:
            raise ValueError("选择 1 篇生成阅读卡片，或 2–5 篇生成对比表")
        with self.kb.lock, self.lock:
            papers = [self.kb.document(owner, d) for d in ids]
            params = {"document_ids": ids, "filenames": [p["filename"] for p in papers]}
            if self._create(owner, request_id, "analysis", params):
                self.analysis_executor.submit(self._analysis, owner, request_id, ids)
            return self.get(owner, request_id)

    def _analysis(self, owner, job_id, ids):
        cards = []
        try:
            for index, did in enumerate(ids):
                if not self._update(job_id, f"检索第 {index + 1}/{len(ids)} 篇论文"):
                    return
                # Retrieve each dimension within each paper, avoiding cross-paper starvation.
                chunks, seen = [], set()
                for label in FIELDS.values():
                    for chunk in self.kb.retrieve(owner, label, 3, [did]):
                        if chunk["chunk_id"] not in seen:
                            seen.add(chunk["chunk_id"])
                            chunks.append(chunk)
                evidence = sources_from_chunks(chunks)
                if not self._update(job_id, f"整理第 {index + 1}/{len(ids)} 篇阅读卡片"):
                    return
                if evidence:
                    method = getattr(self.generator, "research", None)
                    if not callable(method):
                        raise GenerationError("当前模型适配器不支持阅读卡片")
                    fields = validate_card(method(evidence, FIELDS), evidence)
                else:
                    fields = validate_card(
                        {k: {"text": "", "source_ids": []} for k in FIELDS}, evidence
                    )
                with self.kb.lock:
                    paper = self.kb.document(owner, did)
                    cards.append(
                        {
                            "document_id": did,
                            "filename": paper["filename"],
                            "fields": fields,
                            "evidence": evidence,
                            "edits": {},
                        }
                    )
                    if not self._update(
                        job_id, f"已完成 {index + 1}/{len(ids)} 篇", result={"cards": cards}
                    ):
                        return
            self._update(job_id, "已完成，请核对原文", status="succeeded", result={"cards": cards})
        except GenerationError as exc:
            self._update(job_id, "分析未完成", status="failed", error=str(exc))
        except Exception:
            logger.exception("Analysis task failed")
            self._update(
                job_id,
                "分析未完成",
                status="failed",
                error="检索或保存失败，已完成卡片保留；未自动重试",
            )

    def cancel(self, owner, job_id):
        with self.lock:
            row = self._row(owner, job_id)
            if row["kind"] != "analysis":
                raise ValueError("仅分析任务可停止；文献入库请等待完成")
            self._update(
                job_id, "已停止后续分析", status="cancelled", error="已发出的模型请求可能仍会计费"
            )
            return self.get(owner, job_id)

    def edit(self, owner, job_id, document_id, field, text, revision):
        if field not in FIELDS or (text is not None and len(text) > 6000):
            raise ValueError("字段或内容无效")
        with self.lock, self.db:
            job = self.get(owner, job_id)
            if job["kind"] != "analysis" or job["status"] in ACTIVE:
                raise ResearchConflict("请等分析结束后再编辑")
            if job["revision"] != revision:
                raise ResearchConflict("卡片已在其他窗口修改，请刷新")
            card = next(
                (c for c in job["result"].get("cards", []) if c["document_id"] == document_id), None
            )
            if card is None:
                raise KeyError(document_id)
            if text is None:
                card["edits"].pop(field, None)
            else:
                card["edits"][field] = text.strip()
            self.db.execute(
                "UPDATE tasks SET result=?,revision=revision+1,updated_at=? WHERE id=?",
                (packed(job["result"]), now(), job_id),
            )
            return self.get(owner, job_id)

    def export(self, owner, job_id):
        job = self.get(owner, job_id)
        if job["kind"] != "analysis" or job["status"] in ACTIVE:
            raise ResearchConflict("请等分析结束后导出")
        lines = [
            "研迹 PaperTrail · 阅读卡片与多篇对比",
            f"任务状态：{job['status']}",
            "基于检索片段，不能视为全文系统综述；引用编号校验不代表事实准确率。",
        ]
        for card in job["result"].get("cards", []):
            lines.extend(["", card["filename"]])
            for key, field in card["fields"].items():
                lines.extend([field["label"], "AI 整理：" + field["text"]])
                if key in card["edits"]:
                    lines.append("人工笔记（未自动验证）：" + card["edits"][key])
                for evidence in card["evidence"]:
                    if evidence["id"] in field["source_ids"]:
                        lines.append(
                            f"第 {evidence['page']} 页 [{evidence['id']}]：{evidence['quote']}"
                        )
        return "\n".join(lines).encode("utf-8-sig")

    def purge(self, owner, document_id):
        # Called under kb.lock, after authorized deletion.
        with self.lock, self.db:
            for row in self.db.execute("SELECT * FROM tasks WHERE owner=?", (owner,)).fetchall():
                params, result = json.loads(row["params"]), json.loads(row["result"])
                if (
                    document_id in params.get("document_ids", [])
                    or result.get("document_id") == document_id
                ):
                    self.db.execute("DELETE FROM tasks WHERE id=?", (row["id"],))

    def start_chat(self, owner, cid, request_id, question, top_k):
        # Acquire KB first because start_turn validates the fixed document scope.
        with self.kb.lock, self.lock:
            if self.closed or self.chat_active >= 4:
                raise ResearchConflict("问答队列繁忙，请稍后再试")
            old, ids, history = self.store.start_turn(owner, cid, request_id, question, top_k)
            if old is None:
                self.chat_active += 1
                self.chat_executor.submit(
                    self._chat, owner, request_id, question, top_k, ids, history
                )
        return request_id

    def _chat(self, owner, request_id, question, top_k, ids, history):
        preview, last_save, completed = "", 0.0, False
        try:
            for event in stream_question(
                self.kb, self.generator, owner, question, top_k, ids, history
            ):
                if event["type"] == "delta":
                    preview += event.get("text", "")
                    if time.monotonic() - last_save > 0.4:
                        self.store.update_turn(request_id, preview=preview)
                        last_save = time.monotonic()
                elif event["type"] == "done":
                    self.store.update_turn(request_id, answer=event["answer"])
                    completed = True
            if not completed:
                raise GenerationError("回答未完成")
        except Exception as exc:
            logger.exception("Conversation turn failed")
            self.store.update_turn(request_id, preview=preview)
            self.store.update_turn(
                request_id,
                error=str(exc)
                if isinstance(exc, GenerationError)
                else "回答未完成，请检查后端日志；草稿未经引用校验，未自动重试",
            )
        finally:
            with self.lock:
                self.chat_active -= 1

    def close(self):
        with self.lock:
            self.closed = True
        self.upload_executor.shutdown(wait=True)
        self.analysis_executor.shutdown(wait=True)
        self.chat_executor.shutdown(wait=True)
        self.db.close()
