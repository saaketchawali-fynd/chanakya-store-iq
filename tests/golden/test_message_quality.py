"""User, 5 Nov 2026: every message, from before the shift to the end, well formatted, well spaced, written in full and
with the customer present in it. The whole journey in English, Hindi and Marathi:
shift start → home questions → photo → doubt → label → price asked early → options → side by side → demo guide →
which felt right → worth the extra → choice → setup → full cost → exchange → payment → offer → delivery → consent →
Family Card → task → installation → score → reminders → a voice-note customer → not today → menu → manager."""

from __future__ import annotations

import re

import pytest

from chanakya.channel import limits
from chanakya.i18n.lang import raw
from chanakya.sim.harness import PHONES
from tests.conftest import make_harness
from tests.helpers import check_floor

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
DEV = re.compile(r"[\u0900-\u097F]")
EMOJI = re.compile("[\U0001F100-\U0001F1FF\U0001F300-\U0001FAFF☀-➿⭐⏰-⏿]")


def _rows(out):
    return [r.id for m in out for sec in m.sections for r in sec.rows]


async def journey(h, lang: str) -> list:
    """Every message of one long shift, in order."""
    seen: list = []

    async def go(fn, *a):
        out = await fn(*a)
        check_floor(out)
        seen.extend(out)
        return out

    def first(out, prefix):
        return next((i for i in [b.id for m in out for b in m.buttons] + _rows(out) if i.startswith(prefix)), None)

    await go(h.say, "namaste")
    await go(h.tap, f"g:alang:{lang}")
    out = await go(h.say, "my name is Amit")  # then which product's stock and offers: every product, one by one
    for cat in ("ac", "wm", "fridge", "laptop", "tv"):
        out = await go(h.pick, f"g:briefcat:{cat}")
        if cat != "tv":
            await go(h.tap, "g:brief")
    await go(h.tap, next(b.id for m in out for b in m.buttons))  # today's training
    await go(h.tap, "g:ready:translate")
    await go(h.tap, "g:new")
    await go(h.tap, f"s:C1:lang:{lang}")
    await go(h.pick, "s:C1:cat:tv")
    await go(h.voice, "assess_short")
    for b in ("s:C1:slot:trigger:replace", "s:C1:slot:old_size:o43", "s:C1:slot:room_light:sunny"):
        await go(h.tap, b)
    await go(h.say, "papa cricket dekhte hain, bachche cartoon")
    await go(h.tap, "s:C1:photo_yes")
    await go(h.photo, "room_living.jpg")
    await go(h.tap, "s:C1:readback_ok")
    await go(h.tap, "s:C1:doubt")
    await go(h.pick, "s:C1:myth:tv_4k_useless")
    await go(h.photo, "label_meru55.jpg")
    await go(h.tap, "s:C1:bt:yes:b_motion")
    await go(h.say, "offers")
    await go(h.tap, "s:C1:options")
    await go(h.tap, "s:C1:more")
    await go(h.tap, "s:C1:cmpall")
    await go(h.tap, "s:C1:demo")
    await go(h.tap, "s:C1:shown")
    await go(h.pick, "s:C1:upgrade")
    await go(h.pick, "s:C1:opt:B")
    for b in ("s:C1:slot:mounting:wall", "s:C1:slot:wall_type:concrete", "s:C1:slot:soundbar:no"):
        await go(h.tap, b)
    out = await go(h.tap, "s:C1:addons:pick")  # "Complete it": one add-on, then that's all → the full cost
    await go(h.pick, next(r.id for m in out for s_ in m.sections for r in s_.rows if ":addon:" in r.id))
    await go(h.pick, "s:C1:addons:done")
    for b in ("s:C1:offers", "s:C1:slot:exchange_old:yes_working", "s:C1:pay:emi"):
        await go(h.tap, b)
    await go(h.pick, "s:C1:bank:HDFC")
    out = await go(h.tap, "s:C1:delivery")
    await go(h.tap, first(out, "s:C1:dday:"))
    await go(h.tap, "s:C1:qr")
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    h.as_("customer")
    await go(h.say, f"CHANAKYA {s.optin_token}")
    h.as_("associate")
    await go(h.tap, "s:C1:task")
    await go(h.tap, "s:C1:task_when:today_evening")
    await go(h.tap, "s:C1:khatam")
    await go(h.tap, "s:C1:islot:t1")
    await go(h.clock, "+1d")
    await go(h.clock, "install")
    h.as_("customer")
    await go(h.tap, f"u:{s.id[:8]}:working")
    h.as_("associate")
    # a second customer: everything in one voice note, then not today
    out = await go(h.tap, "g:new")
    code = first(out, "s:C").split(":")[1]
    await go(h.tap, f"s:{code}:lang:{lang}")
    await go(h.pick, f"s:{code}:cat:tv")
    await go(h.voice, "assess_1")
    await go(h.tap, f"s:{code}:cmpall")
    await go(h.tap, f"s:{code}:demo")
    await go(h.tap, f"s:{code}:shown")
    await go(h.pick, f"s:{code}:nonefit")
    await go(h.tap, f"s:{code}:nottoday")  # the reasons, each with a preview of the honest line
    await go(h.pick, f"s:{code}:obj:waiting_for_sale")
    await go(h.say, "menu")
    await go(h.tap, "g:status")
    await go(h.tap, f"s:{code}:khatam")
    out = await go(h.tap, f"s:{code}:outcome:lost")
    await go(h.tap, first(out, f"s:{code}:pricefirst:"))
    await go(h.tap, "g:score")
    h.as_("manager")
    await go(h.say, "namaste")
    await go(h.tap, f"g:alang:{lang}")
    await go(h.say, "floor")
    return seen


@pytest.mark.parametrize("lang", ["en", "hi", "mr"])
async def test_every_message_reads_well(tmp_path, lang):
    h = await make_harness(tmp_path, lang_set=False)
    try:
        msgs = await journey(h, lang)
    finally:
        await h.close()
    assert len(msgs) > 60
    do_label = raw("label_do", lang)
    last_do = None
    for m in msgs:
        text = m.body or m.caption or ""
        visible = [text, m.footer or ""] + [b.title for b in m.buttons] + [
            x for sec in m.sections for r in sec.rows for x in (r.title, r.description or "")]
        to_associate = m.to in (PHONES["associate"], PHONES["manager"])
        picker = any(b.id.startswith("g:alang:") for b in m.buttons)
        for v in visible:
            assert not PLACEHOLDER.search(v), v  # never a raw {placeholder}
            if lang == "hi" and to_associate and not picker:  # Hindi is Roman Hinglish, the lines to say too
                assert not DEV.search(v), v
            assert not EMOJI.search(v), v  # no emojis anywhere
            assert ": ?" not in v, v  # an unknown is said in words ('not asked yet'), never '?'
        if m.buttons:
            assert len(text) <= limits.BODY_MAX, text
        if m.coach:  # Say / Why / Do: what to do with the customer, right now
            assert do_label in text, text
            do = next(ln for ln in text.split("\n") if ln.startswith(do_label))
            assert do != last_do, do  # never the same Do line twice in a row
            last_do = do
        lines = [ln for ln in text.split("\n") if ln.strip()]
        if len(lines) >= 3 and not picker:  # a detailed message opens with its bold title (or the Say label)
            assert lines[0].startswith("*"), text
            assert "\n\n" in text or len(lines) <= 5, text  # and is spaced into blocks
