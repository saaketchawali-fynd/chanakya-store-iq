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
    pack = ctx.pack
    home = ctx.session.home
    for sid in pack.site_needs:
        if sid not in pack.cost_slots or P.known(home, sid):
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
        ctx.b("b_show_customer", ctx.sid("cost_show")),
        ctx.b("b_drop_optional", ctx.sid("cost_drop_optional")),
        ctx.b("b_offers", ctx.sid("offers")),
    ]
    say = ctx.pick(ctx.packs.common_line("complete_line"), ctx.clang)
    msg = coach_reply(ctx, say, why, cites, btns, card=card)
    return ctx.reply(msg)


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
