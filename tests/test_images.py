"""No real models or billable provider requests; exercise HTTP, files and SQLite."""

import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient
from helpers import pdf

from backend.generation import DeepSeek, GenerationError
from backend.images import ImageBusy, ImageConflict, ImageService, Seedream
from backend.main import create_app


def png():
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 20, 20))
    pix.clear_with(240)
    return pix.tobytes("png")


class FakeImage:
    enabled = True
    model = "test-seedream"

    def __init__(self, gate=None, error=None):
        self.calls = 0
        self.gate = gate
        self.error = error

    def __call__(self, prompt):
        self.calls += 1
        if self.gate:
            assert self.gate.wait(4)
        if self.error:
            raise self.error
        return png(), "png"


def finished(service, owner, job):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        result = service.get(owner, job["id"])
        if result["status"] in {"succeeded", "failed"}:
            return result
        time.sleep(0.01)
    pytest.fail("Job did not finish")


def test_provider_payload_and_saved_format(monkeypatch):
    monkeypatch.setenv("SEEDREAM_API_KEY", "provider-secret")
    monkeypatch.setenv("SEEDREAM_MODEL", "doubao-seedream-4-0-20260415")
    monkeypatch.setenv("SEEDREAM_BASE_URL", "https://example.test/api/v3")
    original = httpx.Client
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://example.test/api/v3/images/generations"
        assert request.headers["Authorization"] == "Bearer provider-secret"
        assert json.loads(request.content) == {
            "model": "doubao-seedream-4-0-20260415",
            "prompt": "science illustration",
            "size": "2K",
            "sequential_image_generation": "disabled",
            "stream": False,
            "response_format": "b64_json",
            "watermark": True,
        }
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    assert Seedream()("science illustration") == (png(), "png")
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_provider_errors_hide_details_and_never_retry(monkeypatch, status):
    monkeypatch.setenv("SEEDREAM_API_KEY", "provider-secret")
    original = httpx.Client
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="provider-secret and internal details")

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    with pytest.raises(GenerationError) as error:
        Seedream()("x")
    assert "provider-secret" not in str(error.value)
    assert "internal details" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": []},
        {"data": [{"b64_json": "not base64!"}]},
        {"data": [{"url": "http://127.0.0.1/private"}]},
        {"data": [{"b64_json": base64.b64encode(b"<svg onload='alert(1)'/>").decode()}]},
    ],
)
def test_invalid_responses_are_rejected_without_url_download(monkeypatch, body):
    monkeypatch.setenv("SEEDREAM_API_KEY", "test")
    original = httpx.Client
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=body)

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    with pytest.raises(GenerationError):
        Seedream()("x")
    assert len(calls) == 1


def test_timeout_does_not_retry(monkeypatch):
    monkeypatch.setenv("SEEDREAM_API_KEY", "test")
    original = httpx.Client
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("private details")

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    with pytest.raises(GenerationError, match="平台可能已受理"):
        Seedream()("x")
    assert len(calls) == 1


def test_idempotency_concurrency_owner_limits_and_persistence(tmp_path):
    gate = threading.Event()
    provider = FakeImage(gate)
    service = ImageService(tmp_path, provider)
    request = str(uuid4())
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(
                pool.map(lambda _: service.submit("alice", request, "private prompt"), range(4))
            )
        assert len({job["id"] for job in jobs}) == 1
        with pytest.raises(ImageConflict):
            service.submit("alice", request, "different prompt")
        with pytest.raises(ImageBusy):
            service.submit("alice", str(uuid4()), "second image")
        assert service.list("bob") == []
        with pytest.raises(KeyError):
            service.file("bob", jobs[0]["id"])
        gate.set()
        job = finished(service, "alice", jobs[0])
        assert job["status"] == "succeeded"
        assert provider.calls == 1
        path, media = service.file("alice", job["id"])
        assert path.read_bytes() == png() and media == "image/png"
    finally:
        gate.set()
        service.close()
    service = ImageService(tmp_path, provider)
    try:
        assert service.get("alice", job["id"])["status"] == "succeeded"
        assert service.submit("alice", request, "private prompt")["id"] == job["id"]
        assert provider.calls == 1
        with service.db:
            service.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    finally:
        service.close()
    service = ImageService(tmp_path, provider)
    try:
        assert service.get("alice", job["id"])["status"] == "failed"
        assert "后端" in service.get("alice", job["id"])["error"]
        assert provider.calls == 1
    finally:
        service.close()


