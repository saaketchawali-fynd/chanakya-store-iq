"""The associate's own wins (user, 4 Nov 2026): "3 Family Cards sent today, 2 answered · 1 sale · 4-day streak".
Counted from their own conversations only (events and outcomes), never compared with anyone else."""

from __future__ import annotations

from datetime import date, timedelta

from chanakya.core import repo
from chanakya.core.clock import IST
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Reply
from chanakya.skills.base import SkillContext

STREAK_DAYS = 30


def _day(e, today: date) -> date | None:
    if not hasattr(e, "created_at"):  # not saved yet (this very message): today
        return today
    at = repo.aware(e.at) or repo.aware(e.created_at)
    return at.astimezone(IST).date() if at else None


async def counts(ctx: SkillContext, wa: str | None = None, day: date | None = None) -> dict:
    """{'cards': n, 'answered': n, 'sales': n, 'streak': days} for today; the streak counts days in a row (to today or
    yesterday) with at least one Family Card sent or one sale."""
    wa = wa or ctx.actor_wa_id
    today = ctx.now.astimezone(IST).date()
    on = day or today
    sold_sessions: set = set()
    evs = await repo.events_for(types=["parivaar_sent", "family_replied", "session_closed", "sale_marked"],
                                since=ctx.now - timedelta(days=STREAK_DAYS))
    evs = list(evs) + [e for e in ctx.events if e.type in ("parivaar_sent", "family_replied", "session_closed", "sale_marked")]
    mine_sessions = {st.id for _, st in await ctx.svc.sessions.since(ctx.now - timedelta(days=STREAK_DAYS))
                     if st.associate_wa_id == wa}
    active: set[date] = set()
    c = {"cards": 0, "answered": 0, "sales": 0}
    for e in evs:
        d = _day(e, today)
        pl = e.payload or {}
        mine = e.actor_wa_id == wa or pl.get("associate") == wa or (e.session_id in mine_sessions)
        if not mine or d is None:
            continue
        sale = e.type == "sale_marked" or (e.type == "session_closed" and pl.get("outcome") == "sold")
        if e.type == "parivaar_sent" or sale:
            active.add(d)
        if d == on:
            if e.type == "parivaar_sent":
                c["cards"] += 1
            elif e.type == "family_replied":
                c["answered"] += 1
            elif sale and e.session_id not in sold_sessions:  # one sale per customer
                sold_sessions.add(e.session_id)
                c["sales"] += 1
    streak, d = 0, today if today in active else today - timedelta(days=1)
    while d in active:
        streak += 1
        d -= timedelta(days=1)
    c["streak"] = streak
    return c


async def line(ctx: SkillContext, yesterday: bool = False) -> str | None:
    """One short line, or None when there is nothing yet (no zeros shown as a 'win')."""
    day = ctx.now.astimezone(IST).date() - timedelta(days=1) if yesterday else None
    c = await counts(ctx, day=day)
    parts = []
    if c["cards"]:
        parts.append(ctx.t("wins_cards", n=ctx.reg.num(c["cards"], "wins:cards")))
    if c["answered"]:
        parts.append(ctx.t("wins_answered", n=ctx.reg.num(c["answered"], "wins:answered")))
    if c["sales"]:
        parts.append(ctx.t("wins_sales", n=ctx.reg.num(c["sales"], "wins:sales")))
    if c["streak"] >= 2:
        parts.append(ctx.t("wins_streak", n=ctx.reg.num(c["streak"], "wins:streak")))
    return ctx.t("wins_yesterday" if yesterday else "wins_today", what=" · ".join(parts)) if parts else None


async def show(ctx: SkillContext) -> Reply:
    ln = await line(ctx)
    return ctx.reply(info_reply(ctx, ln or ctx.t("wins_none"), [ctx.b("b_new_customer", "g:new")]))
