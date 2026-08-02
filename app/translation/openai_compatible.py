from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.security.redaction import redact_text


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    service_name: str
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0
    retries: int = 2

    @classmethod
    def deepseek(cls, api_key: str) -> "OpenAICompatibleConfig":
        return cls(
            service_name="DeepSeek",
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        )


class TranslationServiceError(RuntimeError):
    pass


class OpenAICompatibleTranslator:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def test_connection(self) -> str:
        translations = await self.translate(["connection test"], "en", "zh-Hans")
        return translations[0]

    async def translate(
        self,
        texts: list[str],
        source_language: str,
        target_language: str,
        *,
        system_prompt: str = "你是专业漫画翻译员。忠实、自然地翻译，不添加解释。",
        context: list[str] | None = None,
    ) -> list[str]:
        if not texts:
            return []
        payload = self._build_payload(texts, source_language, target_language, system_prompt, context)
        response = await self._request(payload)
        try:
            content = response.json()["choices"][0]["message"]["content"]
            translations = self._parse_translations(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise TranslationServiceError("翻译服务返回了无法识别的数据格式") from error
        if len(translations) != len(texts):
            raise TranslationServiceError(
                f"翻译条目数量不匹配：发送{len(texts)}条，收到{len(translations)}条"
            )
        return translations

    def _build_payload(
        self,
        texts: list[str],
        source_language: str,
        target_language: str,
        system_prompt: str,
        context: list[str] | None,
    ) -> dict[str, Any]:
        request_data = {
            "source_language": source_language,
            "target_language": target_language,
            "texts": texts,
            "context": context or [],
            "output_schema": {"translations": ["与texts等长的译文数组"]},
        }
        return {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(request_data, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

    async def _request(self, payload: dict[str, Any]) -> httpx.Response:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            try:
                response = await self._client.post(url, headers=headers, json=payload)
                if response.is_success:
                    return response
                self._raise_for_status(response)
            except TranslationServiceError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
                if attempt >= self.config.retries:
                    raise TranslationServiceError("无法连接翻译服务，请检查网络、Base URL和超时设置") from error
        raise TranslationServiceError(redact_text(str(last_error or "翻译请求失败")))

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        messages = {
            400: "请求参数或模型配置无效",
            401: "API Key无效或已失效",
            402: "账户余额不足或需要付费",
            403: "服务拒绝访问，可能是权限或地区限制",
            404: "Base URL、接口或模型不存在",
            429: "请求过于频繁，已达到服务限额",
        }
        message = messages.get(response.status_code)
        if message is None and response.status_code >= 500:
            message = "翻译服务暂时不可用"
        if message is None:
            message = f"翻译请求失败（HTTP {response.status_code}）"
        raise TranslationServiceError(message)

    @staticmethod
    def _parse_translations(content: str) -> list[str]:
        cleaned = str(content).strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        data = json.loads(cleaned)
        translations = data.get("translations")
        if not isinstance(translations, list) or not all(isinstance(item, str) for item in translations):
            raise TranslationServiceError("翻译结果缺少translations数组")
        return translations
