"""Flow control: menu, end the current customer, end-and-start-new, start over from the very beginning."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.sim.harness import PHONES
from tests.helpers import button_ids, check_floor, open_brief, texts


def rows(msg):
    return [r.id for s in msg.sections for r in s.rows]


async def start_customer(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:mr")
    return await h.voice("assess_short")


async def test_coach_replies_carry_a_menu_hint(h):
    await start_customer(h)
    out = await h.tap("s:C1:slot:trigger:new_home")
    coach = next(m for m in out if m.coach)
    assert coach.footer.startswith("Bolkar bhi: voice note") and coach.footer.endswith("menu") and len(coach.footer) <= 60


async def test_menu_lists_new_end_restart(h):
    out = await h.say("menu")
    assert out[0].kind == "list" and rows(out[0])[0] == "g:new" and "g:restart" in rows(out[0])
    await start_customer(h)
    out = await h.say("madad")
    check_floor(out)
    r = rows(out[0])
    assert r[:3] == ["g:new", "g:exitnew", "s:C1:khatam"] and "g:restart" in r and "g:langpick" in r


async def test_exit_confirms_then_ends_current_customer(h):
    await start_customer(h)
    out = await h.say("band")
    assert button_ids(out) == ["g:exitok", "s:C1:khatam", "s:C1:go"] and "C1" in out[0].body
    out = await h.tap("g:exitok")
    check_floor(out)
    assert button_ids(out)[0] == "g:new"
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.step.value == "closed" and s.extra["outcome"] == "exited"
    assert (await repo.staff_by_wa(PHONES["associate"])).current_session_id is None
    out = await h.say("status")
    assert "s:C1" not in " ".join(button_ids(out))


async def test_end_and_start_new_customer(h):
    await start_customer(h)
    out = await h.tap("g:exitnew")
    assert "C1" in out[0].body and rows(out[-1])[0] == "s:C2:cat:tv"  # the new customer's opener
    assert (await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")).step.value == "closed"


async def test_start_over_from_the_very_beginning(h):
    await start_customer(h)
    await h.tap("g:new")  # a second open customer (C2)
    out = await h.say("shuru se")
    assert button_ids(out) == ["g:restartok", "g:status"] and "2" in out[0].body
    out = await h.tap("g:restartok")
    assert [b.id for b in out[0].buttons] == ["g:alang:en", "g:alang:hi", "g:alang:mr"]  # step 0: language
    assert await h.rt.svc.sessions.active(PHONES["associate"]) == []
    out = await open_brief(h, await h.tap("g:alang:en"))  # then the brief (its product first), in English
    assert any(m.card and m.card.template == "brief" and m.card.lang == "en" for m in out)
    out = await h.tap("g:new")
    assert "C3" in texts(out) or "C1" in texts(out)
