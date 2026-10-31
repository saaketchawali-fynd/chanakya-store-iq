"""Shared fixtures: an isolated SQLite DB per harness (TEST_DATABASE_URL to use Postgres), FakeChannel,
AI_MODE=fixture, frozen demo clock (Mon 2 Nov 2026, 11:00 IST)."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from chanakya.core import clock, router
from chanakya.sim.harness import Harness

FROZEN = datetime(2026, 11, 2, 11, 0, tzinfo=clock.IST)


def db_url(tmp: Path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp / f'test_{uuid.uuid4().hex[:8]}.db'}"


async def make_harness(tmp: Path, **kw) -> Harness:
    clock.freeze(FROZEN)
    clock.set_offset(clock.offset() * 0)
    router._unknown_replied.clear()
    return await Harness.create(db_url(tmp), **kw)


@pytest.fixture
async def h(tmp_path):
    hh = await make_harness(tmp_path)
    yield hh
    await hh.close()


@pytest.fixture(scope="session")
async def rt(tmp_path_factory):
    """Read-only runtime (facts + packs) for unit tests of the engines."""
    hh = await make_harness(tmp_path_factory.mktemp("rt"))
    yield hh.rt
    await hh.close()
