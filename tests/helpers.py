"""Global checks on every outbound message (spec 18.3)."""

from __future__ import annotations

import re

from chanakya.core.contracts import OutMessage
from chanakya.i18n.lang import raw

BANNED = [
    "today only",
    "sirf aaj",
    "aaj hi",
    "आज ही",
    "सिर्फ़ आज",
    "फक्त आज",
    "last piece",
    "aakhri piece",
    "आखिरी पीस",
    "शेवटचा पीस",
    "because of the war",
    "real brand",
    "asli brand",
    "just written",
]


def check_floor(msgs: list[OutMessage]) -> None:
    for m in msgs:
        for text in (m.body, m.caption):
            if text:
                lines = text.split("\n")
                assert len(lines) <= 24, f"> 24 lines: {text!r}"  # well spaced, detailed, still scannable
                assert not any(not a.strip() and not b.strip() for a, b in zip(lines, lines[1:], strict=False)), f"double gap: {text!r}"
                assert not re.search(r"[०-९]", text)
                low = text.lower()
                assert not any(b in low for b in BANNED), text
        assert len(m.buttons) <= 3
        assert all(len(b.title) <= 20 for b in m.buttons), [b.title for b in m.buttons]
        assert len({b.title for b in m.buttons}) == len(m.buttons)
        if m.coach:
            assert m.why_cites, m.body
            assert any(raw("label_kyun", lg) in (m.body or m.caption or "") for lg in ("hi", "mr", "en"))  # image: caption


def texts(msgs: list[OutMessage]) -> str:
    out = []
    for m in msgs:
        out += [m.body or "", m.caption or ""] + [b.title for b in m.buttons]
    return "\n".join(out)


def button_ids(msgs: list[OutMessage]) -> list[str]:
    return [b.id for m in msgs for b in m.buttons]


# ---------- the sales flow (shared by the golden and red-team tests) ----------
COMPLETE_ANSWERS = ("mounting:wall", "wall_type:concrete", "soundbar:has", "outdoor_spot:wall", "pipe_run_ft:p_le10",
                    "wall_hole:existing")


async def assess_tv(h, clang: str | None = "mr", trigger: str = "new_home"):
    """New customer → (customer language) → the Shah voice note (room, distance, light, content, budget) → why now."""
    await h.tap("g:new")
    if clang:
        await h.tap(f"s:C1:lang:{clang}")
    await h.voice("assess_1")
    return await h.tap(f"s:C1:slot:trigger:{trigger}")


async def assess_steps(h, clang: str | None = "mr", trigger: str = "new_home"):
    """The question-by-question path: a short note (room, distance, budget), then why now, light and content by tap,
    until the photo question. (A note that tells the needs in full skips straight to the products instead.)"""
    await h.tap("g:new")
    if clang:
        await h.tap(f"s:C1:lang:{clang}")
    out = await h.voice("assess_short")
    answers = (f"trigger:{trigger}", "old_size:o43", "room_light:sunny", "content:sports")
    for _ in range(5):
        ids = button_ids(out)
        if "s:C1:photo_no" in ids:
            return out
        nxt = next((f"s:C1:slot:{a}" for a in answers if f"s:C1:slot:{a}" in ids), None)
        if nxt is None:
            return out
        out = await h.tap(nxt)
    return out


async def to_options(h, photo=None, clang: str | None = "mr"):
    """…→ room photo (or none) → read it back → no doubts → the options."""
    await assess_tv(h, clang)
    if photo:
        await h.tap("s:C1:photo_yes")
        await h.photo(photo)
    else:
        await h.tap("s:C1:photo_no")
    await h.tap("s:C1:readback_ok")
    return await h.tap("s:C1:options")


async def answer_complete(h, out):
    """Answer the cost questions Complete asks (mounting, wall, soundbar / AC site) until the cost card comes."""
    for _ in range(6):
        if any(m.card and m.card.template == "kharcha" for m in out):
            return out
        ids = button_ids(out)
        nxt = next((f"s:C1:slot:{a}" for a in COMPLETE_ANSWERS if f"s:C1:slot:{a}" in ids), None)
        if nxt is None:
            return out
        out = await h.tap(nxt)
    return out


async def pick(h, label: str = "B"):
    """Customer picks an option (after the demo) → Complete's questions answered → the cost card."""
    return await answer_complete(h, await h.tap(f"s:C1:opt:{label}"))


async def pay(h, how: str = "card", bank: str | None = "HDFC"):
    """Offers: (exchange question if replacing) → payment → bank → the offer card."""
    await h.tap("s:C1:offers")
    out = await h.tap(f"s:C1:pay:{how}")
    if bank and how in ("card", "emi"):
        out = await h.tap(f"s:C1:bank:{bank}")
    return out
