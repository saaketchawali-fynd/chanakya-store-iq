"""Step 3 — Pehle Maano (Validate): myths, price-first rescue, truth checks, honest version, looks guard."""

from __future__ import annotations

import re

from chanakya.ai.rules import contains_any
from chanakya.brain.fit import fit
from chanakya.brain.options import candidate_set
from chanakya.core.buttons import encode
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Reply
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext


def minute(ctx: SkillContext) -> float:
    from datetime import datetime

    started = ctx.x.get("started_at")
    if not started:
        return 0.0
    return round((ctx.now - datetime.fromisoformat(started)).total_seconds() / 60, 1)


def mark_first_price(ctx: SkillContext, who: str) -> None:
    """Record who spoke about price first (once per session)."""
    x = ctx.x
    if "first_price_event" in x:
        return
    x["first_price_event"] = {"who": who, "minute": minute(ctx), "after_demo": ctx.session.demo_done}
    if "price_first_by" not in x:
        x["price_first_by"] = who
    ctx.ev("price_first_by", who=x["price_first_by"], minute=minute(ctx), after_demo=ctx.session.demo_done)


def _demo_best_name(ctx: SkillContext) -> str:
    s = ctx.session
    sku = s.option_labels.get("B") or ctx.x.get("label_sku")
    p = ctx.facts.product_sync(sku) if sku else None
    return C.product_name(ctx, p) if p else ctx.t("this_product", ctx.clang)


async def myth(ctx: SkillContext, claim_id: str) -> Reply:
    m = ctx.packs.myth(claim_id, ctx.session.home.category)
    if m is None:
        return await unknown_claim(ctx, claim_id)
    line = ctx.pick(m.line, ctx.clang)
    if "{monthly_bill}" in line:
        from chanakya.truth.hisaab import lifetime_cost

        sku = ctx.session.chosen_sku or ctx.session.option_labels.get("B") or ctx.x.get("label_sku")
        p = ctx.facts.product_sync(sku) if sku else None
        lt = await lifetime_cost(p, ctx.facts, ctx.reg) if p else None
        line = (
            line.replace("{monthly_bill}", lt["monthly_str"])
            if lt
            else re.sub(r"[^।.:]*\{monthly_bill\}[^।.]*[।.]?", "", line)
        )
    why = ctx.t("kyun_myth")
    prefer: list[str] = []
    demo = ctx.pack.demo(m.proof) if (m.proof and m.proof != "kharcha" and ctx.pack) else None
    if demo:
        why += " · " + ctx.t("proof_label", demo=ctx.pick(demo.title) or demo.id)
        prefer = list(demo.when_slots.keys())
    cites = C.known_cites(ctx, prefer + ["content"])
    btns = [
        ctx.b("b_proof", ctx.sid("proof", m.id)),
        ctx.b("b_go", ctx.sid("go")),
        ctx.b("b_expert", ctx.sid("expert")),
    ]
    ctx.ev("claim_validated", claim_id=m.id, proof=m.proof)
    ctx.x["claim_validated"] = True
    if not cites:
        slot = C.pick_next_slot(ctx)
        if slot:
            ctx.asking.add(slot)
            cites = [slot]
    if not cites:
        return ctx.reply(info_reply(ctx, f'{ctx.t("label_bolo")}: "{line}"', btns))
    return ctx.reply(coach_reply(ctx, line, why, cites, btns))


async def proof(ctx: SkillContext, claim_id: str | None) -> Reply:
    m = ctx.packs.myth(claim_id, ctx.session.home.category) if claim_id else None
    if m and m.proof == "kharcha":
        from chanakya.core import state
        from chanakya.skills import complete

        if state.price_gate_open(ctx.session, ctx.pack):
            return await complete.show_cost(ctx)
        from chanakya.skills import recommend

        return recommend.gate_price_reply(ctx)
    demo = ctx.pack.demo(m.proof) if (m and m.proof) else None
    if demo is None:
        return ctx.reply(C.next_question_or_options(ctx))
    from chanakya.skills import recommend

    return ctx.reply(recommend.demo_message(ctx, demo, other_label=None))


