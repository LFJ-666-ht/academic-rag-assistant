"""Regression tests with real SQLite/Qdrant storage and deterministic model doubles.

These tests verify persistence and evidence routing, not model answer accuracy.
"""

import json
import sqlite3
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import pdf

from backend.generation import GeneratedText, GenerationError
from backend.main import create_app
from backend.research_store import ResearchConflict, ResearchStore
from backend.research_tasks import FIELDS, ResearchTasks, validate_card
from backend.translations import TranslationConflict, TranslationService


def uid():
    return str(uuid4())


def wait_for(read, predicate=lambda item: item["status"] not in {"queued", "running"}):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        item = read()
        if predicate(item):
            return item
        time.sleep(0.01)
    pytest.fail("background task did not complete")


class Provider:
    model = "deterministic-test-model"

    def __init__(self):
        self.chats, self.cards, self.translations, self.contexts = [], [], [], []

    def stream(self, question, evidence):
        self.chats.append((question, evidence))
        yield {"type": "delta", "text": "测试回答 [1]"}
        yield {"type": "result", "text": GeneratedText("测试回答 [1]")}

    def stream_context(self, question, evidence, history):
        self.contexts.append(history)
        return self.stream(question, evidence)

    def research(self, evidence, fields):
        self.cards.append(evidence)
        return {k: {"text": "模型整理", "source_ids": [evidence[0]["id"]]} for k in fields}

    def translate(self, text, language, glossary=None):
        self.translations.append((text, language, glossary))
        return "译文：" + text


@pytest.fixture
def research(kb):
    store = ResearchStore(kb)
    yield store
    store.close()


@pytest.fixture
def tasks(kb, research):
    service = ResearchTasks(kb, research, Provider())
    yield service
    service.close()


def new_note(store, did, **changes):
    args = dict(
        owner="alice",
        note_id=uid(),
        document_id=did,
        page=1,
        kind="note",
        text="我的理解",
        quote="原文",
        tags=["方法", "方法"],
        revision=0,
    )
    args.update(changes)
    return store.save_note(**args)


def test_notes_owner_revision_retry_export_and_reopen(kb, research):
    doc = kb.ingest("alice", "notes.pdf", pdf())
    note = new_note(research, doc["document_id"])
    assert note["tags"] == ["方法"] and note["revision"] == 1
    assert research.notes("bob") == []
    with pytest.raises(KeyError):
        research.delete_note("bob", note["id"])
    duplicate = new_note(research, doc["document_id"], note_id=note["id"])
    assert duplicate["revision"] == 1
    with pytest.raises(ResearchConflict):
        new_note(research, doc["document_id"], note_id=note["id"], text="不同内容")
    changed = new_note(
        research, doc["document_id"], note_id=note["id"], text="更新内容", revision=1
    )
    assert changed["revision"] == 2
    assert "更新内容" in research.export_notes("alice").decode("utf-8-sig")
    reopened = ResearchStore(kb)
    try:
        assert reopened.notes("alice")[0]["text"] == "更新内容"
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "change",
    [{"page": 3}, {"kind": "unknown"}, {"text": "  "}, {"tags": ["x" * 31]}, {"page": True}],
)
def test_note_validation(kb, research, change):
    did = kb.ingest("alice", "a.pdf", pdf())["document_id"]
    with pytest.raises(ValueError):
        new_note(research, did, **change)


def test_bookmark_without_text_and_delete(kb, research):
    did = kb.ingest("alice", "a.pdf", pdf())["document_id"]
    note = new_note(research, did, kind="bookmark", text="", page=2)
    assert note["page"] == 2
    research.delete_note("alice", note["id"])
    assert research.notes("alice") == []


def test_glossary_versions_owner_and_conflicts(research):
    original = research.glossary("alice", "zh")
    saved = research.save_glossary("alice", "zh", [{"source": "UV", "target": "紫外"}], 0)
    assert saved["revision"] == 1 and saved["version"] != original["version"]
    assert research.glossary("bob", "zh")["entries"] == []
    assert research.save_glossary("alice", "zh", saved["entries"], 0) == saved
    with pytest.raises(ResearchConflict):
        research.save_glossary("alice", "zh", [], 0)
    restored = research.save_glossary("alice", "zh", [], 1)
    assert restored["version"] == original["version"] and restored["revision"] == 2


