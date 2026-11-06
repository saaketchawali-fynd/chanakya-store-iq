"""Validator (spec 7.6): floor rules, firewall, banned phrases, Kyun cites. Runs on every OutMessage."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from chanakya.channel import limits
from chanakya.core.contracts import HomeCard, OutMessage
from chanakya.i18n import lang as i18n
from chanakya.log import get_log
from chanakya.truth.firewall import NumberRegistry

log = get_log("validator")
DEV_DIGITS = re.compile(r"[०-९]")
_DEV_MAP = str.maketrans("०१२३४५६७८९", "0123456789")


@dataclass
class Violation:
    kind: str
    detail: str


@dataclass
class Check:
    msg: OutMessage
    violations: list[Violation] = field(default_factory=list)
    fatal: bool = False


IMAGE_KEYS = ("image", "product_image", "photo")


def _is_ref(s: str) -> bool:
    """A picture reference (URL / data URI / file path), never shown as text: its digits are not claims."""
    return s.startswith(("data:", "http://", "https://")) or s.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))


def _strings(obj: Any):
    if isinstance(obj, str):
        if not _is_ref(obj):
            yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in IMAGE_KEYS:
                yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _visible_texts(m: OutMessage) -> list[str]:
    out = [m.body or "", m.caption or "", m.footer or "", m.list_button or "", m.url_text or ""]
    out += [b.title for b in m.buttons]
    for sec in m.sections:
        out += [sec.title or ""] + [r.title for r in sec.rows] + [r.description or "" for r in sec.rows]
    if m.card:
        out += [s for k, s in _card_strings(m.card.data)]
    return out


def _card_strings(data: dict):
    for k, v in data.items():
        if k in IMAGE_KEYS or isinstance(v, (bytes, bytearray)):
            continue  # pictures (at any depth: options, catalogue items, compare sides) are not text
        for s in _strings(v):
            yield k, s


def check(
    m: OutMessage,
    reg: NumberRegistry,
    home: HomeCard | None,
    lang: str,
    banned: list[str],
    asking: set[str] | None = None,
) -> Check:
    c = Check(msg=m.model_copy(deep=True))
    msg = c.msg

    # 7 — Devanagari digits → auto-fix to Latin
    for attr in ("body", "caption", "footer"):
        v = getattr(msg, attr)
        if v and DEV_DIGITS.search(v):
            setattr(msg, attr, v.translate(_DEV_MAP))
            c.violations.append(Violation("devanagari_digits", attr))

    # 1 — lines / length
    for attr, mx in (("body", limits.BODY_MAX), ("caption", limits.CAPTION_MAX)):
        v = getattr(msg, attr)
        if not v:
            continue
        lines = [ln for ln in v.split("\n") if ln.strip()]
        if len(lines) != len(v.split("\n")):  # no blank lines: 3 lines, nothing wasted
            c.violations.append(Violation("empty_line", attr))
        if len(lines) > limits.FLOOR_MAX_LINES:  # never sent long: the rest joins the last line (and is logged)
            c.violations.append(Violation("too_many_lines", f"{attr}:{len(lines)}"))
            lines = lines[: limits.FLOOR_MAX_LINES - 1] + [" · ".join(x.strip() for x in lines[limits.FLOOR_MAX_LINES - 1:])]
        v = "\n".join(lines)
        setattr(msg, attr, v)
        if len(v) > mx:
            setattr(msg, attr, limits.truncate(v, mx))
            c.violations.append(Violation("too_long", attr))
    if msg.footer and len(msg.footer) > limits.FOOTER_MAX:
        msg.footer = limits.truncate(msg.footer, limits.FOOTER_MAX)
        c.violations.append(Violation("footer_long", ""))

    # 2 — buttons
    if len(msg.buttons) > limits.MAX_BUTTONS:
        c.violations.append(Violation("too_many_buttons", str(len(msg.buttons))))
        msg.buttons = msg.buttons[: limits.MAX_BUTTONS]
    seen: set[str] = set()
    fixed = []
    for b in msg.buttons:
        title = b.title
        if len(title) > limits.BUTTON_TITLE_MAX:
            title = limits.truncate_title(title)
            c.violations.append(Violation("title_long", b.title))
        if title in seen:
            c.violations.append(Violation("dup_title", title))
            continue
        if len(b.id) > limits.BUTTON_ID_MAX:
            c.violations.append(Violation("id_long", b.id[:40]))
            c.fatal = True
        seen.add(title)
        fixed.append(b.model_copy(update={"title": title}))
    msg.buttons = fixed
    if msg.kind == "buttons" and not msg.buttons:
        msg.kind = "image" if msg.card or msg.image_bytes or msg.media_id else "text"

    # 3 — lists
    rows = [r for s in msg.sections for r in s.rows]
    if len(rows) > limits.LIST_ROWS_MAX:
        c.violations.append(Violation("too_many_rows", str(len(rows))))
        c.fatal = True
    for s in msg.sections:
        for r in s.rows:
            if len(r.title) > limits.LIST_ROW_TITLE_MAX:
                r.title = limits.truncate(r.title, limits.LIST_ROW_TITLE_MAX)
    if msg.list_button and len(msg.list_button) > limits.LIST_BUTTON_MAX:
        msg.list_button = limits.truncate_title(msg.list_button)

    # 4 — coaching replies carry a Kyun citing a known slot
    if msg.coach:
        label = i18n.raw("label_kyun", lang)
        if label not in (msg.body or "") and label not in (msg.caption or ""):
            c.violations.append(Violation("no_kyun", ""))
            c.fatal = True
        if not msg.why_cites:
            c.violations.append(Violation("no_cites", ""))
            c.fatal = True
        else:
            for slot in msg.why_cites:
                ok = bool(home and home.slots.get(slot)) or (asking and slot in asking)
                if not ok:
                    c.violations.append(Violation("cite_unknown", slot))
                    c.fatal = True

    # 5 — firewall
    allowed = NumberRegistry()
    allowed.merge(reg)
    for n in msg.allowed_numbers:
        allowed.static(n)
    for text in _visible_texts(msg):
        bad = allowed.check(text)
        if bad:
            c.violations.append(Violation("firewall", f"{bad} in {text[:60]!r}"))
            c.fatal = True

    # 6 — banned phrases
    for text in _visible_texts(msg):
        low = text.lower()
        for phrase in banned:
            if phrase and phrase in low:
                c.violations.append(Violation("banned_phrase", phrase))
                c.fatal = True
    return c


def enforce(
    m: OutMessage,
    reg: NumberRegistry,
    home: HomeCard | None,
    lang: str,
    banned: list[str],
    asking: set[str] | None,
    fallback,
) -> tuple[OutMessage, list[Violation]]:
    """Returns the message to send (auto-fixed, or the fallback) and the violations found. A fallback of None means
    nothing is sent (a message to the customer's phone is never swapped for a staff fallback)."""
    c = check(m, reg, home, lang, banned, asking)
    for v in c.violations:
        log.warning("validator_violation", kind=v.kind, detail=v.detail)
    if c.fatal:
        fb = fallback()
        if fb is not None:
            fb.to = m.to
        return fb, c.violations
    return c.msg, c.violations
