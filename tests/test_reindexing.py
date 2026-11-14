"""Offline migration keeps the old KB usable and preserves all research records."""

import hashlib
import sqlite3
import uuid
from dataclasses import replace

import pytest
from helpers import FakeModels, pdf

from backend.core import KnowledgeBase, Settings
from backend.generation import answer_question
from backend.reindexing import rebuild
from backend.research_store import ResearchStore


def create_source(path):
    settings = Settings(path)
    kb = KnowledgeBase(settings, FakeModels())
    a = kb.ingest("alice", "a.pdf", pdf())
    b = kb.ingest("bob", "b.pdf", pdf("Another tenant's original evidence."))
    kb.update_reading("alice", a["document_id"], page=2)
    kb.close()
    # Research/translation/image/task stores are copied as opaque snapshots;
    # in particular quotes are not regenerated from new chunk IDs.
    for name in ("research", "translations", "images", "tasks"):
        with sqlite3.connect(path / (name + ".sqlite3")) as db:
            db.execute("CREATE TABLE saved(document_id TEXT,quote TEXT)")
            db.execute(
                "INSERT INTO saved VALUES(?,?)", (a["document_id"], "original saved quote [1]")
            )
    return settings, a["document_id"], b["document_id"]


def test_new_model_and_chunks_preserve_ids_progress_and_snapshots(tmp_path):
    source, output = tmp_path / "old", tmp_path / "new"
    settings, a, b = create_source(source)

    class NewModels(FakeModels):
        def encode(self, texts, query=False):
            return [v + [1.0, 2.0] for v in super().encode(texts, query)]

    changed = replace(settings, embedding_model="new-multilingual", chunk_size=60, chunk_overlap=10)
    report = rebuild(changed, output, NewModels())
    assert len(report["documents"]) == 2
    old = KnowledgeBase(settings, FakeModels())
    new = KnowledgeBase(replace(changed, data_dir=output), NewModels())
    try:
        assert old.document("alice", a)["reading"] == new.document("alice", a)["reading"]
        assert old.document("alice", a)["sha256"] == new.document("alice", a)["sha256"]
        assert new.retrieve("alice", "detector", document_ids=[a])
        assert {r["document_id"] for r in new.retrieve("bob", "evidence")} == {b}
        assert old.vectors.get_collection(old.collection).config.params.vectors.size == 8
        assert new.vectors.get_collection(new.collection).config.params.vectors.size == 10
        assert old.retrieve("alice", "detector")  # rollback directory remains fully usable
    finally:
        old.close()
        new.close()
    for name in ("research", "translations", "images", "tasks"):
        with sqlite3.connect(output / (name + ".sqlite3")) as db:
            assert db.execute("SELECT * FROM saved").fetchone() == (a, "original saved quote [1]")


def test_failure_never_publishes_partial_directory_or_changes_source(tmp_path):
    source, output = tmp_path / "old", tmp_path / "new"
    settings, a, _ = create_source(source)
    before = hashlib.sha256((source / "metadata.sqlite3").read_bytes()).digest()

    class Broken(FakeModels):
        def encode(self, texts, query=False):
            raise RuntimeError("simulated encoder failure")

    with pytest.raises(RuntimeError, match="encoder"):
        rebuild(settings, output, Broken())
    assert not output.exists() and not list(tmp_path.glob(".new.building-*"))
    assert hashlib.sha256((source / "metadata.sqlite3").read_bytes()).digest() == before
    old = KnowledgeBase(settings, FakeModels())
    try:
        assert old.document("alice", a)["reading"]["last_page"] == 2
    finally:
        old.close()


def test_running_backend_existing_target_and_nested_target_are_rejected(tmp_path):
    settings, _, _ = create_source(tmp_path / "old")
    kb = KnowledgeBase(settings, FakeModels())
    try:
        with pytest.raises(RuntimeError, match="仍在使用"):
            rebuild(settings, tmp_path / "new", FakeModels())
    finally:
        kb.close()
    for output in (settings.data_dir, settings.data_dir / "new", tmp_path):
        with pytest.raises(ValueError, match="输出目录"):
            rebuild(settings, output, FakeModels())


def test_tampered_pdf_does_not_change_history_provenance(tmp_path):
    settings, a, _ = create_source(tmp_path / "old")
    (settings.data_dir / "pdfs" / (a + ".pdf")).write_bytes(
        pdf("Changed content on the same two pages.")
    )
    with pytest.raises(ValueError, match="校验值"):
        rebuild(settings, tmp_path / "new", FakeModels())
    assert not (tmp_path / "new").exists()


def test_real_saved_notes_and_conversation_quotes_survive_rechunking(tmp_path):
    settings = Settings(tmp_path / "old")
    kb = KnowledgeBase(settings, FakeModels())
    did = kb.ingest("alice", "a.pdf", pdf())["document_id"]
    store = ResearchStore(kb)
    note_id, request_id = str(uuid.uuid4()), str(uuid.uuid4())
    note = store.save_note(
        owner="alice",
        note_id=note_id,
        document_id=did,
        page=2,
        kind="note",
        text="阅读笔记",
        quote="原始摘录",
        tags=["方法"],
        revision=0,
    )
    conv = store.create_conversation("alice", [did])
    store.start_turn("alice", conv["id"], request_id, "detector", 5)
    answer = answer_question(kb, lambda q, evidence: "回答 [1]", "alice", "detector")
    store.update_turn(request_id, answer=answer)
    snapshot = store.conversation("alice", conv["id"])
    store.close()
    kb.close()
    target = tmp_path / "new"
    rebuild(replace(settings, chunk_size=50, chunk_overlap=8), target, FakeModels())
    kb = KnowledgeBase(replace(settings, data_dir=target), FakeModels())
    store = ResearchStore(kb)
    try:
        assert store.notes("alice")[0] == note
        restored = store.conversation("alice", conv["id"])
        assert restored == snapshot
        assert restored["missing_document_ids"] == []
        assert kb.retrieve("alice", "detector")
    finally:
        store.close()
        kb.close()
