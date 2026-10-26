"""Offline contract checks for the DeepSeek Chat Completions gateway."""

import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from aipulse.llm.config import DeepSeekSettings, LLMConfigurationError, load_deepseek_settings
from aipulse.llm.gateway import DeepSeekGateway, GenerationRequest, LLMError


class ExampleAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str


def settings():
    return DeepSeekSettings(DEEPSEEK_API_KEY="offline-test-key", _env_file=None)


def completion(content="四。", *, finish_reason="stop", reasoning_content="private reasoning"):
    return {
        "id": "offline-1", "object": "chat.completion", "created": 1,
        "model": "deepseek-v4-pro",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": content,
                                 "reasoning_content": reasoning_content}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18,
                  "completion_tokens_details": {"reasoning_tokens": 4}},
    }


def request(*, research=False):
    return GenerationRequest(system_prompt="用中文简答。", user_prompt="2+2?",
                             max_tokens=256, use_research_model=research)


def test_settings_defaults_and_safe_validation(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    loaded = load_deepseek_settings(env_file=None)
    assert loaded.base_url == "https://api.deepseek.com"
    assert (loaded.model, loaded.research_model) == ("deepseek-v4-pro", "deepseek-v4-pro")
    assert (loaded.reasoning_effort, loaded.research_reasoning_effort) == ("high", "max")
    assert loaded.timeout_seconds == 120
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://not-deepseek.invalid")
    with pytest.raises(LLMConfigurationError) as error:
        load_deepseek_settings(env_file=None)
    assert "offline-test-key" not in str(error.value)


def test_settings_read_explicit_model_effort_and_timeout(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_RESEARCH_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "max")
    monkeypatch.setenv("DEEPSEEK_RESEARCH_REASONING_EFFORT", "high")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "45")
    loaded = load_deepseek_settings(env_file=None)
    assert (loaded.reasoning_effort, loaded.research_reasoning_effort) == ("max", "high")
    assert loaded.timeout_seconds == 45


@pytest.mark.asyncio
async def test_text_uses_deepseek_thinking_and_reports_usage_without_reasoning():
    bodies = []

    def respond(http_request):
        bodies.append(json.loads(http_request.content))
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        async with DeepSeekGateway(settings(), http_client=http_client, max_retries=0) as gateway:
            result = await gateway.generate_text(request())
            research_result = await gateway.generate_text(request(research=True))
    assert result.text == "四。" and "private reasoning" not in repr(result)
    assert result.model == "deepseek-v4-pro" and result.elapsed_seconds >= 0
    assert (result.usage.prompt_tokens, result.usage.completion_tokens,
            result.usage.total_tokens, result.usage.reasoning_tokens) == (7, 11, 18, 4)
    assert bodies[0]["reasoning_effort"] == "high"
    assert bodies[1]["reasoning_effort"] == "max"
    assert all(body["thinking"] == {"type": "enabled"} for body in bodies)
    assert all(body["model"] == "deepseek-v4-pro" and body["max_tokens"] == 256 for body in bodies)
    assert all("temperature" not in body and "top_p" not in body for body in bodies)


