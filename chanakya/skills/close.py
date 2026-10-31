"""Step 7 — Daam Aakhir Mein (Close): offers from data, finance in one sentence, objections, delivery."""

from __future__ import annotations

from datetime import timedelta

from chanakya.ai.rules import contains_any
from chanakya.core import repo, state
from chanakya.core.buttons import encode
from chanakya.core.clock import today
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Button, OutMessage, Reply, Step
from chanakya.i18n.lang import fill
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext
from chanakya.skills.recommend import gate_price_reply
from chanakya.truth import finance, fmt
from chanakya.truth import offers as offers_engine

BANKS = ("HDFC", "SBI", "other")


def _sku(ctx: SkillContext) -> str | None:
    s = ctx.session
    return s.chosen_sku or s.option_labels.get("B")


async def offers(ctx: SkillContext) -> Reply:
    s = ctx.session
    if not state.price_gate_open(s, ctx.pack):
        ctx.x["pending_after_override"] = "offers"
        return gate_price_reply(ctx)
    if not _sku(ctx):
        from chanakya.skills.recommend import show_options

        return await show_options(ctx)
    if not ctx.x.get("bank"):
        btns = [
            Button(id=ctx.sid("bank", "HDFC"), title=ctx.t("b_bank_hdfc")),
            Button(id=ctx.sid("bank", "SBI"), title=ctx.t("b_bank_sbi")),
            Button(id=ctx.sid("bank", "other"), title=ctx.t("b_bank_other")),
        ]
        return ctx.reply(info_reply(ctx, ctx.t("ask_bank"), btns))
    return await quote(ctx)


async def set_bank(ctx: SkillContext, bank: str) -> Reply:
    ctx.x["bank"] = bank
    return await offers(ctx)


async def quote(ctx: SkillContext, alt: bool = False) -> Reply:
    s = ctx.session
    sku = _sku(ctx)
    bank = ctx.x.get("bank")
    bank = None if bank in ("other", "none") else bank
    res = await offers_engine.best_stack(sku, s.home, ctx.facts, ctx.reg, today(), bank=bank, emi=True, lang=ctx.lang)
    best, alt_q = await finance.best_plan(
        ctx.facts.product_sync(sku).category, res.payable, bank, ctx.facts, ctx.reg, lang=ctx.clang
    )
    q = alt_q if (alt and alt_q) else best
    s.offer_snapshot = {
        "sku": sku,
        "payable": res.payable,
        "payable_str": res.payable_str,
        "applied": [a.model_dump(mode="json") for a in res.applied],
        "finance": q.model_dump() if q else None,
    }
    state.advance(s, Step.CLOSE)
    ctx.ev(
        "offers_shown",
        sources=[f"offer:{a.id}" for a in res.applied] + [f"price:{sku}"],
        offer_ids=[a.id for a in res.applied],
        payable=res.payable,
    )
    if q:
        ctx.ev(
            "finance_shown",
            sources=[f"calc:emi:{q.plan_id}"],
            plan_id=q.plan_id,
            monthly=q.monthly,
            months=q.months,
            total=q.total,
            extra=q.extra,
        )
        say = finance_sentence(ctx, q, ctx.clang)
    else:
        say = ctx.tc("payable_now", payable=res.payable_str)
    if res.applied:
        a = res.applied[0]
        bank_word = a.title.split(" ")[0] if a.stack_group == "bank" else a.title
        why = ctx.t(
            "kyun_offers",
            offer=ctx.reg.raw(bank_word, f"offer:{a.id}"),
            amount=a.amount_str,
            date=ctx.reg.raw(fmt.date_short(a.valid_to, ctx.lang), f"offer:{a.id}:valid_to"),
            payable=res.payable_str,
        )
    else:
        why = ctx.t("kyun_no_offer", payable=res.payable_str)
    cites = C.known_cites(ctx, ["mounting", "exchange_old", "outdoor_spot", "family_size", "carry"])
    btns = [  # spec 12.7: [Customer ko bhejo] [Delivery din] [Parivaar Card]
        ctx.b("b_send_customer", ctx.sid("sendoffer")),
        ctx.b("b_delivery", ctx.sid("delivery")),
        ctx.b("b_parivaar", ctx.sid("parivaar")),
    ]
    return ctx.reply(coach_reply(ctx, say, why, cites, btns))


