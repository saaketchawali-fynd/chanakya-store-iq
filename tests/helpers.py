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
                assert len(text.split("\n")) <= 3, f"> 3 lines: {text!r}"
                assert not re.search(r"[०-९]", text)
                low = text.lower()
                assert not any(b in low for b in BANNED), text
        assert len(m.buttons) <= 3
        assert all(len(b.title) <= 20 for b in m.buttons), [b.title for b in m.buttons]
        assert len({b.title for b in m.buttons}) == len(m.buttons)
        if m.coach:
            assert m.why_cites, m.body
            assert any(raw("label_kyun", lg) in (m.body or "") for lg in ("hi", "mr", "en"))


def texts(msgs: list[OutMessage]) -> str:
    out = []
    for m in msgs:
        out += [m.body or "", m.caption or ""] + [b.title for b in m.buttons]
    return "\n".join(out)


def button_ids(msgs: list[OutMessage]) -> list[str]:
    return [b.id for m in msgs for b in m.buttons]
