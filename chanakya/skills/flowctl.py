"""Flow control for the associate: menu, end the current customer, start a new one, start over from the beginning.

- menu (also: madad / help): a list of what can be done right now
- exit / band: end the current customer (one-tap confirm; Khatam + score is offered instead)
- restart / shuru se: close every open customer, then the very beginning: language → brief → new customer
"""

from __future__ import annotations

from chanakya.channel.limits import truncate
from chanakya.core.buttons import encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import ListRow, ListSection, OutMessage, Reply
from chanakya.core.status import short_name
from chanakya.skills.base import SkillContext


def _who(ctx: SkillContext) -> str:
    s = ctx.session
    if not s:
        return ""
    name = short_name(s.home.customer_name) if s.home.customer_name else ""
    code = ctx.reg.raw(s.code, "session:code")
    return f"{code} · {name}" if name and name != "–" else code


def _row(ctx: SkillContext, rid: str, key: str, desc_key: str | None = None, **vars) -> ListRow:
    title = truncate(ctx.t(key, **vars), 24)
    desc = truncate(ctx.t(desc_key, **vars), 72) if desc_key else None
    ctx.reg.static(title + " " + (desc or ""))
    return ListRow(id=rid, title=title, description=desc)


async def menu(ctx: SkillContext) -> Reply:
    s = ctx.session
    who = _who(ctx)
    rows = [_row(ctx, encode("g", "new"), "menu_new", "menu_new_d" if s else "menu_new_d0", who=who)]
    if s:
        rows.append(_row(ctx, encode("g", "exitnew"), "menu_exitnew", "menu_exitnew_d", who=who))
        rows.append(_row(ctx, ctx.sid("khatam"), "menu_khatam", "menu_khatam_d", who=who))
    rows.append(_row(ctx, encode("g", "restart"), "menu_restart", "menu_restart_d"))
    active = await ctx.svc.sessions.active(ctx.actor_wa_id)
    if len(active) > (1 if s else 0):
        rows.append(_row(ctx, encode("g", "switch"), "menu_switch", "menu_switch_d"))
    if s:
        lname = ctx.t(f"lang_name_{s.home.customer_lang}")
        rows.append(_row(ctx, ctx.sid("clang"), "menu_clang", "menu_clang_d", language=lname))  # not lang=: t()'s own
    rows.append(_row(ctx, encode("g", "status"), "menu_status", "menu_status_d"))
    rows.append(_row(ctx, encode("g", "catalog", "", 0), "menu_catalog", "menu_catalog_d"))
    rows.append(_row(ctx, encode("g", "langpick"), "menu_lang", "menu_lang_d"))
    body = ctx.t("help_body")
    return ctx.reply(
        OutMessage(
            to=ctx.actor_wa_id,
            kind="list",
            body=body,
            list_button=ctx.t("b_menu"),
            sections=[ListSection(rows=rows[:10])],
            allowed_numbers=ctx.reg.allowed(),
        )
    )


async def exit_ask(ctx: SkillContext) -> Reply:
    s = ctx.session
    if not s:
        return ctx.reply(
            info_reply(
                ctx,
                ctx.t("status_none"),
                [
                    ctx.b("b_new_customer", encode("g", "new")),
                    ctx.b("b_restart", encode("g", "restart")),
                    ctx.b("b_menu", encode("g", "menu")),
                ],
            )
        )
    btns = [
        ctx.b("b_exit_yes", encode("g", "exitok")),
        ctx.b("b_khatam_score", ctx.sid("khatam")),
        ctx.b("b_back", ctx.sid("go")),
    ]
    return ctx.reply(info_reply(ctx, ctx.t("exit_confirm", who=_who(ctx)), btns))


async def _close_current(ctx: SkillContext, outcome: str) -> str | None:
    s = ctx.session
    if not s:
        return None
    who = _who(ctx)
    ctx.ev("session_closed", outcome=outcome)
    await ctx.svc.sessions.close(s, outcome, ctx.now)
    ctx.session = None
    if ctx.staff:
        ctx.staff.current_session_id = None
    return who


async def exit_ok(ctx: SkillContext) -> Reply:
    who = await _close_current(ctx, "exited")
    active = await ctx.svc.sessions.active(ctx.actor_wa_id)
    btns = [ctx.b("b_new_customer", encode("g", "new"))]
    btns.append(ctx.b("b_switch", encode("g", "switch")) if active else ctx.b("b_restart", encode("g", "restart")))
    btns.append(ctx.b("b_menu", encode("g", "menu")))
    body = ctx.t("exit_done", who=who) if who else ctx.t("status_none")
    return ctx.reply(info_reply(ctx, body, btns))


async def exit_and_new(ctx: SkillContext) -> Reply:
    from chanakya.skills import assess

    who = await _close_current(ctx, "exited")
    r = await assess.new_session(ctx)
    if who:
        r.messages.insert(0, info_reply(ctx, ctx.t("exit_done_short", who=who)))
    return r


async def restart_ask(ctx: SkillContext) -> Reply:
    active = await ctx.svc.sessions.active(ctx.actor_wa_id)
    if not active:
        return await restart_ok(ctx)
    n = ctx.reg.num(len(active), "sessions:active")
    btns = [ctx.b("b_restart_yes", encode("g", "restartok")), ctx.b("b_back", encode("g", "status"))]
    return ctx.reply(info_reply(ctx, ctx.t("restart_confirm", n=n), btns))


async def restart_ok(ctx: SkillContext) -> Reply:
    """The very beginning: every open customer closed, then the language picker → brief → new customer."""
    from chanakya.core.router import language_picker

    for s in await ctx.svc.sessions.active(ctx.actor_wa_id):
        ctx.session = s
        ctx.ev("session_closed", outcome="restart")
        await ctx.svc.sessions.close(s, "restart", ctx.now)
    ctx.session = None
    if ctx.staff:
        ctx.staff.current_session_id = None
        ctx.staff.lang_set = False
    ctx.ev("flow_restarted")
    return language_picker(ctx)