async def price_rescue(ctx: SkillContext) -> Reply:
    pack = ctx.pack
    s = ctx.session
    products = ctx.facts.products_sync(pack.category)
    in_stock = [p for p in products if p.qty > 0]
    pool = candidate_set(products, s.home) if s.home.slots or s.home.start_level_inr else in_stock
    if s.home.slots:
        g = ctx.facts.snap.guides
        fitting = [p for p in pool if fit(p, s.home, pack, g).score >= 60]
        pool = fitting or pool
    pool = [p for p in pool if p.qty > 0] or pool or in_stock
    low = min(p.price for p in pool)
    high = max(p.price for p in pool)
    src = ",".join(sorted(p.price_source for p in pool))
    lo_s = ctx.reg.inr(low, "calc:range_low").lstrip("₹")
    hi_s = ctx.reg.inr(high, "calc:range_high").lstrip("₹")
    ctx.reg.sources.append(src)
    line = ctx.pick(ctx.packs.common_line("price_rescue"), ctx.clang)
    say = line.format(low=lo_s, high=hi_s)
    ctx.ev("price_range_given", sources=[p.price_source for p in pool], low=low, high=high)
    mark_first_price(ctx, "customer")
    slot = C.pick_next_slot(ctx)
    if slot:
        return ctx.reply(C.question_reply(ctx, slot, say_override=say, why_override=ctx.t("kyun_price_rescue")))
    cites = C.known_cites(ctx)
    return ctx.reply(coach_reply(ctx, say, ctx.t("kyun_price_rescue"), cites, [ctx.b("b_options", ctx.sid("options"))]))


async def unknown_claim(ctx: SkillContext, question: str | None) -> Reply:
    from chanakya.skills import expert

    return await expert.ask(ctx, question or ctx.inbound.text or "", via_truth_check=True)


def looks_bait(ctx: SkillContext, text: str) -> bool:
    return bool(text) and contains_any(text, ctx.packs.common.get("looks_bait", []))


async def no_looks(ctx: SkillContext) -> Reply:
    ctx.ev("guard_looks")
    if ctx.session and ctx.pack:
        return ctx.reply(C.budget_list(ctx, body=ctx.t("no_looks")))
    return ctx.reply(info_reply(ctx, ctx.t("no_looks")))


def banned_in(ctx: SkillContext, text: str) -> str | None:
    low = (text or "").lower()
    return next((b for b in ctx.packs.banned if b in low), None)


async def honest_version(ctx: SkillContext, phrase: str) -> Reply:
    """The associate typed a pressure line. Offer the honest version from data; never repeat the phrase."""
    from chanakya.core.clock import today

    s = ctx.session
    cat = s.home.category if s else (ctx.staff.dept if ctx.staff else "tv")
    stock_words = ("piece", "पीस", "stock")
    if any(w in phrase for w in stock_words):
        sku = (s.chosen_sku or s.option_labels.get("B") or ctx.x.get("label_sku")) if s else None
        p = ctx.facts.product_sync(sku) if sku else None
        if p:
            honest = ctx.t(
                "honest_stock",
                ctx.clang,
                qty=ctx.reg.num(p.qty, f"stock:{p.sku}@{p.store_id}"),
                product=C.product_name(ctx, p),
            )
        else:
            honest = ctx.t("honest_generic", ctx.clang)
    else:
        offers = await ctx.facts.offers(cat or "tv", today())
        if offers:
            from chanakya.truth import fmt

            o = min(offers, key=lambda o: o.valid_to)
            honest = ctx.t(
                "honest_offer",
                ctx.clang,
                date=ctx.reg.raw(fmt.date_short(o.valid_to, ctx.clang), f"offer:{o.id}:valid_to"),
            )
        else:
            honest = ctx.t("honest_generic", ctx.clang)
    ctx.ev("guard_banned_phrase")
    btns = [ctx.b("b_go", ctx.sid("go") if s else encode("g", "status")), ctx.b("b_status", encode("g", "status"))]
    return ctx.reply(info_reply(ctx, ctx.t("honest_body", honest=honest), btns))
