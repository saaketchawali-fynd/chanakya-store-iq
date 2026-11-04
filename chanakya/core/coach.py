"""coach_reply(): the Bolo / Kyun / status message (spec 2.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chanakya.core.contracts import Button, Card, OutMessage
from chanakya.core.status import status_line

if TYPE_CHECKING:
    from chanakya.skills.base import SkillContext


def _one_line(s: str) -> str:
    return " ".join((s or "").split())


# Message layout (user, 4 Nov 2026): fast to read on the floor, well spaced, never a robotic one-liner.
#   *Bolo*:
#   > the words to say (a WhatsApp quote)
#
#   *Kyun*: the reason, in a full sentence
#   • each further fact on its own bullet
#
#   _C1 · TV · Ghar 2/4 · …_   (the status, quiet) — or the copilot tip, which replaces it
def bolo_block(ctx: SkillContext, say: str, lang: str | None = None) -> str:
    return f'{ctx.t("label_bolo", lang or ctx.lang)}:\n> {_one_line(say).strip(chr(34))}'


def kyun_block(ctx: SkillContext, why: str, lang: str | None = None) -> str:
    parts = [p.strip() for p in _one_line(why).split(" · ") if p.strip()]
    if not parts:
        return f'{ctx.t("label_kyun", lang or ctx.lang)}:'
    return "\n".join([f'{ctx.t("label_kyun", lang or ctx.lang)}: {parts[0]}'] + [f"• {p}" for p in parts[1:]])


def status_block(text: str, tip: bool = False) -> str:
    text = _one_line(text)
    return text if tip or not text else f"_{text}_"


def coach_reply(
    ctx: SkillContext,
    say: str,
    why: str,
    cites: list[str],
    buttons: list[Button],
    card: Card | None = None,
    status: bool = True,
    footer: str | None = None,
) -> OutMessage:
    body = bolo_block(ctx, say) + "\n\n" + kyun_block(ctx, why)
    st, is_tip = (status_line(ctx) if status else ""), False
    if status:  # the copilot: one quiet line, only when a fact calls for it (the status is one tap away)
        from chanakya.skills.copilot import tip

        t = tip(ctx)
        st, is_tip = (t, True) if t else (st, False)
    if st:
        body += "\n\n" + status_block(st, is_tip)
    # no footer given: the voice line is added for every associate message when it is sent (orchestrator)
    return OutMessage(
        to=ctx.actor_wa_id,
        kind="buttons" if buttons else "text",
        body=body,
        footer=footer,
        buttons=buttons[:3],
        card=card,
        coach=bool(cites),  # a reason grounded in a Home Card fact; with none known yet it is sent as plain advice
        why_cites=cites,
        allowed_numbers=ctx.reg.allowed(),
    )


def info_reply(
    ctx: SkillContext, body: str, buttons: list[Button] | None = None, card: Card | None = None, to: str | None = None
) -> OutMessage:
    kind = "buttons" if buttons else ("image" if card else "text")
    return OutMessage(
        to=to or ctx.actor_wa_id,
        kind=kind,
        body=tidy(body),
        buttons=(buttons or [])[:3],
        card=card,
        allowed_numbers=ctx.reg.allowed(),
    )


def tidy(body: str | None) -> str:
    """Spacing kept (one blank line between blocks), never two in a row, none at the ends; within the line limit."""
    from chanakya.channel.limits import FLOOR_MAX_LINES

    out: list[str] = []
    for ln in (body or "").split("\n"):
        if not ln.strip():
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(ln.rstrip())
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out[:FLOOR_MAX_LINES])