async def send_to_customer(ctx: SkillContext) -> Reply:
    """[Customer ko bhejo]: the offer + finance summary to the customer (consent first, as for the Parivaar Card)."""
    s = ctx.session
    if not s.consent or not s.customer_wa_id:
        from chanakya.skills import follow

        return await follow.parivaar(ctx)
    os_ = s.offer_snapshot or {}
    sku = os_.get("sku") or _sku(ctx)
    p = ctx.facts.product_sync(sku)
    lang = s.home.customer_lang
    lines = [C.product_title(ctx, p, lang)]
    if s.cost_snapshot:
        lines[0] += " · " + ctx.t(
            "parivaar_cost", lang, total=ctx.reg.raw(s.cost_snapshot["zaroori_total_str"], "snapshot")
        )
    if os_.get("applied"):
        a = os_["applied"][0]
        from datetime import date

        o = next((o for o in ctx.facts.snap.offers if o.id == a["id"]), None)
        lines.append(
            ctx.t(
                "parivaar_offer",
                lang,
                offer=ctx.pick(o.title, lang) if o else a["id"],
                amount=ctx.reg.raw(a["amount_str"], f"offer:{a['id']}"),
                date=ctx.reg.raw(fmt.date_short(date.fromisoformat(a["valid_to"]), lang), f"offer:{a['id']}"),
                payable=ctx.reg.raw(os_.get("payable_str", ""), "snapshot:payable"),
            )
        )
    if os_.get("finance"):
        q = finance.FinanceQuote.model_validate(os_["finance"])
        for v in (q.monthly_str, q.fee_str, q.total_str, q.extra_str, q.months_str):
            ctx.reg.raw(v, f"snapshot:finance:{q.plan_id}")
        lines.append(finance_sentence(ctx, q, lang))
    ctx.ev("offer_sent_to_customer", sku=sku)
    to_cust = OutMessage(to=s.customer_wa_id, kind="text", body="\n".join(lines[:3]), allowed_numbers=ctx.reg.allowed())
    btns = [
        ctx.b("b_delivery", ctx.sid("delivery")),
        ctx.b("b_parivaar", ctx.sid("parivaar")),
        ctx.b("b_khatam", ctx.sid("khatam")),
    ]
    return ctx.reply(to_cust, info_reply(ctx, ctx.t("relay_sent"), btns))


def finance_sentence(ctx: SkillContext, q: finance.FinanceQuote, lang: str) -> str:
    tpl = ctx.pick(ctx.packs.common_line("finance_line"), lang)
    return fill(
        tpl,
        monthly=q.monthly_str.lstrip("₹"),
        months=q.months_str,
        fee=q.fee_str.lstrip("₹"),
        total=q.total_str.lstrip("₹"),
        extra=q.extra_str.lstrip("₹"),
    )


async def delivery(ctx: SkillContext) -> Reply:
    p = ctx.facts.product_sync(_sku(ctx))
    d1 = today() + timedelta(days=max(1, p.delivery_days if p.qty == 0 else 1))
    d2 = d1 + timedelta(days=1)
    src = f"stock:{p.sku}@{p.store_id}:delivery_days"

    def label(d, lang, roman=False):
        return ctx.reg.raw(f"{fmt.day_name(d, lang, roman=roman)} {fmt.date_short(d, lang)}", src)

    say = fill(
        ctx.pick(ctx.packs.common_line("close_question"), ctx.clang),
        day1=label(d1, ctx.clang),
        day2=label(d2, ctx.clang),
    )
    btns = [
        Button(id=ctx.sid("dday", d1.isoformat()), title=label(d1, ctx.lang, roman=True)[:20]),
        Button(id=ctx.sid("dday", d2.isoformat()), title=label(d2, ctx.lang, roman=True)[:20]),
        ctx.b("b_parivaar", ctx.sid("parivaar")),
    ]
    cites = C.known_cites(ctx, ["decider", "floor_lift"])
    return ctx.reply(coach_reply(ctx, say, ctx.t("kyun_delivery"), cites, btns))


async def set_delivery(ctx: SkillContext, iso: str) -> Reply:
    from datetime import date

    d = date.fromisoformat(iso)
    ctx.x["delivery_choice"] = iso
    txt = ctx.reg.raw(f"{fmt.day_name(d, ctx.lang, roman=True)} {fmt.date_short(d, ctx.lang)}", "choice:delivery")
    btns = [
        ctx.b("b_parivaar", ctx.sid("parivaar")),
        ctx.b("b_task", ctx.sid("task")),
        ctx.b("b_khatam", ctx.sid("khatam")),
    ]
    return ctx.reply(info_reply(ctx, ctx.t("delivery_set", day=txt), btns))