def test_job_failure_is_terminal_and_not_retried(tmp_path):
    provider = FakeImage(error=GenerationError("test failure"))
    service = ImageService(tmp_path, provider)
    try:
        job = service.submit("alice", str(uuid4()), "x")
        assert finished(service, "alice", job)["error"] == "test failure"
        assert service.submit("alice", job["request_id"], "x")["status"] == "failed"
        assert provider.calls == 1
        with pytest.raises(ImageConflict):
            service.file("alice", job["id"])
    finally:
        service.close()


class FakeText:
    def __call__(self, question, sources):
        return "回答 [1]"

    def image_prompt(self, instruction, sources):
        assert sources[0]["quote"] and sources[0]["page"] > 0
        return "根据原文整理的概念配图，白色背景。"


def test_dual_model_api_draft_owner_validation_download_and_chat(kb):
    provider = FakeImage()
    a = {"X-API-Key": "a" * 20}
    b = {"X-API-Key": "b" * 20}
    app = create_app(kb, FakeText(), {"a" * 20: "alice", "b" * 20: "bob"}, provider)
    with TestClient(app) as client:
        assert client.get("/api/images/config").status_code == 401
        assert client.get("/api/images", headers=a).json() == {"images": []}
        assert (
            client.post("/api/images/prompt", headers=a, json={"question": "UV"}).status_code == 422
        )
        document = kb.ingest("alice", "paper.pdf", pdf())
        assert (
            client.post("/api/chat", headers=a, json={"question": "UV"}).json()["answer"]
            == "回答 [1]"
        )
        body = {"question": "UV detector", "document_ids": [document["document_id"]]}
        assert client.post("/api/images/prompt", headers=b, json=body).status_code == 404
        draft = client.post("/api/images/prompt", headers=a, json=body).json()
        body = {
            "request_id": str(uuid4()),
            "prompt": draft["prompt"] + " 修改颜色",
            "draft_id": draft["draft_id"],
        }
        assert client.post("/api/images", headers=b, json=body).status_code == 404
        response = client.post("/api/images", headers=a, json=body)
        assert response.status_code == 202, response.text
        job = finished(app.state.images, "alice", response.json())
        assert job["evidence"] == draft["evidence"]
        assert client.post("/api/images", headers=a, json=body).json()["id"] == job["id"]
        assert client.get(f"/api/images/{job['id']}", headers=b).status_code == 404
        assert client.get(job["file_url"]).status_code == 401
        assert client.get(job["file_url"], headers=b).status_code == 404
        file = client.get(job["file_url"], headers=a)
        assert file.content == png()
        assert file.headers["Cache-Control"] == "no-store"
        assert client.get("/api/images", headers=b).json() == {"images": []}
        assert provider.calls == 1
        assert (
            client.post(
                "/api/images", headers=a, json={"request_id": str(uuid4()), "prompt": " "}
            ).status_code
            == 422
        )


def test_no_image_key_does_not_break_chat(kb, monkeypatch):
    monkeypatch.delenv("SEEDREAM_API_KEY", raising=False)
    kb.ingest("alice", "paper.pdf", pdf())
    with TestClient(create_app(kb, FakeText(), {"a" * 20: "alice"})) as client:
        headers = {"X-API-Key": "a" * 20}
        assert client.get("/api/images/config", headers=headers).json()["enabled"] is False
        assert client.post("/api/chat", headers=headers, json={"question": "UV"}).status_code == 200
        assert (
            client.post(
                "/api/images", headers=headers, json={"request_id": str(uuid4()), "prompt": "x"}
            ).status_code
            == 422
        )


def test_deepseek_drawing_prompt_uses_separate_instructions(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "text-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", "text-model")
    original = httpx.Client

    def handle(request):
        body = json.loads(request.content)
        assert "绘图提示词" in body["messages"][0]["content"]
        assert "精确数据图" in body["messages"][0]["content"]
        assert json.loads(body["messages"][1]["content"])["instruction"] == "画概念"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "绘图描述"}, "finish_reason": "stop"}]}
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    assert DeepSeek().image_prompt("画概念", [{"id": 1, "quote": "资料"}]) == "绘图描述"
