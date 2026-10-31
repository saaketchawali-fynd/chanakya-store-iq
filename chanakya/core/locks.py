"""Per-wa_id asyncio locks: messages from one sender are processed sequentially."""

from __future__ import annotations

import asyncio
from collections import defaultdict

_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def for_(wa_id: str) -> asyncio.Lock:
    return _locks[wa_id]


def reset() -> None:
    _locks.clear()
