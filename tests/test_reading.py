"""Persistent reading progress, upgrade compatibility and authenticated access."""

import sqlite3

import pymupdf
import pytest
from fastapi.testclient import TestClient
from helpers import FakeModels, pdf

from backend.core import KnowledgeBase, Settings
from backend.main import create_app


def test_page_progress_resume_backtracking_and_explicit_completion(kb):
    doc = kb.ingest("alice", "paper.pdf", pdf())
    did = doc["document_id"]
    assert doc["status"] == "ready" and doc["reading"]["status"] == "unread"
    assert doc["reading"]["last_page"] == 0
    first = kb.update_reading("alice", did, page=1)
    assert first["status"] == "reading" and first["progress_percent"] == 50
    last = kb.update_reading("alice", did, page=2)
    assert last["status"] == "reading" and last["progress_percent"] == 100
    assert last["completed_at"] is None  # Viewing the last page does not imply completion.
    back = kb.update_reading("alice", did, page=1)
    assert back["last_page"] == 1 and back["furthest_page"] == 2
    done = kb.update_reading("alice", did, status="completed")
    assert done["status"] == "completed" and done["completed_at"]
    revisit = kb.update_reading("alice", did, page=1)
    assert revisit["status"] == "completed" and revisit["completed_at"] == done["completed_at"]
    assert revisit["last_page"] == 1 and revisit["furthest_page"] == 2
    assert kb.documents("alice")[0]["reading"] == revisit
    reread = kb.update_reading("alice", did, status="reading")
    assert reread["completed_at"] is None and reread["furthest_page"] == 2
    reset = kb.update_reading("alice", did, status="unread")
    assert reset["last_page"] == reset["furthest_page"] == reset["progress_percent"] == 0
    assert reset["status"] == "unread" and reset["completed_at"] is None
    assert kb.document("alice", did)["status"] == "ready"


def test_start_status_does_not_invent_page_visit(kb):
    did = kb.ingest("alice", "paper.pdf", pdf())["document_id"]
    reading = kb.update_reading("alice", did, status="reading")
    assert reading["last_page"] == reading["furthest_page"] == 0
    assert reading["progress_percent"] == 0 and reading["updated_at"]


def test_duplicate_upload_preserves_reading_and_owner_isolation(kb):
    content = pdf()
    doc = kb.ingest("alice", "paper.pdf", content)
    reading = kb.update_reading("alice", doc["document_id"], page=2)
    duplicate = kb.ingest("alice", "renamed.pdf", content)
    assert duplicate["duplicate"] and duplicate["reading"] == reading
    other = kb.ingest("bob", "paper.pdf", content)
    assert other["reading"]["status"] == "unread"
    with pytest.raises(KeyError):
        kb.update_reading("bob", doc["document_id"], status="completed")
    assert kb.document("alice", doc["document_id"])["reading"] == reading


def test_upgrade_existing_database_then_restart_preserves_progress(tmp_path):
    kb = KnowledgeBase(Settings(tmp_path), FakeModels())
    did = kb.ingest("alice", "existing.pdf", pdf())["document_id"]
    kb.close()
    # Reproduce the prior version's schema: document data exists, no reading table.
    with sqlite3.connect(tmp_path / "metadata.sqlite3") as db:
        db.execute("DROP TABLE reading_progress")
    kb = KnowledgeBase(Settings(tmp_path), FakeModels())
    try:
        assert kb.document("alice", did)["reading"]["status"] == "unread"
        saved = kb.update_reading("alice", did, page=2)
    finally:
        kb.close()
    kb = KnowledgeBase(Settings(tmp_path), FakeModels())
    try:
        assert kb.document("alice", did)["reading"] == saved
        assert kb.retrieve("alice", "UV")  # Upgrade preserves the existing search index.
    finally:
        kb.close()


def test_delete_cascades_progress_and_prevents_stale_save(kb):
    did = kb.ingest("alice", "paper.pdf", pdf())["document_id"]
    kb.update_reading("alice", did, page=1)
    kb.delete("alice", did)
    assert kb.db.execute("SELECT COUNT(*) FROM reading_progress").fetchone()[0] == 0
    with pytest.raises(KeyError):
        kb.update_reading("alice", did, page=2)
    reuploaded = kb.ingest("alice", "paper.pdf", pdf())
    assert reuploaded["document_id"] != did and reuploaded["reading"]["status"] == "unread"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"page": 0},
        {"page": -1},
        {"page": 3},
        {"page": 1.5},
        {"page": True},
        {"page": "1"},
        {"status": "done"},
        {"page": 1, "status": "reading"},
        {"status": None},
    ],
)
def test_api_rejects_invalid_updates_without_changing_progress(kb, body):
    did = kb.ingest("alice", "paper.pdf", pdf())["document_id"]
    headers = {"X-API-Key": "reading-test-token"}
    with TestClient(create_app(kb=kb, api_tokens={"reading-test-token": "alice"})) as client:
        response = client.patch(f"/api/documents/{did}/reading", headers=headers, json=body)
        assert response.status_code == 422
        assert kb.document("alice", did)["reading"]["status"] == "unread"


def test_api_auth_read_write_documents_and_cors(kb):
    did = kb.ingest("alice", "paper.pdf", pdf())["document_id"]
    path = f"/api/documents/{did}/reading"
    alice = {"X-API-Key": "alice-reading-token"}
    bob = {"X-API-Key": "bob-reading-token"}
    with TestClient(
        create_app(kb=kb, api_tokens={"alice-reading-token": "alice", "bob-reading-token": "bob"})
    ) as client:
        assert client.get(path).status_code == 401
        assert client.patch(path, json={"page": 1}).status_code == 401
        assert client.get(path, headers=bob).status_code == 404
        assert client.patch(path, headers=bob, json={"page": 1}).status_code == 404
        update = client.patch(path, headers=alice, json={"page": 2})
        assert update.status_code == 200 and update.json()["last_page"] == 2
        assert client.get(path, headers=alice).json() == update.json()
        listed = client.get("/api/documents", headers=alice).json()["documents"][0]
        assert listed["reading"] == update.json() and listed["status"] == "ready"
        preflight = client.options(
            path,
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "X-API-Key,Content-Type",
            },
        )
        assert preflight.status_code == 200
        assert "PATCH" in preflight.headers["access-control-allow-methods"]


def test_single_page_can_be_100_percent_without_being_completed(kb):
    with pymupdf.open() as source:
        source.new_page().insert_text((50, 50), "One-page paper.")
        did = kb.ingest("alice", "one.pdf", source.tobytes())["document_id"]
    progress = kb.update_reading("alice", did, page=1)
    assert progress["progress_percent"] == 100 and progress["status"] == "reading"
