"""coach_reply(): the Bolo / Kyun / status message (spec 2.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chanakya.core.contracts import Button, Card, OutMessage
from chanakya.core.status import status_line

if TYPE_CHECKING:
    from chanakya.skills.base import SkillContext


def _one_line(s: str) -> str:
    return " ".join((s or "").split())


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
    L = ctx.lang
    body = f'{ctx.t("label_bolo", L)}: "{_one_line(say)}"\n{ctx.t("label_kyun", L)}: {_one_line(why)}'
    st = status_line(ctx) if status else ""
    if status:  # the copilot: one quiet line, only when a fact calls for it (the status is one tap away)
        from chanakya.skills.copilot import tip

        st = tip(ctx) or st
    if st:
        body += "\n" + st
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
    lines = [ln for ln in (body or "").split("\n") if ln.strip()]
    kind = "buttons" if buttons else ("image" if card else "text")
    return OutMessage(
        to=to or ctx.actor_wa_id,
        kind=kind,
        body="\n".join(lines[:3]),
        buttons=(buttons or [])[:3],
        card=card,
        allowed_numbers=ctx.reg.allowed(),
    )
