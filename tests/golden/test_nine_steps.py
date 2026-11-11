"""The associate flow in the contest's 9 steps (user, 11 Nov 2026): Assess asks the product's 3-4 home questions and
that's it, then the products; after a voice note, one clear choice (Proceed, or once More details for what the note
did not cover); Proceed never asks a home question again; nothing seen or declined is offered twice; each message
names its step and is spaced for a quick read."""

from __future__ import annotations

from chanakya.channel.whatsapp import WhatsAppChannel, spaced
from chanakya.config import Settings
from chanakya.core.contracts import Button, OutMessage
from chanakya.sim.harness import PHONES
from tests.helpers import button_ids, check_floor, option_pages

HOME_Q = {"tv": ["room", "seat_distance", "room_light", "content"],
          "ac": ["room_size", "sun_exposure", "hours_per_day", "power_issues"],
          "wm": ["family_size", "wash_load", "water", "space"],
          "fridge": ["family_size", "shopping", "space_width", "power_cuts"],
          "laptop": ["user", "course", "software", "carry"]}


def _slot_buttons(out) -> list[str]:
    return [b for b in button_ids(out) if ":slot:" in b]


def _last_buttons(out) -> list[str]:
    return [b.id.split(":", 2)[-1] for b in out[-1].buttons]


async def _tap_through(h, out, limit: int = 8):
    """Answer each home question with its first answer until the products come; returns (questions, out)."""
    asked = []
    for _ in range(limit):
        if option_pages(out):
            return asked, out
        ids = [b for b in button_ids(out) if ":slot:" in b or ":dq:" in b]
        assert ids, [m.body for m in out]
        asked.append(ids[0].split(":")[3])
        out = await h.tap(ids[0])
        check_floor(out)
    return asked, out


async def test_assess_is_the_four_home_questions_then_the_products(h):
    await h.tap("g:new")
    out = await h.pick("s:C1:cat:tv")
    assert out[0].header == "Step 2/9 · Ghar samjho"  # which of the 9 steps, on the message itself
    asked, out = await _tap_through(h, out)
    assert asked == HOME_Q["tv"]  # no 'why now', old size, photo, budget, read-back or doubts question
    head = out[0]
    assert head.card and head.card.template == "home"  # Assess ends in a card of the customer's home
    assert "*Ghar:*" in (head.body or head.caption)
    assert len(option_pages(out)) == 3
    assert _last_buttons(out) == ["proceed", "moreopts", "doubt"]  # one clear choice: Proceed first
    assert out[-1].header == "Step 5/9 · 3 options aur demo"
    assert "Dikhane wala farak" in out[-1].body  # the contest: which difference to show
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.start_level_inr is None  # no budget asked: the price comes last, the start level is theirs to set


async def test_each_product_asks_at_most_four(h):
    for i, cat in enumerate(("ac", "wm", "fridge", "laptop"), start=1):
        await h.tap("g:new")
        out = await h.pick(f"s:C{i}:cat:{cat}")
        asked, out = await _tap_through(h, out)
        assert 3 <= len(asked) <= 4, (cat, asked)
        assert set(asked) <= set(HOME_Q[cat]) | {"dq"}, (cat, asked)
        assert option_pages(out), cat
        await h.say("khatam")


async def test_voice_note_gives_products_and_more_details_once(h):
    await h.tap("g:new")
    await h.pick("s:C1:cat:tv")
    out = await h.voice("assess_short")  # room and distance (and a budget) in one note
    check_floor(out)
    assert option_pages(out) and out[0].card.template == "home"  # the products from the note, at once
    assert _last_buttons(out) == ["proceed", "moreopts", "doubt"]
    assert out[-1].buttons[1].title == "Aur jaankari"
    out = await h.tap("s:C1:moreopts")  # More details: only the home questions the note did not cover
    assert _slot_buttons(out)[0].startswith("s:C1:slot:room_light:")
    assert out[0].footer.startswith("Aur jaankari 1/2") and out[0].header == "Step 2/9 · Ghar samjho"
    out = await h.tap("s:C1:slot:room_light:sunny")
    assert _slot_buttons(out)[0].startswith("s:C1:slot:content:") and out[0].footer.startswith("Aur jaankari 2/2")
    out = await h.tap("s:C1:slot:content:sports")
    assert option_pages(out) or "best fits" in out[0].body.lower() or out[-1].buttons
    assert _last_buttons(out)[0] == "proceed"
    assert out[-1].buttons[1].title != "Aur jaankari"  # never offered twice


