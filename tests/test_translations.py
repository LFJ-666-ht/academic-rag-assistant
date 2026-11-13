"""Real PDF/storage/API translation tests; all model calls use deterministic doubles."""

import threading
import time
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient
from helpers import pdf

from backend.generation import GeneratedText, GenerationError
from backend.main import create_app
from backend.translations import (
    TranslationBusy,
    TranslationConflict,
    TranslationService,
    split_segments,
)


class Translator:
    model = "test-translator"

    def __init__(self):
        self.calls = []

    def translate(self, text, language):
        self.calls.append((text, language))
        return f"译文({language})：{text}"


def wait(service, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = service.get("alice", job_id)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    raise AssertionError("translation did not finish")


def submit(service, doc, **kwargs):
    return service.submit("alice", str(uuid4()), doc["document_id"], 1, 2, **kwargs)


def test_segments_preserve_every_character_without_overlap():
    text = "A sentence. First paragraph!\n第二段文字。包含 254 nm 与 [12] 引用。\n" * 130
    parts = split_segments(text)
    assert "".join(parts) == text
    assert len(parts) > 1 and all(0 < len(p) <= 1800 for p in parts)
    assert "".join(split_segments("x" * 3601)) == "x" * 3601


def test_full_page_source_and_persistence_cache_language_and_model(kb):
    translator = Translator()
    doc = kb.ingest("alice", "paper.pdf", pdf())
    service = TranslationService(kb, translator)
    try:
        first = wait(service, submit(service, doc, target_language="zh")["id"])
        assert first["status"] == "succeeded" and first["completed_pages"] == 2
        assert first["pages"][0]["original"] == "Solar blind UV detector has low dark current."
        assert first["pages"][1]["original"] == "Responsivity is measured at 254 nm."
        assert [c[0] for c in translator.calls] == [p["original"] for p in first["pages"]]
        assert "【原文】" in service.export("alice", first["id"]).decode("utf-8-sig")
        assert "第 2 页" in service.export("alice", first["id"]).decode("utf-8-sig")
    finally:
        service.close()
    service = TranslationService(kb, translator)
    try:
        assert service.get("alice", first["id"])["pages"] == first["pages"]
        reused = wait(service, submit(service, doc, target_language="zh")["id"])
        assert reused["reused_pages"] == 2 and len(translator.calls) == 2
        english = wait(service, submit(service, doc, target_language="en")["id"])
        assert english["reused_pages"] == 0 and len(translator.calls) == 4
    finally:
        service.close()
    translator.model = "different-model"
    service = TranslationService(kb, translator)
    try:
        changed = wait(service, submit(service, doc, target_language="zh")["id"])
        assert changed["reused_pages"] == 0 and len(translator.calls) == 6
    finally:
        service.close()


def test_failed_page_is_not_saved_and_manual_retry_reuses_completed_page(kb):
    class Failing(Translator):
        fail = True

        def translate(self, text, language):
            result = super().translate(text, language)
            if self.fail and "Responsivity" in text:
                raise GenerationError("模拟连接中断")
            return result

    provider = Failing()
    doc = kb.ingest("alice", "paper.pdf", pdf())
    service = TranslationService(kb, provider)
    try:
        job = wait(service, submit(service, doc, target_language="zh")["id"])
        assert job["status"] == "failed" and job["completed_pages"] == 1
        assert job["pages"][1]["translation"] == "" and job["pages"][1]["status"] == "pending"
        assert "[本页未完成]" in service.export("alice", job["id"]).decode("utf-8-sig")
        provider.fail = False
        retry = wait(service, submit(service, doc, target_language="zh")["id"])
        assert retry["status"] == "succeeded" and retry["reused_pages"] == 1
        assert len(provider.calls) == 3
    finally:
        service.close()


def test_partial_segment_not_committed_as_complete_page(kb):
    class Truncated(Translator):
        def translate(self, text, language):
            return GeneratedText("只有部分译文", "length")

    doc = kb.ingest("alice", "paper.pdf", pdf())
    service = TranslationService(kb, Truncated())
    try:
        job = wait(service, submit(service, doc, target_language="zh")["id"])
        assert job["status"] == "failed" and job["completed_pages"] == 0
        assert not job["pages"][0]["translation"]
        with pytest.raises(TranslationConflict):
            service.export("alice", job["id"])
    finally:
        service.close()


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (1, 3)])
def test_page_bounds_rejected_before_call(kb, start, end):
    doc = kb.ingest("alice", "paper.pdf", pdf())
    provider = Translator()
    service = TranslationService(kb, provider)
    try:
        with pytest.raises(ValueError, match="页码"):
            service.submit("alice", str(uuid4()), doc["document_id"], start, end, "zh")
        assert not provider.calls
    finally:
        service.close()


