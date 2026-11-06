"""No dummy name (user, 3 Nov 2026): the associate is asked their name after the language, and it is used everywhere."""

from __future__ import annotations

from chanakya.core import repo
from chanakya.sim.harness import PHONES
from chanakya.skills.staff_name import clean_name
from chanakya.truth import seed
from tests.conftest import make_harness
from tests.helpers import open_brief


def test_names_are_cleaned():
    assert clean_name("mera naam amit hai") == "Amit"
    assert clean_name("My name is Priya Shah") == "Priya Shah"
    assert clean_name("माझं नाव सुनीता") == "सुनीता"
    assert clean_name("55 inch TV chahiye") is None and clean_name("") is None
    assert clean_name("ok ok ok ok ok") is None  # not a name
    for word in ("restart", "Restart", "shuru se", "new", "help", "menu", "hi", "namaste", "ok", "haan", "नमस्ते"):
        assert clean_name(word) is None, word  # a command or a greeting is never a name


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
        out = await open_brief(h, await h.say("Amit"))
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
        out = await open_brief(h, await h.voice_bytes(b"OggS-name"))
        assert any(m.card and m.card.template == "brief" for m in out)
        assert (await repo.staff_by_wa(PHONES["associate"])).name == "Rahul"
    finally:
        await h.close()


async def test_restart_is_never_taken_as_a_name(tmp_path):
    """3 Nov 2026 on the phone: the language was set before names existed; the first message ('restart') was
    saved as the name, and the question was never asked. Now the question comes first, and commands are not names."""
    from chanakya.core import repo as R

    h = await make_harness(tmp_path, associate_name="")
    try:
        await R.staff_update(PHONES["associate"], name_set=False, name_asked=False)  # as on production
        out = await h.say("restart")
        assert "name" in out[0].body.lower() or "naam" in out[0].body.lower()  # asked, not saved
        st = await R.staff_by_wa(PHONES["associate"])
        assert not st.name_set and st.name_asked
        out = await h.say("restart")  # still not a name
        assert "naam" in out[0].body.lower() and not (await R.staff_by_wa(PHONES["associate"])).name_set
        out = await open_brief(h, await h.say("Amit"))
        assert any(m.card and m.card.template == "brief" for m in out)
        assert (await R.staff_by_wa(PHONES["associate"])).name == "Amit"
    finally:
        await h.close()


async def test_a_command_saved_as_a_name_is_repaired(tmp_path):
    from chanakya.core import repo as R

    h = await make_harness(tmp_path)
    try:
        await R.staff_update(PHONES["associate"], name="Restart", name_set=True, name_asked=True)  # the bug's result
        out = await h.say("namaste")
        assert "naam" in out[0].body.lower()  # asked again
        out = await h.say("mera naam Rahul hai")
        assert (await R.staff_by_wa(PHONES["associate"])).name == "Rahul"
    finally:
        await h.close()
