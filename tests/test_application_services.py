import asyncio
from pathlib import Path

from app.security.credentials import CredentialStore
from app.services.cached_translator import CachedTranslator
from app.services.safe_logger import SafeLogger
from app.storage.database import Database


class MemoryCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def delete_password(self, service_name: str, username: str) -> None:
        self.values.pop((service_name, username), None)


class CountingTranslator:
    def __init__(self) -> None:
        self.calls = 0

    async def translate(self, texts: list[str], source_language: str, target_language: str) -> list[str]:
        self.calls += 1
        return [f"译:{text}" for text in texts]


def test_credential_store_can_save_load_and_revoke() -> None:
    backend = MemoryCredentialBackend()
    store = CredentialStore(backend)

    store.save("deepseek-default", "secret-value")
    assert store.load("deepseek-default") == "secret-value"
    store.revoke("deepseek-default")
    assert store.load("deepseek-default") is None


def test_cached_translator_avoids_duplicate_api_call(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    delegate = CountingTranslator()
    translator = CachedTranslator(database, delegate, provider="DeepSeek", model="deepseek-chat")

    first = asyncio.run(translator.translate(["hello"], "auto", "zh-Hans"))
    second = asyncio.run(translator.translate(["hello"], "auto", "zh-Hans"))

    assert first == second == ["译:hello"]
    assert delegate.calls == 1


def test_safe_logger_redacts_secrets_before_writing(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    logger = SafeLogger(log_path)

    logger.info("Authorization: Bearer fake-sensitive-token api_key=hidden-value")

    contents = log_path.read_text(encoding="utf-8")
    assert "fake-sensitive-token" not in contents
    assert "hidden-value" not in contents
    assert "***" in contents
