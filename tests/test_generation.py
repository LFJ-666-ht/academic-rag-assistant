"""Model budgets, streaming, truncation, and citations; no paid API calls."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import pdf

from backend.generation import (
    DeepSeek,
    GeneratedText,
    GenerationError,
    answer_question,
    stream_question,
)
from backend.main import create_app

ORIGINAL_CLIENT = httpx.Client


@pytest.fixture(autouse=True)
def config(monkeypatch):
    for name in (
        "DEEPSEEK_MAX_TOKENS",
        "DEEPSEEK_TOKEN_PARAMETER",
        "DEEPSEEK_REASONING_EFFORT",
        "DEEPSEEK_THINKING",
        "DEEPSEEK_IMAGE_PROMPT_MAX_TOKENS",
        "DEEPSEEK_TIMEOUT_SECONDS",
        "DEEPSEEK_TOTAL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-provider-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-1-flash-261017")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")


def chunk(text=None, finish=None, reasoning=None):
    return {
        "choices": [
            {"delta": {"content": text, "reasoning_content": reasoning}, "finish_reason": finish}
        ]
    }


def sse(*bodies):
    return (
        "".join("data: " + json.dumps(x, ensure_ascii=False) + "\n\n" for x in bodies)
        + "data: [DONE]\n\n"
    ).encode()


def mock(monkeypatch, handle):
    original = ORIGINAL_CLIENT
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )


def event_response(*bodies):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(*bodies))


def test_261017_budget_reasoning_and_private_reasoning_not_forwarded(monkeypatch):
    calls = []

    def handle(req):
        body = json.loads(req.content)
        calls.append(body)
        assert body["max_tokens"] == 393216 and "max_completion_tokens" not in body
        assert body["reasoning_effort"] == "max" and body["thinking"] == {"type": "enabled"}
        assert body["stream"] and body["stream_options"]["include_usage"]
        return event_response(
            chunk(reasoning="private reasoning"),
            chunk("结论"),
            chunk(" [1]", "stop"),
            {
                "choices": [],
                "usage": {
                    "completion_tokens": 2345,
                    "completion_tokens_details": {"reasoning_tokens": 2300},
                    "private_key": "secret",
                },
            },
        )

    mock(monkeypatch, handle)
    events = list(DeepSeek().stream("q", []))
    assert events[-1]["text"] == "结论 [1]"
    assert events[-1]["text"].usage == {"completion_tokens": 2345, "reasoning_tokens": 2300}
    assert "private" not in json.dumps(events) and len(calls) == 1


def test_explicit_config_sends_only_one_token_parameter(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_TOKEN_PARAMETER", "max_completion_tokens")
    monkeypatch.setenv("DEEPSEEK_MAX_TOKENS", "32768")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "high")
    body = DeepSeek()._request("s", {}, False)[2]
    assert body["max_completion_tokens"] == 32768 and "max_tokens" not in body
    assert body["reasoning_effort"] == "high"


def test_unknown_model_or_provider_is_not_given_ark_preset(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.test")
    body = DeepSeek()._request("s", {}, False)[2]
    assert "max_tokens" not in body and "thinking" not in body
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    monkeypatch.setenv("DEEPSEEK_MODEL", "ep-user-deployment")
    body = DeepSeek()._request("s", {}, False)[2]
    assert body["model"] == "ep-user-deployment" and "reasoning_effort" not in body


def test_260910_retains_documented_default_budget(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-1-flash-260910")
    assert DeepSeek()._request("s", {}, False)[2]["max_tokens"] == 131072


@pytest.mark.parametrize(
    "name,value",
    [
        ("DEEPSEEK_MAX_TOKENS", "0"),
        ("DEEPSEEK_MAX_TOKENS", "384k"),
        ("DEEPSEEK_TOKEN_PARAMETER", "both"),
        ("DEEPSEEK_REASONING_EFFORT", "super"),
        ("DEEPSEEK_THINKING", "yes"),
        ("DEEPSEEK_TIMEOUT_SECONDS", "-1"),
    ],
)
def test_invalid_configuration_fails_before_request(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(GenerationError):
        DeepSeek()._request("s", {}, False)


def test_length_keeps_long_answer_and_citations(monkeypatch, kb):
    kb.ingest("alice", "paper.pdf", pdf())
    answer = "研究方法的详细分析。" * 2000 + " [1]"
    mock(monkeypatch, lambda req: event_response(chunk(answer, "length")))
    result = answer_question(kb, DeepSeek(), "alice", "UV")
    assert result["answer"] == answer and result["incomplete"]
    assert result["finish_reason"] == "length" and result["citations"][0]["id"] == 1


def test_length_without_body_has_specific_error(monkeypatch):
    mock(
        monkeypatch, lambda req: event_response(chunk(reasoning="thinking"), chunk(finish="length"))
    )
    with pytest.raises(GenerationError, match="正文前已耗尽"):
        DeepSeek()("q", [])


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_http_errors_hide_secrets_and_never_retry(monkeypatch, status):
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(status, text="private-provider-key private upstream details")

    mock(monkeypatch, handle)
    with pytest.raises(GenerationError) as exc:
        DeepSeek()("q", [])
    assert "private" not in str(exc.value) and len(calls) == 1


def test_network_drop_keeps_partial_and_closes_upstream(monkeypatch):
    closed = []

    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield sse(chunk("正文 [1]")).replace(b"data: [DONE]\n\n", b"")
            raise httpx.ReadError("private detail")

        def close(self):
            closed.append(True)

    mock(
        monkeypatch,
        lambda req: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Broken()
        ),
    )
    result = DeepSeek()("q", [])
    assert result == "正文 [1]" and result.finish_reason == "interrupted" and closed


def test_missing_finish_is_incomplete_and_filter_is_error(monkeypatch):
    mock(monkeypatch, lambda req: event_response(chunk("partial")))
    assert DeepSeek()("q", []).finish_reason == "interrupted"
    mock(monkeypatch, lambda req: event_response(chunk("partial", "content_filter")))
    with pytest.raises(GenerationError):
        DeepSeek()("q", [])


def test_image_prompt_has_separate_budget_and_rejects_truncation(monkeypatch):
    def handle(req):
        body = json.loads(req.content)
        assert body["max_tokens"] == 4096 and body["thinking"]["type"] == "disabled"
        return event_response(chunk("描述", "length"))

    mock(monkeypatch, handle)
    with pytest.raises(GenerationError, match="未生成完整"):
        DeepSeek().image_prompt("画图", [])


def test_stream_auth_validation_and_incomplete_answer(kb):
    kb.ingest("alice", "paper.pdf", pdf())

    class Text:
        def stream(self, q, e):
            yield {"type": "delta", "text": "结论 [1]"}
            yield {"type": "result", "text": GeneratedText("结论 [1]", "length")}

    with TestClient(create_app(kb, Text(), {"a" * 20: "alice"})) as client:
        headers = {"X-API-Key": "a" * 20}
        assert client.post("/api/chat/stream", json={"question": "UV"}).status_code == 401
        assert (
            client.post(
                "/api/chat/stream", headers=headers, json={"question": "UV", "top_k": 31}
            ).status_code
            == 422
        )
        res = client.post("/api/chat/stream", headers=headers, json={"question": "UV", "top_k": 30})
        events = [json.loads(x) for x in res.text.splitlines()]
        assert events[-1]["type"] == "done" and events[-1]["answer"]["incomplete"]
        assert events[-1]["answer"]["citations"][0]["id"] == 1


def test_cross_user_scope_and_invalid_citations_are_rejected(kb):
    doc = kb.ingest("bob", "private.pdf", pdf())
    kb.ingest("alice", "own.pdf", pdf())
    with TestClient(
        create_app(kb, lambda *a: GeneratedText("bad [999]", "length"), {"a" * 20: "alice"})
    ) as client:
        headers = {"X-API-Key": "a" * 20}
        res = client.post(
            "/api/chat/stream",
            headers=headers,
            json={"question": "UV", "document_ids": [doc["document_id"]]},
        )
        assert (
            json.loads(res.text.splitlines()[-1])["status"] == 404 and "private.pdf" not in res.text
        )
        res = client.post("/api/chat/stream", headers=headers, json={"question": "UV"})
        event = json.loads(res.text.splitlines()[-1])
        assert event["type"] == "error" and "引用编号" in event["message"]


def test_empty_scope_skips_model(kb):
    events = list(
        stream_question(kb, lambda *a: pytest.fail("must not call provider"), "alice", "q")
    )
    assert events[-1]["answer"]["citation_status"] == "no_evidence"
