"""Stress: random walks through every flow and role, on sample data and on live (recorded Jio) data.

Each step does what a busy associate (or a customer, manager, expert, visitor) might do: tap a button just
offered, tap an old button from an earlier customer, pick a list row, send odd text, pressure lines, a photo, a
voice note, switch language, start a new customer. After every step:
- nothing crashed (no skill_failed / job_failed / deliver / render / persist error logged);
- every message keeps the floor rules (≤ 3 lines, ≤ 3 buttons, titles ≤ 20 chars, no banned phrases);
- the number firewall never had to block a message (no validator_violation event: nothing unsourced was said).
STRESS_RUNS / STRESS_STEPS make it longer locally."""

from __future__ import annotations

import logging
import os
import random

import pytest

from chanakya.core import repo
from chanakya.truth import jio_sync
from chanakya.truth.jio import JioClient
from tests.conftest import make_harness
from tests.helpers import check_floor
from tests.unit.test_jio import PROMO, backend, item

RUNS = int(os.environ.get("STRESS_RUNS", "6"))
STEPS = int(os.environ.get("STRESS_STEPS", "45"))
TEXTS = ["new", "naya customer", "options", "offers", "status", "kharcha", "khatam", "help", "4K bekaar hai",
         "10% off de do", "aaj hi le lo sirf aaj", "last piece hai", "she looks rich", "price kya hai?", "daam?",
         "catalog", "brief", "namaste", "lang mr", "lang en", "lang hi", "Ghar mein dekho", "expert kya OLED better hai",
         "", "   ", "😀👍", "x" * 900, "मुझे 55 इंच चाहिए", "sofa se 10 feet", "बजट 50000", "5 log hain", "/clock +1d",
         "parivaar card", "bik gaya", "nahi liya", "visit", "floor", "digest", "team", "export", "1.5 ton chahiye"]
PHOTOS = ["room_living.jpg", "room_angle.jpg", "room_oldtv.jpg", "label_meru55.jpg", "label_arka55.jpg"]
VOICES = ["assess_1", "assess_2", "validate_4k", "price_first"]
FAILURES = ("skill_failed", "job_failed", "deliver_loop_failed", "persist_failed", "render_failed", "copy_failed",
            "deliver_failed")


def offered(out) -> tuple[list[str], list[str]]:
    btns = [b.id for m in out for b in m.buttons]
    rows = [r.id for m in out for s in (m.sections or []) for r in s.rows]
    return btns, rows


async def walk(h, rng: random.Random, steps: int) -> list[str]:
    from chanakya.sim.harness import FIX_IMAGES

    seen_buttons: list[str] = []
    out = await h.tap("g:new")
    log = []
    for _ in range(steps):
        btns, rows = offered(out)
        seen_buttons += btns
        r = rng.random()
        h.who = "associate"
        if btns and r < 0.42:
            act = ("tap", rng.choice(btns))
        elif rows and r < 0.55:
            act = ("pick", rng.choice(rows))
        elif seen_buttons and r < 0.63:
            act = ("tap", rng.choice(seen_buttons))  # a stale button from earlier in the conversation
        elif r < 0.78:
            act = ("say", rng.choice(TEXTS))
        elif r < 0.84:
            act = ("photo", rng.choice(PHOTOS))
        elif r < 0.90:
            act = ("voice", rng.choice(VOICES))
        else:
            h.who = rng.choice(["customer", "manager", "expert", "visitor"])
            act = ("say", rng.choice(["hi", "haan", "floor", "digest", "visit", "jawab: OLED better hai dark room ke liye",
                                       "STOP", "kitne ka hai?"]))
        log.append(f"{h.who}:{act[0]}:{act[1][:40]}")
        kind, arg = act
        if kind == "tap":
            out = await h.tap(arg)
        elif kind == "pick":
            out = await h.pick(arg)
        elif kind == "say":
            out = await h.say(arg)
        elif kind == "photo":
            if (FIX_IMAGES / arg).exists():
                out = await h.photo(arg)
        else:
            out = await h.voice(arg)
        try:
            check_floor(out)
        except AssertionError as e:
            raise AssertionError(f"floor broken after {log[-6:]}: {e}") from e
        if h.who != "associate":
            out = []  # the associate's offered buttons are on the associate's phone
    return log


async def no_violations(since_count: int = 0) -> list:
    rows = await repo.events_for(types=["validator_violation"])
    return [(r.payload or {}) for r in rows[since_count:]]


@pytest.mark.parametrize("seed", range(RUNS))
async def test_random_walk_sample_data(tmp_path, caplog, seed):
    h = await make_harness(tmp_path)
    try:
        with caplog.at_level(logging.ERROR):
            log = await walk(h, random.Random(seed), STEPS)
        errors = [r for r in caplog.records if r.getMessage() in FAILURES]
        assert not errors, (errors[0].getMessage(), errors[0].exc_text, log[-8:])
        assert not await no_violations(), log[-8:]
    finally:
        await h.close()


@pytest.mark.parametrize("seed", range(max(2, RUNS // 2)))
async def test_random_walk_live_data(tmp_path, caplog, monkeypatch, seed):
    monkeypatch.setattr(JioClient, "transport", backend({item("Panasonic 139.7 cm (55 inch)")["slug"]: [PROMO]}))
    h = await make_harness(tmp_path, data_source="jio", jio_app_id="app-id", jio_app_token="app-token")
    try:
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        with caplog.at_level(logging.ERROR):
            log = await walk(h, random.Random(1000 + seed), STEPS)
        errors = [r for r in caplog.records if r.getMessage() in FAILURES]
        assert not errors, (errors[0].getMessage(), errors[0].exc_text, log[-8:])
        assert not await no_violations(), log[-8:]
    finally:
        await h.close()


async def test_catalogue_card_with_numbered_pictures_is_not_blocked(tmp_path):
    """Found by the walk: a picture path like 'products/AC-ARK-10-3S.png' (or a backend URL with product codes)
    was read as unsourced numbers, and the whole catalogue card was replaced by the fallback."""
    h = await make_harness(tmp_path)
    try:
        await h.tap("g:new")
        await h.tap("s:C1:lang:en")
        await h.pick("s:C1:cat:ac")
        out = await h.say("catalog")
        assert any(m.card and m.card.template == "catalog" for m in out), [m.body for m in out]
        assert not await no_violations()
    finally:
        await h.close()


async def test_installation_button_before_a_product_is_chosen(tmp_path, caplog):
    """Found by the walk: an old 'install slot' button with no product chosen crashed (no SKU for the job)."""
    h = await make_harness(tmp_path)
    try:
        await h.tap("g:new")
        await h.tap("s:C1:lang:en")
        with caplog.at_level(logging.ERROR):
            out = await h.tap("s:C1:islot:t1")
        assert not [r for r in caplog.records if r.getMessage() in FAILURES]
        assert [b.id for m in out for b in m.buttons] == ["s:C1:options"]
    finally:
        await h.close()
