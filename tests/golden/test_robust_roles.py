"""Edge cases judges try on the manager, expert and visit-log journeys (user, 6 Nov 2026: "make it robust from
testing around many edge cases by the judges"). Each test is a case a probe found going wrong."""

from __future__ import annotations

import re

from chanakya.core import repo
from chanakya.sim.harness import PHONES
from chanakya.skills.visitlog import scrub_pii
from tests.helpers import check_floor


# ---------------------------------------------------------------- the visit log: no names, no numbers, no nonsense
def test_names_and_numbers_are_scrubbed():
    t = scrub_pii("associate ravi sharma ne pehle price bola, apna number diya 9876543210, +91 98200 12345")
    assert "ravi" not in t.lower() and "9876543210" not in t and "98200" not in t and "[number]" in t
    assert "९८७६५४३२१०" not in (scrub_pii("नंबर ९८७६५४३२१०") or "")


async def test_a_visit_keeps_no_name_or_phone(h):
    h.as_("visitor")
    await h.tap("v:0:new:1")
    out = await h.say("Andheri store gaya TV ke liye, associate ravi sharma ne pehle price bola minute 1 pe, "
                      "apna number diya 9876543210, demo nahi dikhaya, wapas nahi aaunga")
    check_floor(out)
    d = await repo.visit_draft(PHONES["visitor"])
    assert "ravi" not in str(d.entry).lower() and "9876543210" not in str(d.entry)
    from sqlalchemy import select

    from chanakya.truth import db
    from chanakya.truth.models import MessageRow

    async with db.session() as s:  # the stored message too: no number, no name
        rows = list((await s.execute(select(MessageRow).where(MessageRow.wa_id == PHONES["visitor"]))).scalars())
    assert rows and all("9876543210" not in str(r.payload) and "ravi" not in str(r.payload).lower() for r in rows)


async def test_visit_scores_never_contradict_the_note(h):
    h.as_("visitor")
    await h.tap("v:0:new:1")
    await h.say("Juhu store, TV, demo nahi dikhaya, 2 sawaal pooche")
    d = await repo.visit_draft(PHONES["visitor"])
    demo = next((s for s in d.entry.get("steps") or [] if s["step"] == "demonstrate"), None)
    assert d.entry["demo_shown"] is False and (demo is None or demo["score"] == 0)
    await h.say("He did not show any demo. I will never come back")
    d = await repo.visit_draft(PHONES["visitor"])
    assert d.entry["demo_shown"] is False and d.entry["would_come_back"] is False


async def test_short_texts_are_not_visits(h):
    h.as_("visitor")
    for said in ("ok", "thanks", "👍", "what's the weather today"):
        out = await h.say(said)
        check_floor(out)
        assert not any(m.card for m in out)  # never a 0/14 card
    assert (await repo.visit_draft(PHONES["visitor"])) is None or not (await repo.visit_draft(PHONES["visitor"])).entry
    out = await h.say("new")  # a new visit, not a scored note
    assert not any(m.card for m in out)
    out = await h.tap("v:0:export:1")  # nothing saved: one line, never an empty file
    assert out[0].kind != "document" and re.search(r"(?i)no saved|koi saved|सेव्ह", out[0].body)


# ---------------------------------------------------------------- the manager: days, names, words
async def _sold_customer(h):
    from tests.helpers import answer_complete, to_options

    h.as_("associate")
    await to_options(h)
    await h.tap("s:C1:demo")
    await h.tap("s:C1:shown")
    await answer_complete(h, await h.tap("s:C1:opt:B"))
    await h.tap("s:C1:khatam")
    await h.tap("s:C1:outcome:lost")
    await h.tap("s:C1:pricefirst:customer")


async def test_a_customer_across_midnight_counts_on_the_day_they_came(h):
    from datetime import timedelta

    from chanakya.core import clock

    clock.advance(timedelta(hours=12, minutes=30))  # Monday 23:30
    h.as_("associate")
    from tests.helpers import to_options

    await to_options(h)
    clock.advance(timedelta(minutes=45))  # Tuesday 00:15: finished and scored
    await h.tap("s:C1:khatam")
    await h.tap("s:C1:outcome:lost")
    await h.tap("s:C1:pricefirst:customer")
    h.as_("manager")
    out = await h.say("aaj kaisa raha")  # Tuesday: nobody came (the score belongs to Monday's customer)
    assert len(out) == 1 and "no customers yet" in out[0].body.lower() or "koi customer nahi" in out[0].body
    out = await h.say("kal ka digest")
    d = next(m.card for m in out if m.card).data
    assert d["funnel"][0]["n"] == "1" and d["kpis"][0]["value_str"] != "–"  # the customer and their score together


async def test_the_floor_never_invents_a_zero(h):
    h.as_("manager")
    out = await h.say("floor")
    assert "median 0" not in out[0].body and "0/14" not in out[0].body


