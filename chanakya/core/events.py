"""Event helpers (spec 7.9) and timing."""

from __future__ import annotations

import time
from contextlib import contextmanager

from chanakya.core.contracts import Event


def ev(
    type: str,
    session_id: str | None = None,
    actor: str | None = None,
    step: str | None = None,
    sources: list[str] | None = None,
    latency_ms: int | None = None,
    **payload,
) -> Event:
    return Event(
        type=type,
        session_id=session_id,
        actor_wa_id=actor,
        step=step,
        payload=payload,
        sources=sources or [],
        latency_ms=latency_ms,
    )


@contextmanager
def timer():
    t0 = time.perf_counter()
    box = {"ms": 0}
    try:
        yield box
    finally:
        box["ms"] = int((time.perf_counter() - t0) * 1000)