OBJECTION_BUTTONS = {
    "value_option": ("b_value_option", "opt", "A"),
    "emi": ("b_emi", "emi", None),
    "exchange": ("b_exchange", "slotask", "exchange_old"),
    "cost_show": ("b_cost_show", "cost_show", None),
    "offers": ("b_offers", "offers", None),
    "go": ("b_go", "go", None),
    "parivaar": ("b_parivaar", "parivaar", None),
    "task": ("b_task", "task", None),
    "expert": ("b_expert", "expert", None),
}


async def objection(ctx: SkillContext, oid: str) -> Reply:
    ob = ctx.packs.objection(oid) or {}
    s = ctx.session
    if ob.get("use_myth"):
        m = ctx.packs.myth(ob["use_myth"], s.home.category)
        line = ctx.pick(m.line, ctx.clang) if m else ""
    else:
        line = ctx.pick(ob.get("line"), ctx.clang)
    if "{valid_to}" in line:
        offs = await ctx.facts.offers(s.home.category or "tv", today())
        if offs:
            o = min(offs, key=lambda o: o.valid_to)
            line = line.replace("{valid_to}", ctx.reg.raw(fmt.date_short(o.valid_to, ctx.clang), f"offer:{o.id}"))
        else:
            line = line.replace("{valid_to}", "")
    btns = []
    for key in ob.get("buttons", [])[:3]:
        spec = OBJECTION_BUTTONS.get(key)
        if not spec:
            continue
        tkey, action, arg = spec
        btns.append(ctx.b(tkey, ctx.sid(action, arg) if arg else ctx.sid(action)))
    cites = C.known_cites(ctx)
    ctx.ev("objection_handled", objection=oid)
    if not cites:
        slot = C.pick_next_slot(ctx)
        if slot:
            ctx.asking.add(slot)
            cites = [slot]
    if not cites:
        return ctx.reply(info_reply(ctx, f'{ctx.t("label_bolo")}: "{line}"', btns))
    return ctx.reply(coach_reply(ctx, line, ctx.t("kyun_objection"), cites, btns))


def is_discount_ask(ctx: SkillContext, text: str) -> bool:
    if not text:
        return False
    words = (ctx.packs.common.get("intent_keywords") or {}).get("discount_ask", [])
    return contains_any(text, words) or bool(__import__("re").search(r"\d+\s*%\s*(off|discount|छूट|सूट)", text.lower()))


async def no_invented_discount(ctx: SkillContext) -> Reply:
    ctx.ev("guard_discount")
    sid = ctx.sid if ctx.session else None
    btns = (
        [ctx.b("b_offers", sid("offers")), ctx.b("b_ask_manager", sid("askmgr")), ctx.b("b_go", sid("go"))]
        if sid
        else [ctx.b("b_status", encode("g", "status"))]
    )
    return ctx.reply(info_reply(ctx, ctx.t("no_invented_discount"), btns))


async def ask_manager(ctx: SkillContext) -> Reply:
    s = ctx.session
    managers = await repo.staff_by_role("manager")
    owner = managers[0].wa_id if managers else ctx.actor_wa_id
    task = await repo.task_create(
        owner,
        "approval",
        {"hi": "Discount approval", "mr": "सूट मंजुरी", "en": "Discount approval"},
        ctx.now,
        s.id,
        {"associate": ctx.actor_wa_id, "code": s.code, "sku": _sku(ctx)},
    )
    ctx.ev("task_created", task_id=task.id, kind="approval", owner=owner, due_at=ctx.now.isoformat())
    msgs = [info_reply(ctx, ctx.t("approval_sent"))]
    if managers:
        mlang = managers[0].lang or "hi"
        body = ctx.t(
            "approval_for_manager",
            mlang,
            name=ctx.reg.raw(ctx.staff.name, "staff:name"),
            code=ctx.reg.raw(s.code, "session:code"),
        )
        msgs.append(
            info_reply(
                ctx,
                body,
                [
                    Button(id=encode("m", "appr", task.id, "offers"), title=ctx.t("b_offer_dikhao", mlang)),
                    Button(id=encode("m", "appr", task.id, "no"), title=ctx.t("b_mana_karo", mlang)),
                ],
                to=owner,
            )
        )
        ctx.reg.raw(str(task.id), "task:id")
    return ctx.reply(*msgs)
