"""Integration tests for persistence, retrieval, citations and tenant isolation."""

import json

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient
from helpers import FakeModels, pdf

from backend.core import KnowledgeBase, Settings, bm25, rrf
from backend.generation import DeepSeek, GenerationError, answer_question
from backend.main import create_app


def test_persistence_dedup_and_multiple_papers(tmp_path):
    data = pdf()
    kb = KnowledgeBase(Settings(tmp_path), FakeModels())
    a = kb.ingest("alice", "one.pdf", data)
    assert kb.ingest("alice", "renamed.pdf", data)["duplicate"]
    kb.ingest("alice", "two.pdf", pdf("Another paper on quantum efficiency."))
    kb.close()
    kb = KnowledgeBase(Settings(tmp_path), FakeModels())
    try:
        assert len(kb.documents("alice")) == 2
        chunks = kb.retrieve("alice", "detector", 10)
        assert len({c["document_id"] for c in chunks}) == 2
        assert {c["page"] for c in chunks} == {1, 2}
        kb.delete("alice", a["document_id"])
        assert all(c["document_id"] != a["document_id"] for c in kb.retrieve("alice", "detector"))
        assert not (kb.pdf_dir / (a["document_id"] + ".pdf")).exists()
    finally:
        kb.close()


def test_tenant_isolation(kb):
    data = pdf()
    a = kb.ingest("alice", "secret.pdf", data)
    b = kb.ingest("bob", "secret.pdf", data)
    assert a["document_id"] != b["document_id"]
    assert len(kb.documents("bob")) == 1
    assert {c["document_id"] for c in kb.retrieve("bob", "UV")} == {b["document_id"]}
    with pytest.raises(KeyError):
        kb.retrieve("bob", "UV", document_ids=[a["document_id"]])
    with pytest.raises(KeyError):
        kb.delete("bob", a["document_id"])


def test_rrf_keeps_identity_not_text_or_zip_order():
    a = {"chunk_id": "a", "text": "same"}
    b = {"chunk_id": "b", "text": "same"}
    c = {"chunk_id": "c", "text": "other"}
    assert [x["chunk_id"] for x in rrf([a, b], [b, c])] == ["b", "a", "c"]


def test_bm25_english_chinese_and_no_match():
    chunks = [{"text": "氧化镓 紫外 探测器"}, {"text": "solar blind detector"}]
    assert bm25("氧化镓", chunks) == chunks[:1]
    assert bm25("SOLAR", chunks) == chunks[1:]
    assert bm25("nonexistentxyz", chunks) == []


def test_citations_and_empty(kb):
    def never(*args):
        raise AssertionError("Must not call provider for an empty KB")

    assert answer_question(kb, never, "alice", "UV")["citation_status"] == "no_evidence"
    kb.ingest("alice", "paper.pdf", pdf())
    result = answer_question(kb, lambda q, e: "结论 [1]", "alice", "detector")
    assert len(result["citations"]) == 1
    assert result["citations"][0]["quote"]
    with pytest.raises(GenerationError):
        answer_question(kb, lambda q, e: "虚假引用 [999]", "alice", "detector")
    assert (
        answer_question(kb, lambda q, e: "证据不足", "alice", "UV")["citation_status"]
        == "missing_citations"
    )


def test_failure_rolls_back_and_allows_retry(kb):
    class Broken(FakeModels):
        def encode(self, *args, **kwargs):
            raise RuntimeError("model failed")

    kb.models = Broken()
    data = pdf()
    with pytest.raises(RuntimeError):
        kb.ingest("alice", "broken.pdf", data)
    assert kb.documents("alice") == []
    assert list(kb.pdf_dir.iterdir()) == []
    kb.models = FakeModels()
    assert not kb.ingest("alice", "retry.pdf", data)["duplicate"]


def test_restart_recovers_interrupted_delete(tmp_path):
    kb = KnowledgeBase(Settings(tmp_path), FakeModels())
    kb.ingest("alice", "paper.pdf", pdf())
    with kb.db:
        kb.db.execute("UPDATE documents SET status='deleting'")
    kb.close()
    kb = KnowledgeBase(Settings(tmp_path), FakeModels())
    try:
        assert kb.documents("alice") == []
        assert kb.vectors.count(kb.collection).count == 0
    finally:
        kb.close()


def test_model_change_rejected(tmp_path):
    kb = KnowledgeBase(Settings(tmp_path), FakeModels())
    kb.close()
    with pytest.raises(ValueError, match="Embedding"):
        KnowledgeBase(Settings(tmp_path, embedding_model="other-model"), FakeModels())


def test_bad_pdf_and_empty_pdf(kb):
    with pytest.raises(ValueError):
        kb.ingest("a", "bad.pdf", b"not a PDF")
    with pymupdf.open() as doc:
        doc.new_page()
        data = doc.tobytes()
    with pytest.raises(ValueError, match="OCR"):
        kb.ingest("a", "scan.pdf", data)


def test_api_contract_and_auth(kb):
    app = create_app(kb, lambda q, e: "检测结果 [1]", {"a" * 20: "alice", "b" * 20: "bob"})
    with TestClient(app) as client:
        a = {"X-API-Key": "a" * 20}
        b = {"X-API-Key": "b" * 20}
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/documents").status_code == 401
        response = client.post(
            "/api/documents", headers=a, files={"file": ("x.pdf", pdf(), "application/pdf")}
        )
        assert response.status_code == 200, response.text
        did = response.json()["document_id"]
        assert client.get(f"/api/documents/{did}/file", headers=b).status_code == 404
        assert client.get(f"/api/documents/{did}/file", headers=a).content.startswith(b"%PDF")
        assert client.post("/api/chat", headers=a, json={"question": "  "}).status_code == 422
        assert (
            client.post("/api/chat", headers=a, json={"question": "UV", "top_k": 0}).status_code
            == 422
        )
        answer = client.post("/api/chat", headers=a, json={"question": "UV"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["citations"][0]["filename"] == "x.pdf"
        assert client.post(
            "/api/search", headers=a, json={"question": "UV", "document_ids": []}
        ).json() == {"chunks": []}
        assert client.delete(f"/api/documents/{did}", headers=b).status_code == 404
        assert client.delete(f"/api/documents/{did}", headers=a).status_code == 200


def test_upload_size_and_extension(kb):
    kb.settings.max_upload_mb = 1
    app = create_app(kb, api_tokens={"a" * 20: "alice"})
    with TestClient(app) as client:
        headers = {"X-API-Key": "a" * 20}
        assert (
            client.post(
                "/api/documents", headers=headers, files={"file": ("x.txt", b"hello")}
            ).status_code
            == 415
        )
        assert (
            client.post(
                "/api/documents",
                headers=headers,
                files={"file": ("x.pdf", b"x" * (1024 * 1024 + 1))},
            ).status_code
            == 413
        )


def test_deepseek_adapter(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    original = httpx.Client

    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert request.url.path == "/chat/completions"
        assert "evidence" in body["messages"][1]["content"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "答案 [1]"}, "finish_reason": "stop"}]}
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )
    assert DeepSeek()("question", [{"id": 1, "quote": "text"}]) == "答案 [1]"
