"""User feedback, 3 Nov 2026:
- the Family Card: desirable and clear (their room with the product, benefits with proof, ONE final number);
- voice first at every step, in hi / en / mr, for the associate and the manager;
- the manager's voice log;
- the copilot: one quiet line when a fact calls for it, once per customer, never an extra message."""

from __future__ import annotations

from chanakya.ai.schemas import Understanding
from chanakya.core import repo
from chanakya.core.orchestrator import VOICE_KINDS
from chanakya.sim.harness import PHONES
from tests.helpers import button_ids, check_floor, pay, pick, to_options


async def session(h):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")


def to_associate(out):
    return [m for m in out if m.to == PHONES["associate"]]


async def family_card(h, photo=False):
    await to_options(h, photo="room_living.jpg" if photo else None)
    if photo:
        await h.tap("s:C1:tryon")
    await h.tap("s:C1:shown")
    await pick(h)
    await pay(h)
    out = await h.tap("s:C1:delivery")
    await h.tap(button_ids(out)[0])
    await h.tap("s:C1:qr")
    s = await session(h)
    h.as_("customer")
    await h.say(f"CHANAKYA {s.optin_token}")
    h.as_("associate")
    out = await h.tap("s:C1:parivaar")
    return next(m for m in out if m.card and m.card.template == "parivaar"), out


async def test_family_card_is_clear_and_has_one_final_number(h):
    fc, out = await family_card(h)
    check_floor(out)
    d = fc.card.data
    s = await session(h)
    assert fc.card.lang == "en" and fc.to == PHONES["customer"]
    assert d["total"] == s.offer_snapshot["total_today_str"]  # the same number as the offer card and the bill
    assert d["title"].startswith("Your new") and "(TEST)" not in d["store_name"]
    assert len(d["points"]) == 3 and all(p["text"] for p in d["points"]) and any(p["proof"] for p in d["points"])
    assert any(r.get("offer") for r in d["ladder"]) and d["steps"][0]["k"] == "Delivery"


async def test_family_card_shows_their_room_with_the_product(h):
    fc, _ = await family_card(h, photo=True)
    d = fc.card.data
    assert d["image_is_photo"] and d["image"].startswith("data:image/jpeg")
    s = await session(h)
    hero = await repo.media_latest(s.id, "tryon_hero")
    assert hero is not None and s.extra["tryon_hero_sku"] == s.chosen_sku


async def test_presale_family_card_never_shows_a_price_before_it_is_known(h):
    await to_options(h)
    await h.tap("s:C1:presale")  # the family will decide; no full cost has been worked out yet
    await h.tap("s:C1:qr")
    s = await session(h)
    h.as_("customer")
    out = await h.say(f"CHANAKYA {s.optin_token}")
    h.as_("associate")
    fc = next(m for m in out if m.card and m.card.template == "parivaar")
    assert fc.card.data["total"] is None and fc.card.data["no_price"] and not fc.card.data["ladder"]
    assert fc.card.data["kicker"].startswith("For the")


async def test_every_associate_message_offers_voice(h):
    out = []
    out += await to_options(h)
    for step in (lambda: h.tap("s:C1:demo"), lambda: h.tap("s:C1:shown"), lambda: pick(h), lambda: pay(h)):
        out += await step()
    msgs = [m for m in to_associate(out) if m.kind in VOICE_KINDS]
    assert msgs and all(m.footer for m in msgs), [m.body[:40] for m in msgs if not m.footer]


async def test_later_steps_by_voice_in_three_languages(h):
    await to_options(h)
    out = await h.say("demo dikhao")  # Hinglish: the demo guide, in one message
    assert out[-1].body.startswith("*Demo guide*") and "s:C1:shown" in button_ids(out)
    out = await h.say("घरच्यांना पाठवा")  # Marathi: send it to the family
    assert any(b.id in ("s:C1:qr", "s:C1:consent_tap") for b in out[0].buttons)
    out = await h.say("kal yaad dilana")  # a reminder
    assert out and "samajh nahi aaya" not in out[0].body
    out = await h.say("what next")  # English
    assert out and out[0].buttons


async def test_manager_voice_and_voice_log(h, monkeypatch):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.voice("assess_1")  # an associate voice note
    h.as_("manager")
    out = await h.say("aaj ka digest")
    assert any(m.card and m.card.template == "digest" for m in out)
    assert "_Voice notes: 1 · 1 " in (out[0].body or out[0].caption or "")  # beside the huddle's eight numbers

    async def heard(audio, mime=None):
        return "Ravi ke voice notes dikhao"

    monkeypatch.setattr(h.rt.ai, "transcribe", heard)
    out = await h.voice_bytes(b"OggS-mgr")
    check_floor(out)
    rows = [r for m in out for sec in (m.sections or []) for r in sec.rows]
    assert out[0].kind == "list" and rows and rows[0].id.startswith("m:vnote:")
    out = await h.pick(rows[0].id)
    assert "C1" in out[0].body and ("Understood" in out[0].body or "Samjha" in out[0].body)
    out = await h.say("kuch bhi")  # not a command: the menu (a list), with what was heard
    assert out[0].kind == "list" and out[0].footer


async def test_copilot_tip_once_and_only_when_a_fact_calls_for_it(h, monkeypatch):
    await to_options(h)
    await h.tap("s:C1:shown")
    out = await pick(h)
    s = await session(h)
    tips = [m for m in to_associate(out) if "Sujhav:" in (m.body or "")]
    assert len(tips) <= 1
    out = await h.tap("s:C1:cost_show")
    again = [m for m in to_associate(out) if "Sujhav:" in (m.body or "")]
    shown = s.extra.get("tips_shown") or []
    assert not again or len(set(shown)) == len(shown)  # never the same tip twice for one customer


async def test_voice_note_is_logged_for_the_manager(h, monkeypatch):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.pick("s:C1:cat:tv")

    async def u(text, audio, ctx, mime=None):
        return Understanding(transcript="sofa se das feet, dhoop aati hai", language="hi")

    monkeypatch.setattr(h.rt.ai, "understand", u)
    await h.voice_bytes(b"OggS-x")
    rows = await repo.events_for(types=["voice_note"])
    assert rows and rows[-1].payload["transcript"].startswith("sofa se") and rows[-1].payload["code"] == "C1"