def test_empty_page_skip_and_empty_only_rejection(kb):
    with pymupdf.open() as source:
        source.new_page().insert_text((50, 50), "A paper with a blank scanned page.")
        source.new_page()
        doc = kb.ingest("alice", "mixed.pdf", source.tobytes())
    provider = Translator()
    service = TranslationService(kb, provider)
    try:
        with pytest.raises(ValueError, match="OCR"):
            service.submit("alice", str(uuid4()), doc["document_id"], 2, 2, "zh")
        job = wait(service, submit(service, doc, target_language="zh")["id"])
        assert job["status"] == "succeeded" and job["skipped_pages"] == 1
        assert job["pages"][1]["status"] == "skipped" and len(provider.calls) == 1
    finally:
        service.close()


class Blocking(Translator):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def translate(self, text, language):
        result = super().translate(text, language)
        self.started.set()
        assert self.release.wait(5)
        return result


def test_same_request_reused_conflict_busy_and_cancellation(kb):
    provider = Blocking()
    doc = kb.ingest("alice", "paper.pdf", pdf())
    service = TranslationService(kb, provider)
    try:
        request = str(uuid4())
        job = service.submit("alice", request, doc["document_id"], 1, 2, "zh")
        assert provider.started.wait(3)
        assert service.submit("alice", request, doc["document_id"], 1, 2, "zh")["id"] == job["id"]
        with pytest.raises(TranslationConflict):
            service.submit("alice", request, doc["document_id"], 1, 1, "zh")
        with pytest.raises(TranslationBusy):
            submit(service, doc, target_language="zh")
        cancelled = service.cancel("alice", job["id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["pages"][0]["status"] == "pending"
        provider.release.set()
        service.executor.shutdown(wait=True)
        assert len(provider.calls) == 1 and service.get("alice", job["id"])["completed_pages"] == 0
    finally:
        provider.release.set()
        service.close()


def test_deleted_during_translation_never_resurrects_data(kb):
    provider = Blocking()
    doc = kb.ingest("alice", "paper.pdf", pdf())
    service = TranslationService(kb, provider)
    try:
        job = submit(service, doc, target_language="zh")
        assert provider.started.wait(3)
        with kb.lock:
            kb.delete("alice", doc["document_id"])
            service.purge("alice", doc["document_id"])
        provider.release.set()
        service.executor.shutdown(wait=True)
        assert not service.list("alice")
        with pytest.raises(KeyError):
            service.get("alice", job["id"])
        assert service.db.execute("SELECT COUNT(*) FROM translation_cache").fetchone()[0] == 0
        assert service.db.execute("SELECT COUNT(*) FROM translation_parts").fetchone()[0] == 0
    finally:
        provider.release.set()
        service.close()


def test_api_auth_isolation_export_and_delete(kb):
    tokens = {"alice-private-token": "alice", "bob-private-token": "bob"}
    doc = kb.ingest("alice", "paper.pdf", pdf())
    provider = Translator()
    with TestClient(create_app(kb=kb, api_tokens=tokens, translator=provider)) as client:
        alice = {"X-API-Key": "alice-private-token"}
        bob = {"X-API-Key": "bob-private-token"}
        assert client.get("/api/translations").status_code == 401
        assert client.get("/api/translations/config", headers=alice).json()["enabled"]
        source_url = f"/api/documents/{doc['document_id']}/pages/1"
        assert "Solar" in client.get(source_url, headers=alice).json()["original"]
        assert client.get(source_url, headers=bob).status_code == 404
        body = {
            "request_id": str(uuid4()),
            "document_id": doc["document_id"],
            "start_page": 1,
            "end_page": 2,
            "target_language": "zh",
        }
        assert client.post("/api/translations", headers=bob, json=body).status_code == 404
        response = client.post("/api/translations", headers=alice, json=body)
        assert response.status_code == 202
        job_id = response.json()["id"]
        wait(client.app.state.translations, job_id)
        path = f"/api/translations/{job_id}"
        assert client.get(path, headers=bob).status_code == 404
        assert client.get(path + "/file", headers=bob).status_code == 404
        assert client.post(path + "/cancel", headers=bob).status_code == 404
        assert not client.get("/api/translations", headers=bob).json()["translations"]
        exported = client.get(path + "/file", headers=alice)
        assert exported.status_code == 200 and exported.content.startswith(b"\xef\xbb\xbf")
        assert "no-store" in exported.headers["cache-control"]
        assert (
            client.post(
                "/api/translations", headers=alice, json={**body, "target_language": "fr"}
            ).status_code
            == 422
        )
        assert client.delete(f"/api/documents/{doc['document_id']}", headers=bob).status_code == 404
        assert client.get(path, headers=alice).status_code == 200
        assert (
            client.delete(f"/api/documents/{doc['document_id']}", headers=alice).status_code == 200
        )
        assert client.get(path, headers=alice).status_code == 404
        assert client.get(path + "/file", headers=alice).status_code == 404


def test_restart_marks_unfinished_jobs_failed_without_automatic_retry(kb):
    doc = kb.ingest("alice", "paper.pdf", pdf())
    provider = Translator()
    service = TranslationService(kb, provider)
    job = wait(service, submit(service, doc, target_language="zh")["id"])
    service.close()
    import sqlite3

    with sqlite3.connect(kb.settings.data_dir / "translations.sqlite3") as db:
        db.execute("UPDATE translation_jobs SET status='running' WHERE id=?", (job["id"],))
    service = TranslationService(kb, provider)
    try:
        saved = service.get("alice", job["id"])
        assert saved["status"] == "failed" and saved["completed_pages"] == 2
        assert len(provider.calls) == 2
    finally:
        service.close()


def test_long_page_segments_translate_every_character_and_reject_partial_page(kb):
    with pymupdf.open() as source:
        page = source.new_page()
        text = "Complete academic sentence with a measurement 254 nm and citation [12].\n" * 55
        assert page.insert_textbox((30, 30, 565, 810), text, fontsize=8) >= 0
        doc = kb.ingest("alice", "long-page.pdf", source.tobytes())
    provider = Translator()
    service = TranslationService(kb, provider)
    try:
        job = service.submit("alice", str(uuid4()), doc["document_id"], 1, 1, "zh")
        done = wait(service, job["id"])
        assert done["status"] == "succeeded"
        assert len(provider.calls) >= 2
        assert "".join(c[0] for c in provider.calls) == done["pages"][0]["original"]
    finally:
        service.close()

    class SecondSegmentFailure(Translator):
        model = "failing-translator"

        def translate(self, text, language):
            result = super().translate(text, language)
            if len(self.calls) == 2:
                raise GenerationError("second segment failed")
            return result

    provider = SecondSegmentFailure()
    service = TranslationService(kb, provider)
    try:
        job = service.submit("alice", str(uuid4()), doc["document_id"], 1, 1, "zh")
        done = wait(service, job["id"])
        assert done["status"] == "failed" and done["completed_pages"] == 0
        assert done["pages"][0]["translation"] == ""
    finally:
        service.close()
