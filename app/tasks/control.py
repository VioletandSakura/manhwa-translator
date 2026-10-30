from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar


checkpoint_handler: ContextVar[Callable[[], Awaitable[None]] | None] = ContextVar("task_checkpoint", default=None)


async def checkpoint() -> None:
    handler = checkpoint_handler.get()
    if handler is not None:
        await handler()


async def controlled_sleep(seconds: float) -> None:
    """Allow pause/cancel during rate-limit and retry waits, without busy waiting."""
    if checkpoint_handler.get() is None:
        await asyncio.sleep(seconds)
        return
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        await checkpoint()
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(0.1, remaining))
