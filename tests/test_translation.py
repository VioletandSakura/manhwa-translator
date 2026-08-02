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
    assert payload["model"] == "deepseek-chat"
    assert "image_url" not in captured_request.content.decode("utf-8")
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


@pytest.mark.parametrize(
    ("status", "message"),
    [(400, "模型配置无效"), (401, "API Key无效"), (402, "余额不足"), (404, "模型不存在"), (429, "请求过于频繁"), (500, "暂时不可用")],
)
def test_translation_maps_common_http_errors(status: int, message: str) -> None:
    response = httpx.Response(status)

    with pytest.raises(TranslationServiceError, match=message):
        OpenAICompatibleTranslator._raise_for_status(response)