@pytest.mark.parametrize(
    "entries",
    [
        [{"source": "UV", "target": ""}],
        [{"source": "UV", "target": "紫外"}, {"source": "uv", "target": "光"}],
        [{"source": "x" * 121, "target": "y"}],
    ],
)
def test_glossary_validation(research, entries):
    with pytest.raises(ValueError):
        research.save_glossary("alice", "zh", entries, 0)


def test_conversation_scope_history_bounds_and_stale_sources(kb, research):
    did = kb.ingest("alice", "a.pdf", pdf())["document_id"]
    conv = research.create_conversation("alice", [did], conversation_id=uid())
    with pytest.raises(KeyError):
        research.conversation("bob", conv["id"])
    with pytest.raises(KeyError):
        research.create_conversation("bob", [did])
    for n in range(6):
        rid = uid()
        old, ids, history = research.start_turn("alice", conv["id"], rid, f"问题{n}", 6)
        assert old is None and ids == [did] and len(history) <= 4
        assert all("[1]" not in h["answer"] and len(h["answer"]) <= 2000 for h in history)
        research.update_turn(
            rid, answer={"answer": "答案 [1]" * 500, "citation_status": "ids_valid"}
        )
    kb.delete("alice", did)
    detail = research.conversation("alice", conv["id"])
    assert detail["missing_document_ids"] == [did] and len(detail["turns"]) == 6
    with pytest.raises(KeyError):
        research.start_turn("alice", conv["id"], uid(), "再问", 6)


def test_interrupted_turn_retains_draft_and_is_not_context(kb, research):
    did = kb.ingest("alice", "a.pdf", pdf())["document_id"]
    conv = research.create_conversation("alice", [did])
    rid = uid()
    research.start_turn("alice", conv["id"], rid, "question", 6)
    research.update_turn(rid, preview="部分文本 [999]")
    with pytest.raises(ResearchConflict):
        research.delete_conversation("alice", conv["id"])
    reopened = ResearchStore(kb)
    try:
        turn = reopened.conversation("alice", conv["id"])["turns"][0]
        assert turn["status"] == "interrupted" and turn["preview"] == "部分文本 [999]"
        _, _, history = reopened.start_turn("alice", conv["id"], uid(), "继续", 6)
        assert history == []
    finally:
        reopened.close()


def test_chat_saved_replay_and_continuation(kb, research, tasks):
    did = kb.ingest("alice", "a.pdf", pdf())["document_id"]
    cid = research.create_conversation("alice", [did])["id"]
    rid = uid()
    tasks.start_chat("alice", cid, rid, "研究方法？", 6)
    result = wait_for(lambda: research.turn("alice", cid, rid))
    assert result["status"] == "completed" and result["answer"]["citation_status"] == "ids_valid"
    tasks.start_chat("alice", cid, rid, "研究方法？", 6)
    assert len(tasks.generator.chats) == 1
    with pytest.raises(ResearchConflict):
        tasks.start_chat("alice", cid, rid, "different", 6)
    second = uid()
    tasks.start_chat("alice", cid, second, "它的局限？", 6)
    wait_for(lambda: research.turn("alice", cid, second))
    assert tasks.generator.contexts[0][0]["question"] == "研究方法？"
    assert "[1]" not in tasks.generator.contexts[0][0]["answer"]


def test_chat_failed_citation_preserves_unvalidated_draft(kb, research, tasks):
    class Invalid(Provider):
        def stream(self, question, evidence):
            yield {"type": "delta", "text": "不可靠草稿 [999]"}
            yield {"type": "result", "text": GeneratedText("不可靠草稿 [999]")}

    tasks.generator = Invalid()
    did = kb.ingest("alice", "a.pdf", pdf())["document_id"]
    cid = research.create_conversation("alice", [did])["id"]
    rid = uid()
    tasks.start_chat("alice", cid, rid, "a", 6)
    saved = wait_for(lambda: research.turn("alice", cid, rid))
    assert saved["status"] == "failed" and saved["answer"] is None
    assert "[999]" in saved["preview"]


