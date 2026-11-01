"""Step 6 — Poora Kharcha (Complete): site needs that change the cost first, then the Kharcha Card."""

from __future__ import annotations

from chanakya.brain import packs as P
from chanakya.core import state
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Card, OutMessage, Reply, Step
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext
from chanakya.skills.recommend import gate_price_reply, show_options
from chanakya.truth import fmt, hisaab


async def choose(ctx: SkillContext, label: str) -> Reply:
    s = ctx.session
    if not s.option_labels:
        return await show_options(ctx)
    sku = s.option_labels.get(label.upper())
    if not sku:
        return await show_options(ctx)
    s.chosen_sku = sku
    s.extra["chosen_label"] = label.upper()
    return await show_cost(ctx)


async def choose_sku(ctx: SkillContext, sku: str) -> Reply:
    s = ctx.session
    if ctx.facts.product_sync(sku) is None:
        return await show_options(ctx)
    s.chosen_sku = sku
    for lab, v in s.option_labels.items():
        if v == sku:
            s.extra["chosen_label"] = lab
    return await show_cost(ctx)


def _pending_site_slot(ctx: SkillContext) -> str | None:
    """The next question that changes the cost (the pack's complete_slots, in order), if not yet known."""
    pack = ctx.pack
    home = ctx.session.home
    for sid in pack.complete_slots or [x for x in pack.site_needs if x in pack.cost_slots]:
        if P.known(home, sid):
            continue
        sd = pack.slot(sid)
        if sd and sd.ask_if:
            cond = {k: v for k, v in sd.ask_if.items() if k != "slot"}
            if not P.eval_slot_cond(home, sd.ask_if["slot"], cond, pack):
                continue
        return sid
    return None


async def show_cost(ctx: SkillContext, resend: bool = False) -> Reply:
    s = ctx.session
    pack = ctx.pack
    if not state.price_gate_open(s, pack):
        ctx.x["pending_after_override"] = "cost"
        return gate_price_reply(ctx)
    if not s.chosen_sku:
        s.chosen_sku = s.option_labels.get("B")
    if not s.chosen_sku:
        return await show_options(ctx)
    site = _pending_site_slot(ctx)
    if site:  # the AC story: pipe run, outdoor spot, wall hole are asked BEFORE the cost
        ctx.x["pending_cost"] = True
        return ctx.reply(C.question_reply(ctx, site))
    ctx.x.pop("pending_cost", None)
    include_sug = not ctx.x.get("suggestions_off")
    sheet = await hisaab.compute(
        s.chosen_sku,
        s.home,
        ctx.facts,
        pack,
        ctx.reg,
        lang=ctx.clang,
        include_suggestions=include_sug,
        include_optional=bool(ctx.x.get("optional_on")),
        exclude_codes=tuple(ctx.x.get("dropped_codes") or ()),
    )
    s.cost_snapshot = sheet.model_dump(mode="json")
    state.advance(s, Step.COMPLETE)
    first_price = "first_price_event" not in ctx.x
    ctx.ev(
        "cost_shared",
        sources=sheet.sources,
        sku=sheet.sku,
        zaroori_total=sheet.zaroori_total,
        with_suggestions_total=sheet.with_suggestions_total,
    )
    if first_price:
        from chanakya.skills.validate import mark_first_price

        who = ctx.x.get("price_first_by") or ("none" if s.demo_done else "associate")
        mark_first_price(ctx, who)
    card = kharcha_card(ctx, sheet)
    used_site = [sid for sid in pack.cost_slots if P.known(s.home, sid)]
    why = ctx.t("kyun_complete")
    site_reason = _site_reason(ctx, sheet)
    if site_reason:
        why += " · " + site_reason
    cites = used_site or C.known_cites(ctx)
    btns = [
        ctx.b("b_looks_good", ctx.sid("offers")),
        ctx.b("b_remove_item", ctx.sid("cost_rm")),
        ctx.b("b_show_customer", ctx.sid("cost_show")),
    ]
    say = ctx.pick(ctx.packs.common_line("complete_line"), ctx.clang)
    msg = coach_reply(ctx, say, why, cites, btns, card=card)
    if ctx.x.pop("removed_note", None):
        msg.footer = ctx.x.pop("removed_footer", None) or msg.footer
    return ctx.reply(msg)


async def remove_menu(ctx: SkillContext) -> Reply:
    """'Remove an item': the suggested / optional lines, one tap each; required charges cannot be removed."""
    from chanakya.channel.limits import truncate, truncate_title
    from chanakya.core.contracts import ListRow, ListSection

    s = ctx.session
    if not s.cost_snapshot:
        return await show_cost(ctx)
    sheet = hisaab.CostSheet.model_validate(s.cost_snapshot)
    rows = []
    for ln in sheet.lines:
        if ln.kind in ("sujhav", "optional"):
            ctx.reg.raw(ln.amount_str, f"snapshot:{ln.code}")
            rows.append(ListRow(id=ctx.sid("cost_drop", ln.code), title=truncate(ln.title, 24),
                                description=truncate(f"{ln.amount_str}" + (f" · {ln.reason}" if ln.reason else ""), 72)))
    if sheet.optional_count and not ctx.x.get("optional_on"):
        rows.append(ListRow(id=ctx.sid("cost_drop", "_optional"), title=ctx.t("row_add_optional")))
    if ctx.x.get("dropped_codes"):
        rows.append(ListRow(id=ctx.sid("cost_drop", "_restore"), title=ctx.t("row_restore")))
    for r in rows:
        ctx.reg.static(r.title)
    if not rows:
        return ctx.reply(info_reply(ctx, ctx.t("nothing_to_remove"), [ctx.b("b_looks_good", ctx.sid("offers"))]))
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("remove_body"),
                                list_button=truncate_title(ctx.t("list_remove")), sections=[ListSection(rows=rows[:10])],
                                allowed_numbers=ctx.reg.allowed()))