async def test_full_note_offers_only_the_way_on(h):
    await h.tap("g:new")
    await h.pick("s:C1:cat:tv")
    out = await h.voice("assess_1")  # all four home facts in one note
    assert option_pages(out)
    assert _last_buttons(out) == ["proceed", "moreopts", "doubt"]
    assert out[-1].buttons[1].title == "Aur options"  # nothing left to ask: no 'More details'


async def test_proceed_closes_assess_and_shows_them_in_their_space(h):
    await h.tap("g:new")
    await h.pick("s:C1:cat:tv")
    await h.voice("assess_short")
    out = await h.tap("s:C1:proceed")
    assert button_ids(out) == ["s:C1:photo_yes", "s:C1:photo_no"]  # their own space: the room photo, asked once
    out = await h.tap("s:C1:photo_no")
    assert out[0].card and out[0].card.template == "homecompare"  # side by side
    assert "s:C1:tryon" not in button_ids(out)  # declined: never offered again
    out = await h.tap("s:C1:demo")
    ids = button_ids(out)
    assert ids[0] == "s:C1:shown" and "s:C1:cmpall" not in ids and "s:C1:tryon" not in ids
    out = await h.say("kamra dhoop wala hai, papa cricket dekhte hain")  # a home fact after Proceed
    assert not _slot_buttons(out)  # no home question again, ever
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.extra.get("assess_closed")
    out = await h.tap("s:C1:moreopts")  # 'More options' after Proceed: wider picks, never the home questions
    assert not _slot_buttons(out) and option_pages(out)


async def test_proceed_with_their_room_draws_each_option_there(h):
    await h.tap("g:new")
    await h.pick("s:C1:cat:tv")
    await h.photo("room_living.jpg")  # the room photo during Assess: kept for later
    out = await h.voice("assess_1")
    assert option_pages(out)
    await h.tap("s:C1:proceed")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert (s.extra.get("tryon_req") or {}).get("mode") == "cmp"  # each option on their own wall


async def test_steps_named_through_the_journey(h):
    await h.tap("g:new")
    await h.pick("s:C1:cat:tv")
    await h.voice("assess_1")
    out = await h.tap("s:C1:proceed")
    out = await h.tap("s:C1:photo_no")
    out = await h.tap("s:C1:demo")
    out = await h.tap("s:C1:shown")
    assert out[0].header == "Step 5/9 · 3 options aur demo"
    out = await h.pick("s:C1:opt:B")
    assert any(m.header == "Step 6/9 · Poora kharcha" for m in out)
    out = await h.say("khatam")
    assert any(m.header == "Step 9/9 · Review" for m in out if m.header) or any(m.card for m in out)


def test_payload_spaced_with_the_step_header():
    ch = WhatsAppChannel(Settings(_env_file=None, wa_token="x", wa_phone_number_id="1"))
    m = OutMessage(to="91", kind="buttons", body='*Bolo*: "a"\n*Kyun*: b\n*Karo*: c', header="Step 2/9 · Ghar samjho",
                   footer="Bolkar bhi", buttons=[Button(id="x", title="X")])
    p = ch.payload(m)["interactive"]
    assert p["body"]["text"] == '*Bolo*: "a"\n\n*Kyun*: b\n\n*Karo*: c'  # a blank line between the lines
    assert p["header"] == {"type": "text", "text": "Step 2/9 · Ghar samjho"}
    m2 = m.model_copy(update={"media_id": "img1"})
    assert ch.payload(m2)["interactive"]["header"]["type"] == "image"  # a card stays the header
    t = ch.payload(OutMessage(to="91", kind="text", body="one\ntwo", footer="f"))["text"]["body"]
    assert t == "one\n\ntwo\n\n_f_"
    assert spaced("a\n\n\nb\n") == "a\n\nb"