def test_upload_real_stages_persistence_dedup_and_owner(kb, research, tasks):
    stages = []
    original = tasks._update

    def record(job_id, stage, **kwargs):
        stages.append(stage)
        return original(job_id, stage, **kwargs)

    tasks._update = record
    rid, data = uid(), pdf()
    tasks.submit_upload("alice", rid, "a.pdf", data)
    job = wait_for(lambda: tasks.get("alice", rid))
    assert job["status"] == "succeeded" and job["result"]["pages"] == 2
    assert (
        any("解析 PDF" in s for s in stages)
        and any("计算文献向量" in s for s in stages)
        and any("写入检索索引" in s for s in stages)
    )
    assert tasks.submit_upload("alice", rid, "a.pdf", data)["id"] == rid
    with pytest.raises(KeyError):
        tasks.get("bob", rid)
    assert tasks.list("bob", "upload") == []
    second = uid()
    tasks.submit_upload("alice", second, "renamed.pdf", data)
    assert wait_for(lambda: tasks.get("alice", second))["result"]["duplicate"]


def test_multi_paper_analysis_sources_edits_export_and_purge(kb, research, tasks):
    ids = [
        kb.ingest("alice", f"p{n}.pdf", pdf(f"Method {n} has specific data."))["document_id"]
        for n in range(2)
    ]
    rid = uid()
    tasks.submit_analysis("alice", rid, ids)
    job = wait_for(lambda: tasks.get("alice", rid))
    assert job["status"] == "succeeded", job
    assert len(job["result"]["cards"]) == 2
    assert [{e["document_id"] for e in call} for call in tasks.generator.cards] == [
        {ids[0]},
        {ids[1]},
    ]
    changed = tasks.edit("alice", rid, ids[0], "method", "人工修改", 0)
    card = changed["result"]["cards"][0]
    assert card["fields"]["method"]["text"] == "模型整理" and card["edits"]["method"] == "人工修改"
    with pytest.raises(ResearchConflict):
        tasks.edit("alice", rid, ids[0], "method", "覆盖", 0)
    assert "人工笔记（未自动验证）：人工修改" in tasks.export("alice", rid).decode("utf-8-sig")
    tasks.submit_analysis("alice", rid, ids)
    assert len(tasks.generator.cards) == 2
    with kb.lock:
        kb.delete("alice", ids[0])
        tasks.purge("alice", ids[0])
    assert tasks.list("alice", "analysis") == []


def test_cancel_analysis_keeps_no_partial_model_result(kb, research, tasks):
    started, release = threading.Event(), threading.Event()

    class Slow(Provider):
        def research(self, evidence, fields):
            started.set()
            assert release.wait(3)
            return super().research(evidence, fields)

    tasks.generator = Slow()
    ids = [kb.ingest("alice", f"p{n}.pdf", pdf(f"Method {n}"))["document_id"] for n in range(2)]
    rid = uid()
    try:
        tasks.submit_analysis("alice", rid, ids)
        assert started.wait(2)
        tasks.cancel("alice", rid)
    finally:
        release.set()
    tasks.analysis_executor.shutdown(wait=True)
    assert tasks.get("alice", rid)["status"] == "cancelled"
    assert tasks.get("alice", rid)["result"] == {}
    assert len(tasks.generator.cards) == 1


@pytest.mark.parametrize(
    "bad",
    [
        "bad json",
        {},
        {k: {"text": "unsupported", "source_ids": []} for k in FIELDS},
        {k: {"text": "invalid", "source_ids": [999]} for k in FIELDS},
        {k: {"text": "invalid", "source_ids": [True]} for k in FIELDS},
    ],
)
def test_reject_malformed_or_unsupported_cards(bad):
    with pytest.raises(GenerationError):
        validate_card(bad, [{"id": 1}])


def test_missing_fields_are_explicit():
    card = validate_card({k: {"text": "", "source_ids": []} for k in FIELDS}, [])
    assert all(f["missing"] and f["text"] == "未找到相关说明" for f in card.values())


