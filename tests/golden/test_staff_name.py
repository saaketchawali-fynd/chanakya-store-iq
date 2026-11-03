"""No dummy name (user, 3 Nov 2026): the associate is asked their name after the language, and it is used everywhere."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.sim.harness import PHONES
from chanakya.skills.staff_name import clean_name
from chanakya.truth import seed
from tests.conftest import make_harness


def test_names_are_cleaned():
    assert clean_name("mera naam amit hai") == "Amit"
    assert clean_name("My name is Priya Shah") == "Priya Shah"
    assert clean_name("माझं नाव सुनीता") == "सुनीता"
    assert clean_name("55 inch TV chahiye") is None and clean_name("") is None
    assert clean_name("ok ok ok ok ok") is None  # not a name


async def test_first_contact_asks_the_name_then_uses_it(tmp_path):
    h = await make_harness(tmp_path, lang_set=False, associate_name="")
    try:
        await h.say("namaste")
        out = await h.tap("g:alang:en")
        assert "name" in out[0].body.lower()
        out = await h.tap("g:new")  # a button before the name: asked again, kindly
        assert "name" in out[0].body.lower()
        out = await h.say("1234")
        assert "type your name" in out[0].body.lower()
        out = await h.say("Amit")
        b = next(m for m in out if m.card and m.card.template == "brief")
        assert b.card.data["greeting"].endswith("Amit")
        st = await repo.staff_by_wa(PHONES["associate"])
        assert st.name == "Amit" and st.name_set
        out = await h.say("naam Amit Kumar")  # changed later
        assert "Amit Kumar" in out[0].body
        await seed.upsert_staff(h.rt.settings.model_copy(update={"associate_name": "Ravi"}))  # a restart
        assert (await repo.staff_by_wa(PHONES["associate"])).name == "Amit Kumar"  # their name is kept
    finally:
        await h.close()


async def test_name_by_voice(tmp_path, monkeypatch):
    h = await make_harness(tmp_path, lang_set=False, associate_name="")
    try:
        await h.say("namaste")
        await h.tap("g:alang:hi")

        async def heard(audio, mime=None):
            return "mera naam Rahul hai"

        monkeypatch.setattr(h.rt.ai, "transcribe", heard)
        out = await h.voice_bytes(b"OggS-name")
        assert any(m.card and m.card.template == "brief" for m in out)
        assert (await repo.staff_by_wa(PHONES["associate"])).name == "Rahul"
    finally:
        await h.close()
