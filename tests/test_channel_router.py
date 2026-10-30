from __future__ import annotations

import asyncio
from pathlib import Path

from app.domain.models import ApiChannelRecord
from app.security.credentials import CredentialStore
from app.storage.database import Database
from app.translation.openai_compatible import TranslationServiceError
from app.translation.router import AsyncRateLimiter, ChannelRouterTranslator

from test_application_services import MemoryCredentialBackend


def _channel(channel_id: str, priority: int, *, strategy: str = "priority") -> ApiChannelRecord:
    return ApiChannelRecord(
        id=channel_id,
        service_name=channel_id,
        enabled=True,
        base_url="https://example.test/v1",
        model="model",
        timeout_seconds=30,
        max_concurrency=1,
        rpm_limit=60,
        retries=0,
        priority=priority,
        strategy=strategy,
        verified=True,
        last_error=None,
    )


class FakeTranslator:
    def __init__(self, channel_id: str, calls: list[str], *, fail: bool = False) -> None:
        self.channel_id = channel_id
        self.calls = calls
        self.fail = fail

    async def translate(self, texts, source_language, target_language):
        self.calls.append(self.channel_id)
        if self.fail:
            raise TranslationServiceError(f"{self.channel_id} failed")
        return [f"{self.channel_id}:{text}" for text in texts]


def test_router_fails_over_to_next_verified_channel(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    database.upsert_api_channel(_channel("primary", 10))
    database.upsert_api_channel(_channel("backup", 20))
    credentials = CredentialStore(MemoryCredentialBackend())
    credentials.save("primary", "key-1")
    credentials.save("backup", "key-2")
    calls: list[str] = []

    router = ChannelRouterTranslator(
        database,
        credentials,
        lambda channel, _key: FakeTranslator(channel.id, calls, fail=channel.id == "primary"),
    )
    result = asyncio.run(router.translate(["hello"], "en", "zh-Hans"))

    assert result == ["backup:hello"]
    assert calls == ["primary", "backup"]
    assert "failed" in (database.get_api_channel("primary").last_error or "")


def test_round_robin_rotates_channels_with_same_priority(tmp_path: Path) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    database.upsert_api_channel(_channel("a", 10, strategy="round_robin"))
    database.upsert_api_channel(_channel("b", 10, strategy="round_robin"))
    credentials = CredentialStore(MemoryCredentialBackend())
    credentials.save("a", "key-a")
    credentials.save("b", "key-b")
    calls: list[str] = []
    router = ChannelRouterTranslator(database, credentials, lambda channel, _key: FakeTranslator(channel.id, calls))

    asyncio.run(router.translate(["one"], "en", "zh-Hans"))
    asyncio.run(router.translate(["two"], "en", "zh-Hans"))

    assert calls == ["a", "b"]


def test_rate_limiter_waits_when_rpm_window_is_full() -> None:
    now = 0.0
    waits: list[float] = []

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        nonlocal now
        waits.append(seconds)
        now += seconds

    limiter = AsyncRateLimiter(1, window_seconds=60, clock=clock, sleep=sleep)

    async def scenario() -> None:
        await limiter.acquire()
        await limiter.acquire()

    asyncio.run(scenario())
    assert waits == [60]


def test_router_recreates_loop_bound_resources_when_event_loop_changes(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "app.db")
    database.initialize()
    database.upsert_api_channel(_channel("primary", 10))
    credentials = CredentialStore(MemoryCredentialBackend())
    credentials.save("primary", "key-1")
    created: list[FakeTranslator] = []

    def factory(channel, _key):
        translator = FakeTranslator(channel.id, [])
        created.append(translator)
        return translator

    router = ChannelRouterTranslator(database, credentials, factory)

    asyncio.run(router.translate(["one"], "en", "zh-Hans"))
    asyncio.run(router.translate(["two"], "en", "zh-Hans"))

    assert len(created) == 2
