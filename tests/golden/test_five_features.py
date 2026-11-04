"""User, 4 Nov 2026: returning-customer memory, the out-of-stock swap, the associate's wins, A vs B on their own
wall, and viewing comfort from their sofa. All from the conversation and the backend data."""

from __future__ import annotations

from chanakya.sim.harness import PHONES
from chanakya.skills.view import angle, seat
from tests.golden.test_after_sale import to_consent
from tests.helpers import button_ids, check_floor, to_options


async def session(h, code="C1"):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], code)


def rows(out):
    return [r for m in out for sec in (m.sections or []) for r in sec.rows]


async def test_returning_customer_carries_on_without_re_asking(h):
    await to_options(h)
    old = await session(h)
    sku = old.option_labels["B"]
    h.rt.facts.snap.stock[(sku, h.rt.settings.store_id)].price -= 3000  # the backend price fell since
    await h.clock("+2d")
    await h.tap("g:new")
    out = await h.pick("s:C1:returning")
    check_floor(out)
    r = rows(out)
    assert r and r[0].id.startswith("s:C1:retpick:") and "Shah" in r[0].title
    out = await h.pick(r[0].id)
    check_floor(out)
    head = out[0].body
    assert "Shah" in head and "pasand" in head and "sasta" in head  # what happened, and what changed since
    assert any(m.card and m.card.template == "options" for m in out)  # straight back to their picks
    new = await session(h)
    assert new.home.slots and new.option_labels == old.option_labels


async def test_a_named_customer_seen_before_is_offered_to_carry_on(h):
    await to_options(h)
    await h.clock("+1d")
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    out = await h.voice("assess_short")  # 'Shah ji …' again
    assert "s:C1:retpick:" in " ".join(button_ids(out[:1])) and "before" in out[0].body.lower() or "pehle" in out[0].body


async def test_a_customer_message_after_a_while_reminds_the_associate(h):
    await to_consent(h)
    await h.clock("+1d")
    h.as_("customer")
    out = await h.say("Kya B wala abhi bhi available hai?")
    msg = next(m for m in out if m.to == PHONES["associate"])
    assert "Yaad dila" in msg.body and "pasand" in msg.body  # the memory line, then their words


async def test_out_of_stock_pick_offers_the_same_fit_in_stock(h):
    await to_options(h)
    await h.tap("s:C1:shown")
    s = await session(h)
    gone = s.option_labels["B"]
    st = h.rt.facts.snap.stock[(gone, h.rt.settings.store_id)]
    st.qty, st.nearby_qty = 0, 0
    out = await h.tap("s:C1:opt:B")
    check_floor(out)
    swap = next(b.id for b in out[0].buttons if ":swap:" in b.id)
    assert "stock" in out[0].body.lower() and swap.split(":")[-1] != gone
    out = await h.tap(swap)
    assert any(m.card and m.card.template == "kharcha" for m in out) or any("s:C1:slot:" in b for b in button_ids(out))


async def test_wins_count_the_associates_own_family_cards(h):
    await to_consent(h)
    await h.tap("s:C1:parivaar")
    out = await h.say("wins")
    assert "Parivaar Card" in out[0].body or "Family Card" in out[0].body


async def test_a_vs_b_on_their_wall_in_one_picture(h):
    await to_options(h, photo="room_living.jpg")
    await h.tap("s:C1:shown")
    out = await h.pick("s:C1:abwall")
    cards = [m for m in out if m.card and m.card.template == "homecompare"]
    assert len(cards) == 1  # one card: both on their own wall, and how each meets their needs
    d = cards[0].card.data
    assert len(d["cols"]) == 2 and all(c["photo"] and c["is_room"] for c in d["cols"])  # their room, each drawn
    assert d["rows"] and all(len(r["cells"]) == 2 for r in d["rows"])  # their needs, a cell per product
    ids = button_ids(cards)
    assert sum(1 for i in ids if ":tpick:" in i) == 2 and "s:C1:prefer" in ids


async def test_viewing_comfort_from_their_sofa(h):
    assert round(angle(55, 10)) == 23 and seat(angle(55, 10)) == "middle"  # plain geometry
    assert seat(angle(65, 7)) in ("best", "front")
    await to_options(h)
    await h.tap("s:C1:shown")
    out = await h.pick("s:C1:view")
    check_floor(out)
    card = next(m.card for m in out if m.card)
    assert card.template == "view" and all(r["deg"] for r in card.data["rows"])
    assert "10 ft" in card.data["distance"]
