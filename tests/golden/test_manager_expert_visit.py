"""The contest's extras, end to end (user, 5 Nov 2026: "the department manager view, 'Ask an expert', and a store
visit log by voice note ... Ensure its seamless"): the manager's huddle, floor, tip, profile, voice log and
approvals; the expert's queue, the question with its product facts, the clean answer and the reminder; the visit
log's home, rubric scoring, scorecard and visits so far. In English, Hinglish and Marathi."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from chanakya.core import clock, repo
from chanakya.sim.harness import PHONES
from tests.helpers import answer_complete, button_ids, check_floor, texts, to_options

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")


def rows(msgs) -> list[str]:
    return [r.id for m in msgs for s in m.sections for r in s.rows]


def first(msgs, prefix: str) -> str:
    return next(i for i in button_ids(msgs) + rows(msgs) if i.startswith(prefix))


def clean(msgs) -> None:
    check_floor(msgs)
    assert not EMOJI.search(texts(msgs)), texts(msgs)


async def a_day(h, live: bool = True):
    """One scored customer (price first marked at the end), and one still on the floor."""
    h.as_("associate")
    await to_options(h)
    await h.tap("s:C1:demo")
    await h.tap("s:C1:shown")
    await answer_complete(h, await h.tap("s:C1:opt:B"))
    await h.tap("s:C1:khatam")
    await h.tap("s:C1:outcome:lost")
    await h.tap("s:C1:pricefirst:customer")
    if live:
        await h.tap("g:new")


@pytest.mark.parametrize("lang", ["en", "hi", "mr"])
async def test_manager_huddle_floor_tip_profile(h, lang):
    await a_day(h)
    h.as_("manager")
    await h.tap(f"g:alang:{lang}")
    out = await h.say("menu")
    clean(out)
    assert out[0].kind == "list" and not out[0].footer.startswith(("Heard", "Suna"))  # the menu, never "Heard: menu"
    assert "m:visits" in rows(out) and "m:floor" in rows(out)

    out = await h.pick("m:digest")  # the huddle: the answer in words first, then the card (funnel, team, customers)
    clean(out)
    assert out[0].card is None and out[1].card.template == "digest"
    d = out[1].card.data
    assert len(d["funnel"]) == 5 and len(d["kpis"]) == 4 and d["team"] and d["customers"]
    assert {k["sub"].split()[-1] for k in d["kpis"] if k.get("sub")} == {"6", "6/6", "2"}  # October beside the day
    assert all(len(t["low"]) <= 9 for t in d["team"])  # the weakest step's short name: 'Demo', never 'Demonstrate'
    assert not any("*" in str(v) for v in d.values())

    out = await h.pick("m:floor")  # over the shoulder: who is with which customer
    clean(out)
    fc = first(out, "m:floorc:")
    out = await h.pick(fc)
    clean(out)
    tip = first(out, "m:tip:")
    out = await h.tap(tip)
    clean(out)
    to_assoc = [m for m in out if m.to == PHONES["associate"]]
    assert to_assoc and "g:clip:assess" in button_ids(to_assoc)  # the whisper, with the step's clip

    out = await h.pick("m:team")
    out = await h.pick(first(out, "m:profile:"))  # the associate's profile
    clean(out)
    ids = button_ids(out)
    assert any(i.startswith("m:nudge:") for i in ids) and any(i.startswith("m:whov:") for i in ids)
    assert len(out[0].body.split("\n")) == 3  # today, the steps, the wins: 3 lines

    out = await h.pick("m:vlog")
    out = await h.pick(first(out, "m:vnote:"))  # plain words: no raw ids
    clean(out)
    assert "ask_question" not in out[0].body and "=" not in out[0].body


async def test_approval_carries_product_price_offers(h):
    await to_options(h)
    await h.tap("s:C1:demo")
    await h.tap("s:C1:shown")
    await answer_complete(h, await h.tap("s:C1:opt:B"))
    await h.say("customer 10% discount maang raha hai")
    await h.tap("s:C1:askmgr")
    h.as_("manager")
    await h.tap("g:alang:en")
    out = await h.pick("m:approvals")
    clean(out)
    assert "*Product:*" in out[0].body and "₹" in out[0].body and "*Live offers:*" in out[0].body
    out = await h.tap(first(out, "m:appr:"))
    clean(out)
    mine = next(m for m in out if m.to == PHONES["manager"])
    assert mine.body.startswith("*Done.*") and "> " in mine.body  # exactly what the associate was told
    assert any(m.to == PHONES["associate"] for m in out)


@pytest.mark.parametrize("lang", ["en", "hi", "mr"])
async def test_expert_queue_answer_library_reminder(h, lang):
    await to_options(h)
    await h.tap("s:C1:demo")
    out = await h.voice("curve_unknown_spec")
    q_msg = next(m for m in out if m.to == PHONES["expert"])
    # the product asked about (the Pro, not the plain QLED that shares its name) with its facts from the data
    assert "*Product:* Meru 55\" QLED Pro" in q_msg.body and "Refresh rate: 120 Hz" in q_msg.body

    h.as_("expert")
    out = await h.tap(f"g:alang:{lang}")  # the queue, not "no open question"
    clean(out)
    qid = first(out, "x:").split(":")[1]
    out = await h.pick(f"x:{qid}:answer")
    clean(out)
    assert "> " in out[0].body
    out = await h.say("Nahi, Dolby Vision nahi hai, HDR10 aur HLG hai. Say: Isme HDR10 aur HLG hai, Dolby Vision nahi.")
    clean(out)
    to_assoc = next(m for m in out if m.to == PHONES["associate"])
    assert to_assoc.body.startswith("*Expert") and "\n_" in to_assoc.body and "Dolby Vision nahi." in to_assoc.body  # answer, Bolo, the question
    assert {"s:C1:go", "s:C1:expfollow"} <= set(button_ids([to_assoc]))
    out = await h.tap(f"x:{qid}:save")
    clean(out)
    out = await h.tap("x:0:list")
    clean(out)

    h.as_("associate")
    out = await h.voice("curve_unknown_spec")  # the library answers, and says so
    clean(out)
    assert "s:C1:expforce" in button_ids(out)
    out = await h.tap("s:C1:expforce")
    assert any(m.to == PHONES["expert"] for m in out)  # asked anyway

    from chanakya.skills import expert as X

    sent = []

    async def send(m):
        sent.append(m)

    await X.sla_tick(clock.now() + timedelta(seconds=120), send)
    rem = next(m for m in sent if m.to == PHONES["expert"])
    assert rem.kind == "buttons" and "> " in rem.body and any(":answer" in b.id for b in rem.buttons)


@pytest.mark.parametrize("lang", ["en", "hi", "mr"])
async def test_visit_log_scored_by_the_rubric(h, lang):
    h.as_("visitor")
    out = await h.tap(f"g:alang:{lang}")
    clean(out)
    assert {"v:0:new:1", "v:0:sum:1", "v:0:export:1"} <= set(button_ids(out))
    out = await h.say("namaste")  # a greeting is not a visit
    assert await repo.visit_draft(PHONES["visitor"]) is None
    out = await h.tap("v:0:new:1")
    out = await h.say("Juhu store, TV scenario, associate ne pehle price bola minute 2 pe, demo nahi dikhaya, "
                      "Ravi ne 3 sawaal pooche, wapas nahi aaunga")
    clean(out)
    card = out[0].card
    assert card.template == "visit"
    sc = {r["name"]: r["score"] for r in card.data["rows"]}
    assert list(sc.values()).count(None) == 4 and card.data["total"] == "1"  # close 0, demo 0, assess 1
    assert "Ravi" not in str(card.data)
    d = await repo.visit_draft(PHONES["visitor"])
    assert d.entry["minute_first_price"] == 2 and d.entry["would_come_back"] is False
    asked = 0
    while True:
        ids = button_ids(out)
        if any(":save:" in i for i in ids):
            break
        assert any(":s_" in i for i in ids)  # the missing steps, 0 / 1 / 2 with the rubric line
        out = await h.tap(ids[2])
        asked += 1
    assert asked == 3
    out = await h.tap(next(i for i in button_ids(out) if ":save:" in i))
    clean(out)
    out = await h.tap("v:0:sum:1")
    clean(out)
    v = out[0].card
    assert v.template == "visits" and len(v.data["table"]) == 7 and len(v.data["steps"]) == 7
    out = await h.tap("v:0:export:1")
    assert out[0].kind == "document" and out[0].caption.startswith("*")

    h.as_("manager")
    out = await h.pick("m:visits")  # the manager sees the same card
    assert out[0].card.template == "visits"
