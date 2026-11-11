"""coach_reply(): the Bolo / Kyun / status message (spec 2.3)."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from chanakya.core.contracts import Button, Card, OutMessage
from chanakya.core.status import status_line

if TYPE_CHECKING:
    from chanakya.skills.base import SkillContext


def _one_line(s: str) -> str:
    return " ".join((s or "").split())


# The contest's 9 steps, in its order (user, 11 Nov 2026: "stick to the 9 steps clearly"). Each skill entry point is
# marked with the step it serves; the associate's message names it in its header ("Step 2 of 9 · Assess"). A call
# into another step's skill (Close showing the full cost first) names that step: the last one wins.
SESSION_STEP = {"assess": 2, "recommend": 5, "demonstrated": 5, "complete": 6, "close": 7, "follow": 8, "closed": 9}


def step(n: int):
    def wrap(fn):
        @functools.wraps(fn)
        async def inner(ctx, *a, **k):
            ctx.scratch["step_no"] = n
            return await fn(ctx, *a, **k)

        return inner

    return wrap


def step_header(ctx: SkillContext) -> str | None:
    """'Step 5 of 9 · Recommend and demonstrate' for the associate's message (None for other roles)."""
    if ctx.role != "associate":
        return None
    n = ctx.scratch.get("step_no")
    if n is None and ctx.session is not None:
        n = SESSION_STEP.get(getattr(ctx.session.step, "value", ctx.session.step))
    if not n:
        return None
    from chanakya.i18n.lang import t

    return t("step_hdr", ctx.lang, None, n=n, name=t(f"step_{n}", ctx.lang))


# Message layout (user, 5 Nov 2026: the contest's floor rules, strictly): at most 3 lines, no blank lines, 3 buttons.
#   *Bolo*: "the words to say"
#   *Kyun*: the reason · one more fact
#   *Karo*: what to do with the customer right now — or the copilot tip, when a fact calls for it
#   ─ footer (grey): Bolkar bhi · C1 Shah · TV · Ghar 2/4   (voice first, and where the customer stands)
# Everything longer is on the card above the text (Home Card, product page, demo guide, kharcha, score).
def fit_line(parts: list[str], first: str, limit: int | None = None) -> str:
    """One line: the first part, then more facts joined with ' · ' while the line still fits."""
    from chanakya.channel.limits import LINE_MAX

    limit = limit or LINE_MAX
    line = first
    for p in parts:
        if len(line) + 3 + len(p) > limit:
            break
        line = f"{line} · {p}"
    return line


def bolo_block(ctx: SkillContext, say: str, lang: str | None = None) -> str:
    return f'{ctx.t("label_bolo", lang or ctx.lang)}: "{_one_line(say).strip(chr(34))}"'


def kyun_block(ctx: SkillContext, why: str, lang: str | None = None) -> str:
    parts = [p.strip() for p in _one_line(why).split(" · ") if p.strip()]
    label = ctx.t("label_kyun", lang or ctx.lang)
    if not parts:
        return f"{label}:"
    return fit_line(parts[1:], f"{label}: {parts[0]}")


def do_block(ctx: SkillContext, key: str | None, lang: str | None = None) -> str:
    """The Karo line for this moment: what to do with the customer while saying it (look at them, let them finish,
    turn the phone to them, hand over the remote, pause after the price). A moment with variants (key_1, key_2, …)
    takes the next one each time, so the same line never comes twice in a row. '' when the moment has no line."""
    from chanakya.i18n import lang as i18n

    if not key:
        return ""
    variants = [f"{key}_{i}" for i in range(1, 6) if i18n.has(f"{key}_{i}")]
    if variants:
        x = ctx.session.extra if ctx.session else {}
        n = int(x.get("do_n", 0))
        x["do_n"] = n + 1
        key = variants[n % len(variants)]
    elif not i18n.has(key):
        return ""
    return f'{ctx.t("label_do", lang or ctx.lang)}: {_one_line(ctx.t(key, lang))}'


def status_block(text: str, tip: bool = False) -> str:
    text = _one_line(text)
    return text if tip or not text else f"_{text}_"


def status_footer(ctx: SkillContext) -> str | None:
    """The grey footer of an associate's message: voice first, then where the customer stands, as much as fits in
    WhatsApp's 60 characters ('Bolkar bhi · C1 Shah · TV · Ghar 4/4 · Demo baaki')."""
    from chanakya.channel.limits import FOOTER_MAX

    st = status_line(ctx)
    if not st:
        return None
    parts = [p for p in st.split(" · ") if p]  # who · category · home · demo · price
    full = " · ".join([ctx.t("footer_voice_short")] + parts)
    if len(full) > FOOTER_MAX and len(parts) == 5:  # short of room: the category goes first (the associate knows it)
        parts = parts[:1] + parts[2:]
    return fit_line(parts, ctx.t("footer_voice_short"), FOOTER_MAX)


def coach_reply(
    ctx: SkillContext,
    say: str,
    why: str,
    cites: list[str],
    buttons: list[Button],
    card: Card | None = None,
    status: bool = True,
    footer: str | None = None,
    do: str | None = None,
) -> OutMessage:
    """Three lines: Bolo, Kyun, Karo. The copilot tip joins the Kyun line when it fits (else it takes the third line
    of a message with no Karo, or waits for a later message). `do`: the key of this moment's Karo line."""
    from chanakya.channel.limits import LINE_MAX

    kyun, karo = kyun_block(ctx, why), do_block(ctx, do)
    if status:  # the copilot: one quiet fact, only when a fact calls for it
        from chanakya.skills.copilot import tip

        before = list(ctx.session.extra.get("tips_shown", [])) if ctx.session else []
        t = tip(ctx)
        if t and len(kyun) + 3 + len(t) <= LINE_MAX:
            kyun = f"{kyun} · {t}"
        elif t and not karo:
            karo = status_block(t, True)
        elif t and ctx.session:
            ctx.session.extra["tips_shown"] = before  # not shown: it can come with a later message
    lines = [bolo_block(ctx, say), kyun] + ([karo] if karo else [])
    # no footer given: voice first and the status go in the footer when it is sent (orchestrator.voice_footer)
    return OutMessage(
        to=ctx.actor_wa_id,
        kind="buttons" if buttons else "text",
        body="\n".join(lines),
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
    """No blank lines (the floor rule: 3 lines, nothing wasted), none at the ends. The line count is checked by the
    validator, never cut silently here."""
    return "\n".join(ln.rstrip() for ln in (body or "").split("\n") if ln.strip())