async def test_dates_it_cannot_read_are_said_so(h):
    await _sold_customer(h)
    h.as_("manager")
    for said, word in (("31 Feb ka digest", "calendar"), ("last week ka report", "one day"),
                       ("pichhle saal ka report", "one day")):
        await h.tap("g:alang:en")
        out = await h.say(said)
        assert len(out) == 1 and word in out[0].body.lower(), (said, out[0].body)
    out = await h.say("kal ka nahi aaj ka digest")  # what follows the 'not'
    assert next(m.card for m in out if m.card).data["labels"]["title"] == "Today's huddle"
    out = await h.say("2025-11-02 ka digest")
    assert "2025" in out[0].body  # never read as this year's 2 Nov


async def test_manager_words_and_names(h):
    await _sold_customer(h)
    h.as_("manager")
    await h.tap("g:alang:hi")
    out = await h.say("approvals")
    assert out[0].kind != "list" or "Suna" not in (out[0].footer or "")  # not the menu
    out = await h.say("Ravi's voice notes")
    assert any(r.id.startswith("m:vnote:") for m in out for s in m.sections for r in s.rows) or "Ravi" in out[0].body
    await h.tap("g:alang:mr")
    out = await h.say("रवी कसा आहे")  # the Marathi menu's own example: Ravi's profile
    assert any(b.id.startswith("m:nudge:") or b.id.startswith("m:whov:") for m in out for b in m.buttons)


async def test_an_associate_without_a_name_still_has_a_row(h):
    await repo.staff_update(PHONES["associate"], name="")
    await _sold_customer(h)
    h.as_("manager")
    out = await h.pick("m:team")
    rows = [r for m in out for s in m.sections for r in s.rows]
    assert rows and all(r.title.strip() for r in rows) and any("Associate" in r.title for r in rows)
    out = await h.say("visits")
    assert not any(b.id.startswith("v:0:new") for m in out for b in m.buttons)  # no 'new visit' for the manager


# ---------------------------------------------------------------- the expert: what is an answer, and to what
async def _ask_expert(h):
    from tests.helpers import to_options

    h.as_("associate")
    await to_options(h)
    await h.tap("s:C1:demo")
    await h.voice("curve_unknown_spec")  # 'Does Meru 55 QLED Pro support Dolby Vision?' → the expert
    return (await repo.expert_q_open())[0]


async def test_one_minute_is_not_an_answer(h):
    q = await _ask_expert(h)
    h.as_("expert")
    out = await h.say("ok 1 min, check karta hoon")
    assert not any(m.to == PHONES["associate"] for m in out)  # nothing reaches the associate
    assert (await repo.expert_q_get(q.id)).status == "open"
    out = await h.say("थांबा, बघतो")
    assert not any(m.to == PHONES["associate"] for m in out)
    out = await h.say("done — Dolby Vision nahi hai, HDR10 aur HLG hai")  # starts with a command word: still the answer
    assert any(m.to == PHONES["associate"] and "Dolby Vision" in (m.body or "") for m in out)
    assert (await repo.expert_q_get(q.id)).status == "answered"


async def test_stale_expert_buttons_do_nothing_wrong(h):
    q = await _ask_expert(h)
    h.as_("expert")
    await h.tap(f"x:{q.id}:answer")
    await h.say("Nahi, Dolby Vision nahi hai. HDR10 aur HLG hai.")
    out = await h.tap(f"x:{q.id}:dunno")  # after the answer: never 'expert doesn't know' to the associate
    assert not any(m.to == PHONES["associate"] for m in out)
    assert (await repo.expert_q_get(q.id)).status == "answered"
    await h.tap(f"x:{q.id}:save")
    await h.tap(f"x:{q.id}:save")  # twice: one library entry
    assert len(await repo.library_all(None)) == 1


async def test_with_two_questions_the_expert_is_asked_which(h):
    await _ask_expert(h)
    h.as_("associate")
    await h.say("expert kya Meru 55 QLED Pro mein Bluetooth hai?")
    assert len(await repo.expert_q_open()) == 2
    h.as_("expert")
    out = await h.say("Haan, Bluetooth 5.0 hai")
    assert out[0].kind == "list" and not any(m.to == PHONES["associate"] for m in out)  # which question? never a guess


# ---------------------------------------------------------------- each role stays in its own journey
async def test_cancel_and_restart_stay_in_each_role(h):
    h.as_("manager")
    for said in ("cancel", "band karo", "restart", "phir se"):
        out = await h.say(said)
        assert not any(b.id == "g:new" for m in out for b in m.buttons), said  # never 'Naya customer'
        assert not any(b.id.startswith("g:alang") for m in out for b in m.buttons), said  # language kept
    h.as_("visitor")
    await h.tap("v:0:new:1")
    await h.say("Juhu store, TV, associate ne pehle price bola minute 2 pe")
    assert await repo.visit_draft(PHONES["visitor"]) is not None
    out = await h.say("band karo")
    assert await repo.visit_draft(PHONES["visitor"]) is None  # the unsaved draft dropped, the visit log's home shown
    assert not any(b.id == "g:new" for m in out for b in m.buttons)


async def test_malformed_manager_buttons_never_crash(h):
    h.as_("manager")
    for bid in ("m:appr:abc", "m:nudge:x:bogus", "m:profile", "m:digest:2026-13-40"):
        out = await h.tap(bid)
        check_floor(out)
        assert out and not any(b.id == "g:new" for m in out for b in m.buttons), bid
    evs = await repo.events_for(types=["skill_failed"])
    assert not evs
