"""After the sale, and old buttons: found by the flow audit on 2 Nov 2026 and fixed.

- Session codes (C1, C2 …) restart every day: a button always acts on the customer it was sent for.
- Follow-through keeps working after the conversation is scored: task reminders, a call, re-booking an install.
- The lost reason is captured; an expert's answer goes to the question they picked; 'r …' reaches the customer who
  wrote; offers never skip the full cost; an installation follows the delivery day and asks what the installer
  needs."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.sim.harness import PHONES
from tests.helpers import button_ids, pay, pick, to_options


async def session(h, code="C1"):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], code)


async def to_consent(h):
    """Golden path to a booked delivery and a linked customer (QR consent)."""
    await to_options(h)
    await h.tap("s:C1:shown")
    await pick(h)
    await pay(h)
    out = await h.tap("s:C1:delivery")
    await h.tap(button_ids(out)[0])
    await h.tap("s:C1:qr")
    s = await session(h)
    h.as_("customer")
    out = await h.say(f"CHANAKYA {s.optin_token}")
    h.as_("associate")
    return out


async def test_reminder_buttons_act_on_their_own_customer_next_day(h):
    await to_consent(h)
    await h.tap("s:C1:task")
    await h.tap("s:C1:task_when:tomorrow")
    await h.tap("s:C1:khatam")
    await h.tap("s:C1:islot:t1")  # sold → install → scored and closed
    shah = await session(h)
    out = await h.clock("+1d")  # the reminder arrives
    rem = next(m for m in out if m.to == PHONES["associate"] and m.buttons)
    await h.tap("g:new")  # the same day: a new customer gets the code C1 again
    await h.tap("s:C1:lang:en")
    assert len(rem.buttons) == 3  # [Customer ko bhejo] [Ho gaya ✓] [Kal yaad dilao]
    done = next(b.id for b in rem.buttons if ":taskdone:" in b.id)
    out = await h.tap(done)
    assert "shabash" in out[0].body.lower() or "done" in out[0].body.lower()
    assert not [t for t in await repo.tasks_open(PHONES["associate"]) if t.session_id == shah.id]
    today = await session(h)
    assert today.id != shah.id and today.step.value != "closed"  # the new customer was not touched


async def test_old_button_from_a_finished_customer_says_so(h):
    await to_consent(h)
    await h.tap("s:C1:khatam")
    await h.tap("s:C1:islot:t1")
    out = await h.tap("s:C1:options")  # a sales button after the conversation was scored
    assert "earlier customer" in out[0].body.lower() or "pichhle customer" in out[0].body.lower()
    assert button_ids(out) == ["g:new", "g:status"]


async def test_follow_through_after_install_booking(h):
    await to_consent(h)
    await h.tap("s:C1:khatam")
    out = await h.tap("s:C1:islot:t1")  # scored and closed
    assert any(m.card and m.card.template == "score" for m in out)
    out = await h.tap("s:C1:task")  # still possible after the score
    assert "s:C1:task_when:tomorrow" in button_ids(out)
    out = await h.tap("s:C1:task_when:tomorrow")
    assert out and "samajh nahi aaya" not in out[0].body


async def test_lost_reason_is_captured(h):
    await to_options(h)
    await h.tap("s:C1:khatam")
    await h.tap("s:C1:outcome:lost")
    out = await h.tap("s:C1:pricefirst:customer")
    assert any("kyun" in (m.body or "").lower() or "why" in (m.body or "").lower() for m in out)
    out = await h.say("customer ko price zyada laga")
    assert "samajh nahi aaya" not in out[0].body
    s = await session(h)
    assert s.awaiting is None and await repo.events_for(s.id, ["lost_reason"])


async def test_reply_goes_to_the_customer_who_wrote(h):
    await to_consent(h)
    h.as_("customer")
    out = await h.say("Sunday ko delivery ho sakti hai?")
    fwd = next(m for m in out if m.to == PHONES["associate"])
    assert fwd.body.startswith("[C1]")
    h.as_("associate")
    await h.tap("g:new")  # another customer becomes current
    out = await h.say("r Haan, Sunday ko ho jayegi")
    assert any(m.to == PHONES["customer"] and "Sunday" in m.body for m in out)


async def test_offers_never_skip_the_full_cost(h):
    await to_options(h)
    await h.tap("s:C1:shown")
    out = await h.tap("s:C1:offers")  # 'offers' before a pick and the cost card
    assert not any(m.card and m.card.template == "offer" for m in out)
    s = await session(h)
    assert s.chosen_sku is None or (s.cost_snapshot or {}).get("sku") == s.chosen_sku


async def test_install_slots_follow_the_delivery_day(h):
    await to_options(h)
    await h.tap("s:C1:shown")
    await pick(h)
    await pay(h)
    out = await h.tap("s:C1:delivery")
    later = button_ids(out)[1]  # the second day offered
    await h.tap(later)
    await h.tap("s:C1:khatam")
    out = await h.tap("s:C1:islot:t1")
    s = await session(h)
    job = await repo.install_get(s.extra["install_job_id"])
    assert job.slot_date.isoformat() >= later.split(":")[-1]  # never before the delivery


async def test_expert_answer_goes_to_the_question_they_picked(h):
    await to_options(h)
    await h.say("expert Meru 55 mein Dolby Vision hai?")
    await h.say("expert Arka 55 ka warranty kitna hai?")
    qs = await repo.expert_q_open()
    second = max(q.id for q in qs)
    h.as_("expert")
    await h.tap(f"x:{second}:answer")
    out = await h.say("Arka 55 par 1 saal ki warranty hai")
    to_assoc = next(m for m in out if m.to == PHONES["associate"])
    assert "warranty" in to_assoc.body.lower() and "Dolby" not in to_assoc.body
    q = await repo.expert_q_get(second)
    assert q.status == "answered"