async def drop_line(ctx: SkillContext, code: str) -> Reply:
    s = ctx.session
    x = ctx.x
    if code == "_optional":
        x["optional_on"] = True
    elif code == "_restore":
        x["dropped_codes"] = []
    else:
        title = code
        for ln in (s.cost_snapshot or {}).get("lines", []):
            if ln.get("code") == code:
                title = ln.get("title") or code
        x["dropped_codes"] = sorted(set(x.get("dropped_codes") or []) | {code})
        x["removed_note"] = True
        x["removed_footer"] = ctx.t("removed_footer", item=ctx.reg.static(title))[:60]
        ctx.ev("cost_line_removed", code=code)
    return await show_cost(ctx)


def _site_reason(ctx: SkillContext, sheet: hisaab.CostSheet) -> str | None:
    for line in sheet.lines:
        if line.code == "AC_PIPE_FT" and line.qty:
            return ctx.t("site_reason_pipe", ft=ctx.reg.num(line.qty, "card:pipe_run_ft", "ft"))
    return None


def kharcha_card(ctx: SkillContext, sheet: hisaab.CostSheet) -> Card:
    lang = ctx.clang
    s = ctx.session
    p = ctx.facts.product_sync(sheet.sku)
    lines = [
        {"tag": ln.kind, "title": ln.title, "amount_str": ln.amount_str, "reason": ln.reason} for ln in sheet.lines
    ]
    for ln in sheet.lines:
        ctx.reg.raw(ln.title, f"line:{ln.code}")
    exch = (
        ctx.t("exchange_text", lang, low=sheet.exchange_range[0], high=sheet.exchange_range[1])
        if sheet.exchange_range
        else None
    )
    life = None
    if sheet.lifetime:
        lt = sheet.lifetime
        life = ctx.t("lifetime_text", lang, monthly=lt["monthly_str"], five=lt["five_year_str"])
        if lt.get("five_year_saving_str") and lt.get("five_year_saving", 0) > 0:
            life += " · " + ctx.t(
                "lifetime_saving",
                lang,
                saving=lt["five_year_saving_str"],
                alt=ctx.reg.raw(lt["alt_model"], f"product:{lt['alt_sku']}"),
            )
    stamp = ctx.t(
        "kharcha_stamp",
        lang,
        date=ctx.reg.raw(fmt.date_short(ctx.now, lang), "clock:date"),
        time=ctx.reg.raw(fmt.time_short(ctx.now), "clock:time"),
    )
    with_sug = sheet.with_suggestions_total_str if sheet.with_suggestions_total != sheet.zaroori_total else None
    return Card(
        template="kharcha",
        lang=lang,
        data={  # type: ignore[arg-type]
            "customer_name": s.home.customer_name or "",
            "product_title": C.product_title(ctx, p, lang),
            "lines": lines,
            "zaroori_total_str": sheet.zaroori_total_str,
            "with_suggestions_total_str": with_sug,
            "exchange_text": exch,
            "lifetime_text": life,
            "stamp": stamp,
            "optional_count": sheet.optional_count,
            "product_image": p.image,
        },
    )


async def resend_card(ctx: SkillContext) -> Reply:
    s = ctx.session
    if not s.cost_snapshot:
        return await show_cost(ctx)
    sheet = hisaab.CostSheet.model_validate(s.cost_snapshot)
    for ln in sheet.lines:
        ctx.reg.raw(ln.amount_str, f"snapshot:{ln.code}")
    ctx.reg.raw(sheet.zaroori_total_str, "snapshot:total")
    ctx.reg.raw(sheet.with_suggestions_total_str, "snapshot:total")
    if sheet.exchange_range:
        ctx.reg.raw(" ".join(sheet.exchange_range), "snapshot:exchange")
    for v in (sheet.lifetime or {}).values():
        if isinstance(v, str):
            ctx.reg.raw(v, "snapshot:lifetime")
    say = ctx.pick(ctx.packs.common_line("complete_line"), ctx.clang)
    msg = OutMessage(
        to=ctx.actor_wa_id,
        kind="image",
        card=kharcha_card(ctx, sheet),
        body=f'"{say}"',
        allowed_numbers=ctx.reg.allowed(),
    )
    return ctx.reply(msg)


async def toggle_suggestions(ctx: SkillContext) -> Reply:
    ctx.x["suggestions_off"] = not ctx.x.get("suggestions_off")
    r = await show_cost(ctx)
    r.messages.insert(0, info_reply(ctx, ctx.t("suggestions_off" if ctx.x["suggestions_off"] else "suggestions_on")))
    return r
