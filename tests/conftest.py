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


@pytest.fixture(autouse=True)
def no_remote_images(monkeypatch):
    """Tests never download product pictures from the internet (backend media URLs): offline and repeatable."""
    from chanakya.render import renderer

    async def offline(card):
        return None

    monkeypatch.setattr(renderer, "fetch_images", offline)





@pytest.fixture(autouse=True)
def floor_rules(monkeypatch, request):
    """The contest's floor rules on every message of every test (user, 5 Nov 2026: strictly, for the associate and
    the manager): at most 3 lines and no blank line, at most 3 buttons (a list is one button, ≤ 10 rows), a footer
    within 60 characters, and voice first: every associate or manager message says, in its footer, that it can be
    answered by voice (or what a voice note was understood as). Checked as the skills wrote it, before the
    validator's safety net."""
    from chanakya.channel import limits
    from chanakya.core import validator
    from chanakya.sim.harness import PHONES

    staff = {PHONES.get("associate"), PHONES.get("manager")}
    bad: list[str] = []
    orig = validator.enforce

    def enforce(m, *a, **k):
        for attr in ("body", "caption"):
            v = getattr(m, attr) or ""
            if not v:
                continue
            lines = v.split("\n")
            if len(lines) > limits.FLOOR_MAX_LINES or any(not ln.strip() for ln in lines):
                bad.append(f"{len(lines)} lines: {v[:160]!r}")
        if len(m.buttons) > limits.MAX_BUTTONS:
            bad.append(f"{len(m.buttons)} buttons: {(m.body or '')[:60]!r}")
        if sum(len(s.rows) for s in m.sections) > limits.LIST_ROWS_MAX:
            bad.append(f"list rows: {(m.body or '')[:60]!r}")
        if m.footer and len(m.footer) > limits.FOOTER_MAX:
            bad.append(f"footer {len(m.footer)}: {m.footer!r}")
        if m.to in staff and m.kind in ("text", "buttons", "list", "image") and not m.footer \
                and not (m.body or "").startswith("Choose your language"):
            bad.append(f"no voice footer: {(m.body or m.caption or '')[:80]!r}")
        return orig(m, *a, **k)

    monkeypatch.setattr(validator, "enforce", enforce)
    yield
    if request.node.get_closest_marker("floor_exempt") is None:
        assert not bad, "floor rules broken:\n" + "\n".join(dict.fromkeys(bad))


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
