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
                assert len(lines) <= 3, f"> 3 lines: {text!r}"  # the contest's floor rule
                assert all(ln.strip() for ln in lines), f"blank line: {text!r}"
                assert not re.search(r"[०-९]", text)
                low = text.lower()
                assert not any(b in low for b in BANNED), text
        assert len(m.buttons) <= 3
        assert all(len(b.title) <= 20 for b in m.buttons), [b.title for b in m.buttons]
        assert len({b.title for b in m.buttons}) == len(m.buttons)
        if m.coach:
            assert m.why_cites, m.body
            assert any(raw("label_kyun", lg) in (m.body or m.caption or "") for lg in ("hi", "mr", "en"))  # image: caption


def texts(msgs: list[OutMessage], footer: bool = True) -> str:
    out = []
    for m in msgs:
        out += [m.body or "", m.caption or "", (m.footer or "") if footer else ""] + [b.title for b in m.buttons]
    return "\n".join(out)


def button_ids(msgs: list[OutMessage]) -> list[str]:
    return [b.id for m in msgs for b in m.buttons]


# ---------- the sales flow (shared by the golden and red-team tests) ----------
COMPLETE_ANSWERS = ("mounting:wall", "wall_type:concrete", "soundbar:has", "outdoor_spot:wall", "pipe_run_ft:p_le10",
                    "wall_hole:existing")


async def assess_tv(h, clang: str | None = "mr", trigger: str | None = "new_home", photo=None):
    """New customer → (customer language) → TV → (a room photo) → the Shah voice note (room, distance, light,
    content, budget): the 4 home questions are all in it, so the products come at once (user, 11 Nov 2026). `trigger`
    records why now (no longer a question in Assess; it decides the exchange question at Close)."""
    await h.tap("g:new")
    if clang:
        await h.tap(f"s:C1:lang:{clang}")
    await h.pick("s:C1:cat:tv")
    if photo:
        await h.photo(photo)
    out = await h.voice("assess_1")
    if trigger:
        await h.tap(f"s:C1:slot:trigger:{trigger}")
    return out


async def assess_steps(h, clang: str | None = "mr"):
    """The question-by-question path: TV, then the 4 home questions by tap, until the products."""
    await h.tap("g:new")
    if clang:
        await h.tap(f"s:C1:lang:{clang}")
    out = await h.pick("s:C1:cat:tv")
    answers = ("room:living", "seat_distance:d8_10", "room_light:sunny", "content:sports")
    for _ in range(5):
        ids = button_ids(out)
        nxt = next((f"s:C1:slot:{a}" for a in answers if f"s:C1:slot:{a}" in ids), None)
        if nxt is None:
            return out
        out = await h.tap(nxt)
    return out


async def to_options(h, photo=None, clang: str | None = "mr"):
    """…→ (a room photo) → the note → the options (Assess is the 4 home questions, then the products)."""
    return await assess_tv(h, clang, photo=photo)


async def answer_complete(h, out, addons: str = "none"):
    """Answer the cost questions Complete asks (mounting, wall, soundbar / AC site) until the cost card comes.
    "Complete it" (the add-ons, 5 Nov 2026) is answered with `addons`: 'none' (not now) or 'all'."""
    for _ in range(7):
        if any(m.card and m.card.template == "kharcha" for m in out):
            return out
        ids = button_ids(out)
        if "s:C1:addons:all" in ids or "s:C1:addons:none" in ids:
            out = await h.tap(f"s:C1:addons:{addons}")
            continue
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


# ---------- the options, one product page each (user, 5 Nov 2026) ----------
def option_pages(msgs: list[OutMessage]) -> dict[str, dict]:
    """{label: product page data} for the best-fit products shown in these messages, in order."""
    return {m.card.data["chip_label"]: m.card.data for m in msgs
            if m.card and m.card.template == "product" and m.card.data.get("chip_label")}


def options_view(msgs: list[OutMessage]):
    """The shortlist as the old options card's data ({options: [{label, title, role, points, offer, image}]}), built
    from the product pages, so tests can keep checking what each option says; None when no options were shown."""
    from types import SimpleNamespace

    pages = option_pages(msgs)
    if not pages:
        return None
    opts = [{"label": lab, "title": d["title"], "role": d.get("chip_kind"), "is_best": d.get("chip_kind") == "best",
             "points": list(d.get("needs") or []) + list(d.get("also") or []), "image": d.get("image"),
             "offer": {"text": d.get("offer_text"), "none": d.get("offer_none")}} for lab, d in pages.items()]
    return SimpleNamespace(template="options", lang="en", data={"options": opts})


async def open_brief(h, out, cat: str = "tv"):
    """The shift start asks which product first (user, 6 Nov 2026): pick it, then the pre-read card follows."""
    rows = [r.id for m in out for sec in (m.sections or []) for r in sec.rows]
    assert f"g:briefcat:{cat}" in rows, rows  # the product picker, today's numbers on each row
    return await h.pick(f"g:briefcat:{cat}")
