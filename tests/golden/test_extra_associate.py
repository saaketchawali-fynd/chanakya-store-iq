"""A second associate number for the demo (EXTRA_ASSOCIATE_PHONES, user 12 Nov 2026): the number is an associate
from its first message, asked its own name, with its own customers; the manager sees both on the team."""

from __future__ import annotations

from pathlib import Path

import pytest

from chanakya.config import Settings
from chanakya.core import repo
from chanakya.sim.harness import PHONES
from tests.conftest import make_harness
from tests.helpers import button_ids, check_floor, option_pages

SECOND = "919870279367"
FIX_AUDIO = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "audio"


def test_extra_numbers_are_read_like_the_others():
    s = Settings(_env_file=None, associate_phone="919000000001",
                 extra_associate_phones="+91 98702-79367, 9876543210,919000000001, ")
    assert s.associate_phones == ["919000000001", SECOND, "919876543210"]  # normalised, no repeats, none empty
    assert Settings(_env_file=None, associate_phone="919000000001").associate_phones == ["919000000001"]


async def test_second_associate_works_on_its_own(tmp_path, monkeypatch):
    monkeypatch.setitem(PHONES, "associate2", SECOND)
    h = await make_harness(tmp_path, extra_associate_phones=SECOND)
    try:
        st = await repo.staff_by_wa(SECOND)
        assert st is not None and st.role == "associate" and st.store_id == "RD-JUHU"
        assert not st.name  # no dummy name: asked at first contact
        h.as_("associate")
        await h.tap("g:new")
        await h.pick("s:C1:cat:tv")
        await h.voice("assess_1")  # the first associate's customer is out with the products
        h.as_("associate2")
        out = await h.tap("g:new")  # first contact: their own name first (it goes on the customer's Family Card)
        assert all(m.to == SECOND for m in out) and "naam" in out[0].body.lower()
        await h.say("mera naam Priya hai")
        assert (await repo.staff_by_wa(SECOND)).name == "Priya"
        out = await h.say("naya customer")  # then their own new customer, never the first associate's
        check_floor(out)
        assert all(m.to == SECOND for m in out)
        rows = [r.id for m in out for sec in (m.sections or []) for r in sec.rows]
        code = rows[0].split(":")[1]  # s:<code>:cat:tv
        out = await h.pick(f"s:{code}:cat:tv")
        assert out[0].header == "Step 2/9 · Ghar samjho" and any(":slot:room:" in b for b in button_ids(out))
        out = await h.voice("assess_1")
        assert option_pages(out) and all(m.to == SECOND for m in out)
        mine = await h.rt.svc.sessions.get((await repo.staff_by_wa(SECOND)).current_session_id)
        first = await h.rt.svc.sessions.get((await repo.staff_by_wa(PHONES["associate"])).current_session_id)
        assert mine.id != first.id and mine.associate_wa_id == SECOND and first.associate_wa_id == PHONES["associate"]
        team = {s.wa_id for s in await repo.staff_all() if s.role == "associate"}
        assert {PHONES["associate"], SECOND} <= team  # the manager's team has both
    finally:
        await h.close()


FIRST_MESSAGES = ["hi", "test", "TV chahiye 55 inch", "kya hai ye", "👍", "Rahul", "naya customer", "restart",
                  "STOP", "voice", "photo"]


@pytest.mark.parametrize("first", FIRST_MESSAGES)
async def test_any_first_message_starts_the_associate_flow(tmp_path, monkeypatch, first):
    """Whatever the new number sends first (any text, a voice note, a photo): the language, then the name, then
    the brief. Never the customer flow, never silence (user, 13 Nov 2026)."""
    monkeypatch.setitem(PHONES, "associate2", SECOND)
    h = await make_harness(tmp_path, extra_associate_phones=SECOND, lang_set=False)
    try:
        h.as_("associate2")
        if first == "voice":
            out = await h.voice_bytes((FIX_AUDIO / "voice_check_hi.ogg").read_bytes())
        elif first == "photo":
            out = await h.photo("wall_with_tv.jpg")
        else:
            out = await h.say(first)
        assert out and all(m.to == SECOND for m in out)
        assert {b.id for b in out[-1].buttons} == {"g:alang:en", "g:alang:hi", "g:alang:mr"}  # the language first
        out = await h.tap("g:alang:hi")
        assert "naam" in out[-1].body.lower()  # then their own name
        out = await h.say("Priya")
        assert (await repo.staff_by_wa(SECOND)).name == "Priya"
        check_floor(out)
        assert any("Priya" in (m.body or m.caption or "") for m in out)  # then the brief, by name
        assert (await repo.staff_by_wa(SECOND)).role == "associate"
    finally:
        await h.close()