def test_glossary_cache_snapshot_and_lost_reply(kb, research):
    provider = Provider()
    service = TranslationService(kb, provider, research)
    did = kb.ingest("alice", "a.pdf", pdf())["document_id"]

    def translate(version):
        job = service.submit("alice", uid(), did, 1, 1, "zh", version)
        return wait_for(lambda: service.get("alice", job["id"]))

    try:
        original = research.glossary("alice", "zh")
        first = translate(original["version"])
        assert first["completed_pages"] == 1 and len(provider.translations) == 1
        assert translate(original["version"])["reused_pages"] == 1
        terms = research.save_glossary("alice", "zh", [{"source": "UV", "target": "紫外"}], 0)
        with pytest.raises(TranslationConflict):
            translate(original["version"])
        second = translate(terms["version"])
        assert not second["reused_pages"] and provider.translations[-1][2] == terms["entries"]
        assert service.get("alice", first["id"])["glossary_changed"]
        # Retrying an accepted request uses its original snapshot, not today's terms.
        replay = service.submit("alice", first["request_id"], did, 1, 1, "zh", original["version"])
        assert replay["id"] == first["id"] and len(provider.translations) == 2
        research.save_glossary("alice", "zh", [], 1)
        assert translate(original["version"])["reused_pages"] == 1
    finally:
        service.close()


def test_old_translation_schema_migrates(kb):
    service = TranslationService(kb, Provider())
    did = kb.ingest("alice", "old.pdf", pdf())["document_id"]
    job = service.submit("alice", uid(), did, 1, 1, "zh")
    wait_for(lambda: service.get("alice", job["id"]))
    service.close()
    with sqlite3.connect(kb.settings.data_dir / "translations.sqlite3") as db:
        for name in ("glossary_json", "glossary_version", "glossary_revision"):
            db.execute(f"ALTER TABLE translation_jobs DROP COLUMN {name}")
    store = ResearchStore(kb)
    service = TranslationService(kb, Provider(), store)
    try:
        assert service.get("alice", job["id"])["completed_pages"] == 1
        assert service.get("alice", job["id"])["glossary_count"] == 0
        again = service.submit("alice", uid(), did, 1, 1, "zh")
        assert wait_for(lambda: service.get("alice", again["id"]))["reused_pages"] == 1
    finally:
        service.close()
        store.close()


def test_api_auth_conversation_stream_and_delete_cascade(kb):
    provider = Provider()
    app = create_app(
        kb=kb,
        generator=provider,
        translator=provider,
        api_tokens={"a" * 16: "alice", "b" * 16: "bob"},
    )
    a, b = {"X-API-Key": "a" * 16}, {"X-API-Key": "b" * 16}
    with TestClient(app) as client:
        assert client.get("/api/notes").status_code == 401
        did = client.post(
            "/api/documents", headers=a, files={"file": ("a.pdf", pdf(), "application/pdf")}
        ).json()["document_id"]
        note = {
            "id": uid(),
            "document_id": did,
            "page": 1,
            "kind": "note",
            "text": "note",
            "revision": 0,
        }
        assert client.post("/api/notes", headers=b, json=note).status_code == 404
        assert client.post("/api/notes", headers=a, json=note).status_code == 200
        cid = uid()
        assert (
            client.post(
                "/api/conversations", headers=a, json={"id": cid, "document_ids": [did]}
            ).status_code
            == 200
        )
        body = {"request_id": uid(), "question": "What is the result?", "top_k": 6}
        response = client.post(f"/api/conversations/{cid}/stream", headers=a, json=body)
        assert response.status_code == 200, response.text
        assert json.loads(response.text.strip().splitlines()[-1])["type"] == "done"
        assert client.get(f"/api/conversations/{cid}", headers=b).status_code == 404
        assert (
            client.post(f"/api/conversations/{cid}/stream", headers=a, json=body).status_code == 200
        )
        assert len(provider.chats) == 1
        assert client.delete(f"/api/documents/{did}", headers=a).status_code == 200
        assert client.get("/api/notes", headers=a).json() == {"notes": []}
        assert client.get(f"/api/conversations/{cid}", headers=a).json()[
            "missing_document_ids"
        ] == [did]
