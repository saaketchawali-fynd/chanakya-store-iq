"""Step 7 — Daam Aakhir Mein (Close): offers from data, finance in one sentence, objections, delivery."""

from __future__ import annotations

from datetime import timedelta

from chanakya.ai.rules import contains_any
from chanakya.brain import packs as P
from chanakya.core import repo, state
from chanakya.core.buttons import encode
from chanakya.core.clock import today
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import CUSTOMER_PHONE_LANG, Button, OutMessage, Reply, Step
from chanakya.i18n.lang import fill
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext
from chanakya.skills.recommend import gate_price_reply
from chanakya.truth import finance, fmt
from chanakya.truth import offers as offers_engine

BANKS = ("HDFC", "SBI", "other")


def _sku(ctx: SkillContext) -> str | None:
    s = ctx.session
    return s.chosen_sku or state.best_sku(s)


async def offers(ctx: SkillContext) -> Reply:
    """Close, in order: (replacing → is the old one working?) → how will they pay? → (card/EMI → which bank?) →
    the offer card with the full cost today. Only offers from the data, only those this payment gets."""
    s = ctx.session
    if s.option_labels and (not s.chosen_sku or (s.cost_snapshot or {}).get("sku") != s.chosen_sku):
        from chanakya.skills.recommend import offers_view  # the offers on the products shown (never skips the cost)

        return await offers_view(ctx)
    if not state.price_gate_open(s, ctx.pack):
        ctx.x["pending_after_override"] = "offers"
        return gate_price_reply(ctx)
    if not _sku(ctx):
        from chanakya.skills.recommend import show_options

        return await show_options(ctx)
    if not s.chosen_sku:  # which one felt right? (the full cost and offers are for one product)
        from chanakya.skills.recommend import shown

        return await shown(ctx)
    if (s.cost_snapshot or {}).get("sku") != s.chosen_sku:  # Complete before Close: the full cost first
        from chanakya.skills import complete

        return await complete.show_cost(ctx)
    pack = ctx.pack
    if ("replace" in P.values(s.home, "trigger") and pack.slot("exchange_old") and not P.known(s.home, "exchange_old")
            and not ctx.x.get("exchange_asked") and any(e.category == pack.category for e in ctx.facts.snap.exchange)):
        ctx.x["exchange_asked"] = True
        ctx.x["pending_close"] = True
        return ctx.reply(C.question_reply(ctx, "exchange_old"))
    pay = ctx.x.get("pay")
    if not pay:
        btns = [ctx.b("b_pay_card", ctx.sid("pay", "card")), ctx.b("b_pay_upi", ctx.sid("pay", "upi"))]
        emi = bool(await ctx.facts.finance(pack.category))  # EMI only when the data has EMI plans
        if emi:
            btns.append(ctx.b("b_pay_emi", ctx.sid("pay", "emi")))
        say = ctx.tc("bolo_pay" if emi else "bolo_pay_no_emi")
        return ctx.reply(coach_reply(ctx, say, ctx.t("kyun_pay"), C.known_cites(ctx), btns))
    if pay in ("card", "emi") and not ctx.x.get("bank"):
        if not _bank_offers(ctx, pay):  # no bank has an offer on this product today: nothing to choose
            ctx.x["bank"] = "other"
        else:
            return ctx.reply(bank_list(ctx, pay))
    return await quote(ctx)


async def set_pay(ctx: SkillContext, how: str) -> Reply:
    ctx.x["pay"] = how if how in ("card", "upi", "emi") else "upi"
    ctx.x.pop("bank", None)
    ctx.ev("payment_mode", mode=ctx.x["pay"])
    return await offers(ctx)


def _bank_offers(ctx: SkillContext, pay: str) -> list[tuple]:
    """(offer, discount) for each bank with an offer on this product today, from the data."""
    p = ctx.facts.product_sync(_sku(ctx))
    out, seen = [], set()
    for o in ctx.facts.snap.offers:
        if not o.bank or o.bank in seen:
            continue
        if not offers_engine.eligible(o, p, ctx.session.home, o.bank, pay == "emi", today()):
            continue
        seen.add(o.bank)
        out.append((o, offers_engine.discount(o, p.price)))
    return out


