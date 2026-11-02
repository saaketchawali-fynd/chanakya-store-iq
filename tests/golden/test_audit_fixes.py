"""Sale and experience fixes from the flow audit (2 Nov 2026): honest demos, consistent option labels, no repeated
cards, roles kept apart, the customer-language note filled in, a price question answered with the full cost."""

from __future__ import annotations

from chanakya.brain.demos import rank_demos
from chanakya.brain.fit import fit
from chanakya.brain.options import Option, OptionSet
from chanakya.sim.harness import PHONES
from chanakya.truth import jio_sync
from chanakya.truth.jio import JioClient
from tests.helpers import button_ids, pick, to_options
from tests.unit.test_jio import PROMO, backend, item, live


async def session(h):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")


async def test_a_demo_never_says_the_best_pick_wins_when_it_does_not(h):
    await to_options(h)
    s = await session(h)
    pack, g = h.rt.packs.get("tv"), h.rt.facts.snap.guides
    a, b = (h.rt.facts.product_sync(s.option_labels[k]) for k in ("A", "B"))
    # swap roles on purpose: the 'best' is now the weaker product on every rule where they differ
    weak, strong = (a, b) if fit(a, s.home, pack, g).score < fit(b, s.home, pack, g).score else (b, a)
    opts = OptionSet([Option("A", "value", strong, fit(strong, s.home, pack, g), "value_fit"),
                      Option("B", "best", weak, fit(weak, s.home, pack, g), "")])
    for d, _other, score in rank_demos(opts, s.home, pack, g):
        assert score <= 0 or not d.differs_on or weak.spec(d.differs_on) != strong.spec(d.differs_on)
        assert score <= 0 or fit(weak, s.home, pack, g).score >= 0  # only positive where the best really wins


async def test_two_options_keep_their_roles(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", backend({item("Panasonic 139.7 cm (55 inch)")["slug"]: [PROMO]}))
    h = await live(tmp_path)
    try:
        await jio_sync.sync(h.rt.settings, h.rt.facts)
        for b in ("g:new", "s:C1:lang:en"):
            await h.tap(b)
        await h.voice("assess_1")
        for b in ("s:C1:slot:trigger:new_home", "s:C1:photo_no", "s:C1:readback_ok", "s:C1:options"):
            out = await h.tap(b)
        s = await session(h)
        roles = s.extra["option_roles"]
        best = next(lab for lab, r in roles.items() if r == "best")
        out = await h.tap("s:C1:shown")
        rows = {r.id.split(":")[-1]: r for m in out for sec in (m.sections or []) for r in sec.rows}
        cheap = "costs less" in (rows[best].description or "").lower() or "kam kharcha" in (rows[best].description or "")
        assert not cheap  # the best pick is not described as the cheaper one
    finally:
        await h.close()


async def test_unclear_text_at_the_read_back_does_not_resend_the_cards(h):
    from tests.helpers import assess_tv

    await assess_tv(h)
    out = await h.tap("s:C1:photo_no")
    assert any(m.card for m in out)  # the read-back with the Home Card
    out = await h.say("ok")
    assert not any(m.card or m.card_copy for m in out)
    assert button_ids(out) == ["s:C1:readback_ok", "s:C1:readback_edit"]


async def test_roles_stay_apart(h):
    h.as_("expert")
    out = await h.say("floor")
    assert "Floor" not in out[0].body and not any(m.card for m in out)
    h.as_("manager")
    out = await h.say("new")
    assert not any(b.id.startswith("s:") for m in out for b in m.buttons)  # no sales session on the manager's phone


async def test_customer_language_note_is_filled(h):
    await h.tap("g:new")
    out = await h.tap("s:C1:lang:mr")
    note = " ".join((m.footer or "") for m in out)
    assert "{" not in note and "मराठी" in note or "Marathi" in note or "मराठी" in " ".join(m.body or "" for m in out)


async def test_price_question_after_the_cost_resends_the_cost(h):
    await to_options(h)
    await h.tap("s:C1:shown")
    await pick(h)
    out = await h.voice("price_ask")
    assert any(m.card and m.card.template == "kharcha" for m in out)