@pytest.mark.asyncio
async def test_json_mode_validates_model_and_includes_example():
    bodies = []

    def respond(http_request):
        bodies.append(json.loads(http_request.content))
        return httpx.Response(200, json=completion('{"answer":"四。"}'))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        async with DeepSeekGateway(settings(), http_client=http_client, max_retries=0) as gateway:
            result = await gateway.generate_json(
                request(), ExampleAnswer, example=ExampleAnswer(answer="示例"))
    assert result.value.answer == "四。"
    assert result.usage.total_tokens == 18
    body = bodies[0]
    assert body["response_format"] == {"type": "json_object"}
    assert "JSON" in body["messages"][0]["content"]
    assert '"answer":"示例"' in body["messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("content,finish_reason,code", [
    ("", "stop", "empty_response"),
    ("not json", "stop", "invalid_json"),
    ('{"wrong":"value"}', "stop", "schema_mismatch"),
    ('{"answer":"cut"}', "length", "output_truncated"),
])
async def test_json_failures_are_typed(content, finish_reason, code):
    transport = httpx.MockTransport(lambda _request: httpx.Response(
        200, json=completion(content, finish_reason=finish_reason)))
    async with httpx.AsyncClient(transport=transport) as http_client:
        async with DeepSeekGateway(settings(), http_client=http_client, max_retries=0) as gateway:
            with pytest.raises(LLMError) as error:
                await gateway.generate_json(request(), ExampleAnswer,
                                            example=ExampleAnswer(answer="示例"))
    assert error.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason,content,code", [
    ("length", '{"answer":', "output_truncated"),
    ("stop", '{"answer":', "invalid_json"),
    ("stop", '{"wrong":"value"}', "schema_mismatch"),
])
@pytest.mark.parametrize("has_usage", [True, False])
async def test_failed_response_keeps_available_usage_without_sensitive_content(
    finish_reason, content, code, has_usage,
):
    calls = []
    payload = completion(content, finish_reason=finish_reason)
    if not has_usage:
        payload.pop("usage")

    def respond(_request):
        calls.append(1)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        async with DeepSeekGateway(settings(), http_client=http_client, max_retries=0) as gateway:
            with pytest.raises(LLMError) as caught:
                await gateway.generate_json(request(), ExampleAnswer, example=ExampleAnswer(answer="示例"))
    error = caught.value
    assert error.code == code and len(calls) == 1
    assert error.elapsed_seconds >= 0
    assert (error.usage.total_tokens if error.usage else None) == (18 if has_usage else None)
    assert "private reasoning" not in repr(vars(error))
    assert "offline-test-key" not in str(error) + repr(vars(error))
    # Declared field names are intentionally available as diagnostic paths;
    # raw response text and unknown field values must remain absent.
    assert content not in repr(vars(error))
    assert '"wrong":"value"' not in repr(vars(error))


@pytest.mark.asyncio
async def test_report_can_override_effort_without_changing_research_default():
    bodies = []

    def respond(http_request):
        bodies.append(json.loads(http_request.content))
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        async with DeepSeekGateway(settings(), http_client=http_client, max_retries=0) as gateway:
            await gateway.generate_text(request(research=True))
            await gateway.generate_text(GenerationRequest(
                system_prompt="JSON", user_prompt="topic", max_tokens=6144,
                use_research_model=True, reasoning_effort="high",
            ))
    assert [body["reasoning_effort"] for body in bodies] == ["max", "high"]
    assert bodies[1]["max_tokens"] == 6144
    assert all(body["thinking"] == {"type": "enabled"} for body in bodies)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [
    (400, "invalid_request"), (401, "authentication_failed"),
    (402, "insufficient_balance"), (429, "rate_limited"),
    (500, "service_unavailable"),
])
async def test_http_errors_are_classified_without_secret_leak(status, code, caplog):
    calls = []

    def respond(_request):
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": "offline-test-key"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        async with DeepSeekGateway(settings(), http_client=http_client, max_retries=0) as gateway:
            with pytest.raises(LLMError) as error:
                await gateway.generate_text(request())
    assert error.value.code == code and len(calls) == 1
    assert "offline-test-key" not in str(error.value) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_error,code", [
    (httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection_error"),
])
async def test_transport_errors_are_classified(transport_error, code):
    def fail(_request):
        raise transport_error("offline-test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http_client:
        async with DeepSeekGateway(settings(), http_client=http_client, max_retries=0) as gateway:
            with pytest.raises(LLMError) as error:
                await gateway.generate_text(request())
    assert error.value.code == code and "offline-test-key" not in str(error.value)


@pytest.mark.asyncio
async def test_rate_limit_has_one_bounded_retry():
    calls = []

    def respond(_request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        async with DeepSeekGateway(settings(), http_client=http_client, max_retries=1) as gateway:
            result = await gateway.generate_text(request())
    assert result.text == "四。" and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (400, 401, 402))
async def test_non_retryable_statuses_are_attempted_once(status):
    calls = []

    def respond(_request):
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": "rejected"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        async with DeepSeekGateway(settings(), http_client=http_client, max_retries=1) as gateway:
            with pytest.raises(LLMError):
                await gateway.generate_text(request())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_server_error_has_one_bounded_retry_and_closes_client():
    calls = []

    def respond(_request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, json=completion())

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    async with DeepSeekGateway(settings(), http_client=http_client, max_retries=1) as gateway:
        result = await gateway.generate_text(request())
    assert result.text == "四。" and len(calls) == 2
    assert http_client.is_closed