def bank_list(ctx: SkillContext, pay: str) -> OutMessage:
    """Banks that have an offer on this product today (from the data), each with its offer; then 'other card'."""
    from chanakya.channel.limits import truncate, truncate_title
    from chanakya.core.contracts import ListRow, ListSection

    rows = []
    for o, d in _bank_offers(ctx, pay):
        title = ctx.pick(o.title)
        desc = f"−{ctx.reg.inr(d, f'offer:{o.id}')} · {title}"
        rows.append(ListRow(id=ctx.sid("bank", o.bank), title=truncate(o.bank, 24), description=truncate(desc, 72)))
    rows.append(ListRow(id=ctx.sid("bank", "other"), title=ctx.t("row_other_card"), description=ctx.t("row_bank_none_d")))
    if pay == "emi":
        rows.append(ListRow(id=ctx.sid("bank", "none"), title=ctx.t("row_no_card_emi")))
    ctx.reg.static(" ".join(r.title + " " + (r.description or "") for r in rows))
    return OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("bank_body_emi" if pay == "emi" else "bank_body"),
                      list_button=truncate_title(ctx.t("list_bank")), sections=[ListSection(rows=rows[:10])],
                      allowed_numbers=ctx.reg.allowed())


async def set_bank(ctx: SkillContext, bank: str) -> Reply:
    ctx.x["bank"] = bank
    if not ctx.x.get("pay"):
        ctx.x["pay"] = "card"
    return await offers(ctx)


async def quote(ctx: SkillContext, alt: bool = False) -> Reply:
    s = ctx.session
    sku = _sku(ctx)
    pay = ctx.x.get("pay") or "upi"
    bank = ctx.x.get("bank")
    bank = None if (pay == "upi" or bank in ("other", "none")) else bank
    emi = pay == "emi"
    p = ctx.facts.product_sync(sku)
    res = await offers_engine.best_stack(sku, s.home, ctx.facts, ctx.reg, today(), bank=bank, emi=emi, lang=ctx.clang)
    q = None
    if emi:
        best, alt_q = await finance.best_plan(p.category, res.payable, bank, ctx.facts, ctx.reg, lang=ctx.clang)
        q = alt_q if (alt and alt_q) else best
    extras = 0
    if s.cost_snapshot and s.cost_snapshot.get("sku") == sku:
        extras = int(s.cost_snapshot["zaroori_total"]) - int(p.price)
    total_today = res.payable + max(0, extras)
    total_str = ctx.reg.inr(total_today, f"calc:total_today:{sku}")
    s.offer_snapshot = {
        "sku": sku,
        "payable": res.payable,
        "payable_str": res.payable_str,
        "total_today": total_today,
        "total_today_str": total_str,
        "pay": pay,
        "bank": bank,
        "applied": [a.model_dump(mode="json") for a in res.applied],
        "finance": q.model_dump() if q else None,
    }
    state.advance(s, Step.CLOSE)
    ctx.ev(
        "offers_shown",
        sources=[f"offer:{a.id}" for a in res.applied] + [f"price:{sku}"],
        offer_ids=[a.id for a in res.applied],
        payable=res.payable,
        pay=pay,
        bank=bank,
    )
    if q:
        ctx.ev("finance_shown", sources=[f"calc:emi:{q.plan_id}"], plan_id=q.plan_id, monthly=q.monthly,
               months=q.months, total=q.total, extra=q.extra)
        say = finance_sentence(ctx, q, ctx.clang)
    elif extras > 0:
        say = ctx.tc("bolo_total_today", total=total_str)
    else:
        say = ctx.tc("payable_now", payable=res.payable_str)
    if res.applied:  # the Kyun line is the associate's: offer name and date in their language
        a = res.applied[0]
        o = next((o for o in ctx.facts.snap.offers if o.id == a.id), None)
        why = ctx.t("kyun_offers", offer=ctx.pick(o.title) if o else ctx.reg.static(a.title), amount=a.amount_str,
                    date=ctx.reg.raw(fmt.date_short(a.valid_to, ctx.lang), f"offer:{a.id}:valid_to"),
                    payable=res.payable_str)
    else:
        why = ctx.t("kyun_no_offer", payable=res.payable_str)
    async def build():  # offer names, dates and the EMI sentence in the card's language (same numbers)
        r = await offers_engine.best_stack(sku, s.home, ctx.facts, ctx.reg, today(), bank=bank, emi=emi, lang=ctx.clang)
        fq = None
        if emi:
            b1, b2 = await finance.best_plan(p.category, r.payable, bank, ctx.facts, ctx.reg, lang=ctx.clang)
            fq = b2 if (alt and b2) else b1
        return offer_card(ctx, p, r, fq, extras, total_str, pay, bank)

    card = await C.english_async(ctx, build)
    cites = C.known_cites(ctx, ["exchange_old", "mounting", "outdoor_spot", "family_size", "power_cuts", "carry"])
    btns = [
        ctx.b("b_book_delivery", ctx.sid("delivery")),
        ctx.b("b_not_today", ctx.sid("nottoday")),
        ctx.b("b_send_customer", ctx.sid("sendoffer")),
    ]
    return ctx.reply(coach_reply(ctx, say, why, cites, btns, card=card))


