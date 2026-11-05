"""The contest's floor rules and store rules (user, 5 Nov 2026: "strictly ensure these are followed for associate
side and department manager side"). The 3 lines / 3 buttons / voice-first footer are checked on every message of
every test (conftest.floor_rules); here: the first reply in under 5 seconds (the holding line), store rule 3 (never
guess wealth from appearance; the associate sets the start level) and store rule 4 (the associate decides; Store IQ
recommends and gives the reason)."""

from __future__ import annotations

import asyncio

from chanakya.core import repo
from chanakya.i18n.lang import t
from chanakya.sim.harness import PHONES
from tests.helpers import button_ids, to_options


async def test_first_reply_holding_line_when_slow(h, monkeypatch):
    """Nothing back by the hold time (a voice note being heard, a card being drawn): one short line goes first, the
    full reply follows, and the reply time logged is the holding line's."""
    from chanakya.core import router

    monkeypatch.setattr(h.rt.svc.settings, "first_reply_hold_s", 0.15)
    real = router.dispatch

    async def slow(ctx):
        await asyncio.sleep(0.6)
        return await real(ctx)

    monkeypatch.setattr(router, "dispatch", slow)
    out = await h.tap("g:new")
    holds = {t(k, lg) for k in ("hold_voice", "hold_photo", "hold_wait") for lg in ("hi", "mr", "en")}
    assert out[0].body in holds and out[0].to == PHONES["associate"]
    assert len(out) >= 2 and out[1].body not in holds  # then the real reply
    lat = [e for e in await repo.events_for(types=["latency"]) if (e.payload or {}).get("hold")]
    assert lat and lat[-1].payload["ms"] < 5000


async def test_fast_reply_sends_no_holding_line(h):
    out = await h.tap("g:new")
    holds = {t(k, lg) for k in ("hold_voice", "hold_photo", "hold_wait") for lg in ("hi", "mr", "en")}
    assert not any(m.body in holds for m in out)


async def test_appearance_never_sets_the_start_level(h, monkeypatch):
    """Store rule 3: a note about the customer's car, clothes or looks never becomes a budget, even when the model
    reads one into it; a budget said as a budget does. The associate sets the start level."""
    from chanakya.ai.rules import explicit_budget
    from chanakya.ai.schemas import NumberPair, Understanding

    assert not explicit_budget("customer bahut ameer lagte hain, BMW se aaye, budget 2 lakh hoga")
    assert not explicit_budget("branded kapde pehne hain, range upar ki dikhao")
    assert explicit_budget("budget around pachaas hazaar") and explicit_budget("50 hazaar tak ka chahiye")
    await h.tap("g:new")
    await h.tap("s:C1:cat:tv")

    out = await h.say("customer bahut ameer lagte hain, BMW se aaye, budget 2 lakh hoga")  # the front guard
    assert "Poochhiye" in out[0].body  # 'I don't guess from appearance. Ask: how much have they thought of?'

    async def reads_wealth(text, audio, ctx, mime=None):  # behind it: a model that read a budget into a note
        return Understanding(transcript=text, category="tv", slots=[],
                             numbers=[NumberPair(name="budget_inr", value=200000)], intents=["customer_info"])

    monkeypatch.setattr(h.rt.ai, "understand", reads_wealth)
    await h.say("do bachche hain, naya flat 2 lakh mein sajaya")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.start_level_inr is None and s.home.start_level_source == "none"
    evs = [e.type for e in await repo.events_for(s.id)]
    assert "guard_looks" in evs and "budget_ignored" in evs
    monkeypatch.undo()
    await h.say("unhone kaha budget 50 hazaar tak ka hai")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.start_level_inr == 50000 and s.home.start_level_source == "associate_voice"


async def test_the_associate_decides(h):
    """Store rule 4: three options, each with its reason; nothing is chosen, billed or sent to the customer until
    the associate taps."""
    out = await to_options(h)
    pages = [m for m in out if m.card is not None and m.card.template == "product"]
    assert len(pages) >= 2
    for m in pages:
        lines = (m.caption or m.body or "").split("\n")
        assert len(lines) == 3 and lines[1].startswith("*Kyun*: ")  # name, the reason, the offer
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.chosen_sku is None and not s.cost_snapshot and not s.offer_snapshot
    assert not any(m.to == PHONES["customer"] for m in out)
    assert any(i.startswith("s:C1:") for i in button_ids(out))  # the next step is the associate's tap
    await h.tap("s:C1:shown")
    await h.tap("s:C1:opt:B")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.chosen_sku == s.option_labels["B"]
