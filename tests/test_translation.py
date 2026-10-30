import asyncio
import json

import httpx
import pytest

from app.translation.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleTranslator,
    TranslationServiceError,
)


def test_deepseek_translation_uses_chat_completions_without_images() -> None:
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"translations": ["你好"]})}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig.deepseek(api_key="secret-key"),
        client=client,
    )

    result = asyncio.run(translator.translate(["こんにちは"], "auto", "zh-Hans"))

    assert result == ["你好"]
    assert captured_request is not None
    payload = json.loads(captured_request.content)
    assert captured_request.url.path.endswith("/chat/completions")
    assert payload["model"] == "deepseek-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert "json" in " ".join(message["content"] for message in payload["messages"]).casefold()
    assert "image_url" not in captured_request.content.decode("utf-8")
    asyncio.run(client.aclose())


def test_compatible_providers_do_not_receive_deepseek_specific_options() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    translator = OpenAICompatibleTranslator(OpenAICompatibleConfig(
        service_name="Compatible", api_key="test-key", base_url="https://example.test/v1", model="custom",
    ), client=client)
    payload = translator._build_payload(["Hello"], "en", "zh-Hans", "Return JSON", None)
    assert "thinking" not in payload
    asyncio.run(client.aclose())


def test_connection_check_uses_models_endpoint_without_translation_cost() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig.deepseek(api_key="secret-key"), client=client
    )

    result = asyncio.run(translator.test_connection())

    assert result == "deepseek-flash"
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path.endswith("/models")
    asyncio.run(client.aclose())


def test_list_models_returns_unique_sorted_model_ids() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "deepseek-v4-pro"},
                    {"id": "deepseek-flash"},
                    {"id": "deepseek-v4-pro"},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig.deepseek(api_key="secret-key"), client=client
    )

    models = asyncio.run(translator.list_models())

    assert models == ["deepseek-flash", "deepseek-v4-pro"]
    asyncio.run(client.aclose())


def test_connection_check_does_not_repeat_slow_network_failure() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("timed out", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig(
            service_name="DeepSeek",
            api_key="secret-key",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            timeout_seconds=60,
            retries=4,
        ),
        client=client,
    )

    with pytest.raises(TranslationServiceError, match="连通性测试超时"):
        asyncio.run(translator.test_connection())

    assert attempts == 1
    asyncio.run(client.aclose())


def test_connection_check_reports_configured_model_not_available() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig(
            service_name="DeepSeek",
            api_key="secret-key",
            base_url="https://api.deepseek.com/v1",
            model="wrong-model",
        ),
        client=client,
    )

    with pytest.raises(TranslationServiceError, match="模型不存在"):
        asyncio.run(translator.test_connection())
    asyncio.run(client.aclose())


def test_translation_maps_403_to_clear_chinese_message() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "forbidden"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig.deepseek(api_key="secret-key"),
        client=client,
    )

    with pytest.raises(TranslationServiceError, match="拒绝访问"):
        asyncio.run(translator.translate(["test"], "auto", "zh-Hans"))
    asyncio.run(client.aclose())


def test_translation_accepts_json_code_fence() -> None:
    content = '```json\n{"translations":["你好"]}\n```'

    assert OpenAICompatibleTranslator._parse_translations(content) == ["你好"]


def test_custom_system_prompt_is_augmented_for_json_mode() -> None:
    translator = OpenAICompatibleTranslator(OpenAICompatibleConfig.deepseek(api_key="secret-key"))

    payload = translator._build_payload(["こんにちは"], "ja", "zh-Hans", "忠实翻译", None)

    assert "json" in payload["messages"][0]["content"].casefold()
    asyncio.run(translator.close())


def test_http_400_includes_redacted_provider_reason() -> None:
    response = httpx.Response(
        400,
        json={"error": {"message": "Prompt must contain JSON; token=secret-value-123456"}},
    )

    with pytest.raises(TranslationServiceError) as captured:
        OpenAICompatibleTranslator._raise_for_status(response)

    assert "Prompt must contain JSON" in str(captured.value)
    assert "secret-value-123456" not in str(captured.value)


@pytest.mark.parametrize(
    ("status", "message"),
    [(400, "模型配置无效"), (401, "API Key无效"), (402, "余额不足"), (404, "模型不存在"), (429, "请求过于频繁"), (500, "暂时不可用")],
)
def test_translation_maps_common_http_errors(status: int, message: str) -> None:
    response = httpx.Response(status)

    with pytest.raises(TranslationServiceError, match=message):
        OpenAICompatibleTranslator._raise_for_status(response)


def test_translation_retries_rate_limit_then_succeeds(monkeypatch) -> None:
    attempts = 0
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("app.translation.openai_compatible.asyncio.sleep", fake_sleep)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"translations": ["你好"]})}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig(
            service_name="DeepSeek",
            api_key="secret-key",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            retries=2,
        ),
        client=client,
    )

    result = asyncio.run(translator.translate(["こんにちは"], "auto", "zh-Hans"))

    assert result == ["你好"]
    assert attempts == 2
    assert delays == [0.5]
    asyncio.run(client.aclose())


def test_translation_honors_retry_after_header(monkeypatch) -> None:
    attempts = 0
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("app.translation.openai_compatible.asyncio.sleep", fake_sleep)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, headers={"Retry-After": "2"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"translations": ["你好"]})}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig(
            service_name="DeepSeek",
            api_key="secret-key",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            retries=1,
        ),
        client=client,
    )

    result = asyncio.run(translator.translate(["こんにちは"], "auto", "zh-Hans"))

    assert result == ["你好"]
    assert attempts == 2
    assert delays == [2.0]
    asyncio.run(client.aclose())


def test_translation_gives_up_after_retrying_server_errors(monkeypatch) -> None:
    attempts = 0
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("app.translation.openai_compatible.asyncio.sleep", fake_sleep)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig(
            service_name="DeepSeek",
            api_key="secret-key",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            retries=2,
        ),
        client=client,
    )

    with pytest.raises(TranslationServiceError, match="暂时不可用"):
        asyncio.run(translator.translate(["test"], "auto", "zh-Hans"))

    assert attempts == 3
    assert delays == [0.5, 1.0]
    asyncio.run(client.aclose())


def test_translation_retries_network_errors_with_backoff(monkeypatch) -> None:
    attempts = 0
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("app.translation.openai_compatible.asyncio.sleep", fake_sleep)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"translations": ["你好"]})}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig(
            service_name="DeepSeek",
            api_key="secret-key",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            retries=2,
        ),
        client=client,
    )

    result = asyncio.run(translator.translate(["こんにちは"], "auto", "zh-Hans"))

    assert result == ["你好"]
    assert attempts == 3
    assert delays == [0.5, 1.0]
    asyncio.run(client.aclose())


def test_translation_splits_large_text_batches() -> None:
    received: list[list[str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        request_data = json.loads(payload["messages"][1]["content"])
        texts = list(request_data["texts"])
        received.append(texts)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"translations": texts})}}
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    translator = OpenAICompatibleTranslator(
        OpenAICompatibleConfig(
            service_name="DeepSeek",
            api_key="secret-key",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            max_texts_per_request=2,
        ),
        client=client,
    )

    result = asyncio.run(
        translator.translate(["a", "b", "c", "d"], "auto", "zh-Hans")
    )

    assert result == ["a", "b", "c", "d"]
    assert received == [["a", "b"], ["c", "d"]]
    asyncio.run(client.aclose())