def _also(ctx: SkillContext, p, applied_ids: set[str], pay: str) -> list[dict]:
    """Other offers in the data this customer could still use with another card (never invented)."""
    out = []
    s = ctx.session
    for o in ctx.facts.snap.offers:
        if o.id in applied_ids or not o.bank:
            continue
        if not offers_engine.eligible(o, p, s.home, o.bank, pay == "emi", today()):
            continue
        d = offers_engine.discount(o, p.price)
        out.append({"title": ctx.pick(o.title, ctx.clang), "amount_str": "−" + ctx.reg.inr(d, f"offer:{o.id}"),
                    "till": ctx.t("brief_offer_till", ctx.clang, date=ctx.reg.raw(fmt.date_short(o.valid_to, ctx.clang), f"offer:{o.id}:valid_to"))})
        ctx.reg.static(out[-1]["title"])
    return out[:2]


def offer_card(ctx: SkillContext, p, res, q, extras: int, total_str: str, pay: str, bank: str | None):
    from chanakya.core.contracts import Card

    lang = ctx.clang
    s = ctx.session
    offers_rows = [{"title": a.title, "amount_str": "−" + a.amount_str,
                    "till": ctx.t("brief_offer_till", lang, date=a.valid_to_str)} for a in res.applied]
    for a in res.applied:
        ctx.reg.static(a.title)
    mrp_str = ctx.reg.inr(p.mrp, p.price_source) if getattr(p, "mrp", None) and p.mrp > p.price else None
    pay_text = (ctx.t("offer_caption_pay_card", lang, bank=bank) if pay == "card" and bank
                else ctx.t("offer_caption_pay_emi", lang) if pay == "emi" else ctx.t("offer_caption_pay_upi", lang))
    exch = None
    if s.cost_snapshot and s.cost_snapshot.get("exchange_range"):
        lo, hi = s.cost_snapshot["exchange_range"]
        ctx.reg.raw(f"{lo} {hi}", "snapshot:exchange")
        exch = ctx.t("exchange_text", lang, low=lo, high=hi)
    extras_str = ctx.reg.inr(extras, f"calc:extras:{p.sku}") if extras > 0 else None
    saving = res.discount_total + (p.mrp - p.price if mrp_str else 0)
    return Card(
        template="offer",
        lang=lang,
        data={  # type: ignore[arg-type]
            "customer_name": s.home.customer_name or "",
            "product_title": C.product_title(ctx, p, lang),
            "product_image": p.image,
            "pay_text": ctx.reg.static(pay_text),
            "mrp_str": mrp_str,
            "price_str": res.price_str,
            "offers": offers_rows,
            "payable_str": res.payable_str,
            "extras_str": extras_str,
            "total_str": total_str,
            "saving_str": ctx.reg.inr(saving, f"calc:saving:{p.sku}") if saving > 0 else None,
            "finance_text": finance_sentence(ctx, q, lang) if q else None,
            "exchange_text": exch,
            "also": _also(ctx, p, {a.id for a in res.applied}, pay),
            "stamp": ctx.t("kharcha_stamp", lang, date=ctx.reg.raw(fmt.date_short(ctx.now, lang), "clock:date"),
                           time=ctx.reg.raw(fmt.time_short(ctx.now), "clock:time")),
        },
    )


