from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from itertools import groupby
from typing import Protocol

from app.domain.models import ApiChannelRecord
from app.security.credentials import CredentialStore
from app.security.redaction import redact_text
from app.storage.database import Database

from .openai_compatible import TranslationServiceError


class Translator(Protocol):
    async def translate(self, texts: list[str], source_language: str, target_language: str) -> list[str]: ...


TranslatorFactory = Callable[[ApiChannelRecord, str], Translator]


class AsyncRateLimiter:
    def __init__(
        self,
        limit: int,
        *,
        window_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.limit = max(1, limit)
        self.window_seconds = window_seconds
        self.clock = clock
        self.sleep = sleep
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self.clock()
                cutoff = now - self.window_seconds
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.limit:
                    self._timestamps.append(now)
                    return
                await self.sleep(max(0, self.window_seconds - (now - self._timestamps[0])))


class ChannelRouterTranslator:
    def __init__(self, database: Database, credentials: CredentialStore, factory: TranslatorFactory) -> None:
        self.database = database
        self.credentials = credentials
        self.factory = factory
        self._round_robin_cursor: defaultdict[int, int] = defaultdict(int)
        self._translators: dict[str, Translator] = {}
        self._limiters: dict[str, AsyncRateLimiter] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    async def translate(self, texts: list[str], source_language: str, target_language: str) -> list[str]:
        channels = self._ordered_channels()
        if not channels:
            raise TranslationServiceError("没有已启用且验证通过的API通道")
        failures: list[str] = []
        for channel in channels:
            key = self.credentials.load(channel.id)
            if not key:
                message = "未保存API Key"
                self.database.set_api_channel_health(channel.id, verified=False, last_error=message)
                failures.append(f"{channel.service_name}: {message}")
                continue
            translator = self._translators.setdefault(channel.id, self.factory(channel, key))
            limiter = self._limiters.setdefault(channel.id, AsyncRateLimiter(channel.rpm_limit))
            semaphore = self._semaphores.setdefault(channel.id, asyncio.Semaphore(max(1, channel.max_concurrency)))
            try:
                await limiter.acquire()
                async with semaphore:
                    result = await translator.translate(texts, source_language, target_language)
            except TranslationServiceError as error:
                message = redact_text(str(error))
                self.database.set_api_channel_health(channel.id, verified=True, last_error=message)
                failures.append(f"{channel.service_name}: {message}")
                continue
            self.database.set_api_channel_health(channel.id, verified=True, last_error=None)
            return result
        raise TranslationServiceError("所有API通道均失败：" + "；".join(failures))

    def _ordered_channels(self) -> list[ApiChannelRecord]:
        verified = [channel for channel in self.database.list_api_channels(enabled_only=True) if channel.verified]
        ordered: list[ApiChannelRecord] = []
        for priority, grouped in groupby(verified, key=lambda channel: channel.priority):
            group = list(grouped)
            if len(group) > 1 and any(channel.strategy == "round_robin" for channel in group):
                cursor = self._round_robin_cursor[priority] % len(group)
                group = group[cursor:] + group[:cursor]
                self._round_robin_cursor[priority] += 1
            ordered.extend(group)
        return ordered

    async def close(self) -> None:
        for translator in self._translators.values():
            close = getattr(translator, "close", None)
            if close is not None:
                await close()