async def send_to_customer(ctx: SkillContext) -> Reply:
    """[Customer ko bhejo]: the offer + finance summary to the customer (consent first, as for the Parivaar Card)."""
    s = ctx.session
    if not s.consent or not s.customer_wa_id:
        from chanakya.skills import follow

        return await follow.parivaar(ctx)
    os_ = s.offer_snapshot or {}
    sku = os_.get("sku") or _sku(ctx)
    p = ctx.facts.product_sync(sku)
    lang = CUSTOMER_PHONE_LANG
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

    if p.stock_known:
        say = fill(ctx.pick(ctx.packs.common_line("close_question"), ctx.clang),
                   day1=label(d1, ctx.clang), day2=label(d2, ctx.clang))
    else:  # no delivery data from the backend: the customer's preferred day, for the store to confirm
        src = "calc:delivery_preference"
        say = ctx.tc("bolo_delivery_pref", day1=label(d1, ctx.clang), day2=label(d2, ctx.clang))
    btns = [
        Button(id=ctx.sid("dday", d1.isoformat()), title=label(d1, ctx.lang, roman=True)[:20]),
        Button(id=ctx.sid("dday", d2.isoformat()), title=label(d2, ctx.lang, roman=True)[:20]),
        ctx.b("b_not_today", ctx.sid("nottoday")),
    ]
    cites = C.known_cites(ctx, ["decider", "floor_lift"])
    return ctx.reply(coach_reply(ctx, say, ctx.t("kyun_close_choice"), cites, btns))


async def set_delivery(ctx: SkillContext, iso: str) -> Reply:
    """A day picked = sold. Rishta starts at once: consent (QR) → the summary card on their WhatsApp."""
    from datetime import date

    from chanakya.skills import follow

    if not ctx.x.get("pay"):  # an old delivery button before the offers: the offers first
        return await offers(ctx)
    d = date.fromisoformat(iso)
    ctx.x["delivery_choice"] = iso
    ctx.x["outcome"] = "sold"
    ctx.ev("delivery_booked", day=iso)
    txt = ctx.reg.raw(f"{fmt.day_name(d, ctx.lang, roman=True)} {fmt.date_short(d, ctx.lang)}", "choice:delivery")
    return await follow.parivaar(ctx, note=ctx.t("delivery_booked", day=txt))


async def not_today(ctx: SkillContext) -> Reply:
    """'Not today': ask what is holding them back, as one list of the real reasons."""
    from chanakya.channel.limits import truncate_title
    from chanakya.core.contracts import ListRow, ListSection

    ids = ["too_expensive", "ask_family", "cheaper_online", "need_to_think", "waiting_for_sale", "brand_doubt",
           "installation_worry"]
    rows = [ListRow(id=ctx.sid("obj", oid), title=ctx.t(f"obj_row_{oid}")) for oid in ids if ctx.packs.objection(oid)]
    ctx.reg.static(" ".join(r.title for r in rows))
    msg = coach_reply(ctx, ctx.tc("bolo_nottoday"), ctx.t("kyun_nottoday"), C.known_cites(ctx), [])
    msg.kind = "list"
    msg.list_button = truncate_title(ctx.t("list_reason"))
    msg.sections = [ListSection(rows=rows)]
    msg.allowed_numbers = ctx.reg.allowed()
    ctx.ev("not_today")
    return ctx.reply(msg)


OBJECTION_BUTTONS = {
    "value_option": ("b_value_option", "opt", "A"),
    "emi": ("b_emi", "emi", None),
    "exchange": ("b_exchange", "slotask", "exchange_old"),
    "cost_show": ("b_cost_show", "cost_show", None),
    "offers": ("b_offers", "offers", None),
    "go": ("b_go", "go", None),
    "parivaar": ("b_parivaar", "parivaar", None),
    "parivaar_family": ("b_send_family", "presale", None),
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
    ctx.x.setdefault("objections", []).append(oid)
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
    if not ctx.session:
        return ctx.reply(info_reply(ctx, ctx.t("no_invented_discount"), btns))
    # something honest to say to the customer, and the reason for the associate
    return ctx.reply(coach_reply(ctx, ctx.tc("bolo_no_discount"), ctx.t("no_invented_discount"), C.known_cites(ctx), btns))


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
    msgs = [info_reply(ctx, ctx.t("approval_sent"), [ctx.b("b_offers", ctx.sid("offers")), ctx.b("b_status", encode("g", "status"))])]
    if managers:
        mlang = managers[0].lang or "en"
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
