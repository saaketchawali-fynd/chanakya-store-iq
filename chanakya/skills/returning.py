"""Returning customers (user, 4 Nov 2026): when a customer comes back or messages, the associate sees what happened
last time, in one line, and carries on from there: no re-asking.

"Shah ji · 3 din pehle · B Meru 55 QLED Pro pasand · ab ₹3,000 sasta" — every fact from the earlier conversation
and today's backend data (price then vs now, availability, new live offers). Also the out-of-stock swap: when the
model they liked is no longer sold, the same-fit model that is in stock, one tap away."""

from __future__ import annotations

from datetime import timedelta

from chanakya.channel.limits import truncate, truncate_title
from chanakya.core import repo
from chanakya.core.coach import info_reply
from chanakya.core.contracts import ListRow, ListSection, OutMessage, Reply, Step
from chanakya.core.status import short_name
from chanakya.skills.base import SkillContext
from chanakya.truth import fmt

WINDOW_DAYS = 30
CARRY = ("wants", "dq_done", "dq_vals", "option_roles", "seen_prices", "seen_offers", "readback_done", "photo_asked",
         "budget_asked", "doubts_asked", "range_bands", "range_id", "chosen_label", "tryon_skus", "interest",
         "from_note", "skip_slots", "skip_key", "dq_avail")


async def recent(ctx: SkillContext, exclude: str | None = None) -> list[tuple]:
    """Earlier customers of this store (30 days), newest first: not sold, with a product and something to go on."""
    store = ctx.staff.store_id if ctx.staff else None
    staff = {s.wa_id: s for s in await repo.staff_all()}
    out = []
    for row, st in await ctx.svc.sessions.since(ctx.now - timedelta(days=WINDOW_DAYS)):
        if st.id == exclude or st.extra.get("continued_in") or st.extra.get("outcome") == "sold":
            continue
        who = staff.get(st.associate_wa_id)
        if store and who and who.store_id != store:
            continue
        if not st.home.category or not (st.option_labels or st.home.customer_name or st.home.slots):
            continue
        out.append((row, st))
    return sorted(out, key=lambda x: repo.aware(x[0].updated_at) or ctx.now, reverse=True)


def liked(st) -> str | None:
    if st.chosen_sku:
        return st.chosen_sku
    roles = st.extra.get("option_roles") or {}
    best = next((lab for lab, r in roles.items() if r == "best"), None)
    return st.option_labels.get(best) if best else (next(iter(st.option_labels.values()), None))


def alternative(ctx: SkillContext, sku: str, home=None, wants: dict | None = None):
    """The same-fit model that is in stock: the best fit for this home among models on sale, of the same key size
    (TV inches, AC ton, washer kg, fridge litres, laptop RAM) when possible, nearest in price."""
    from chanakya.brain.fit import fit
    from chanakya.brain.wants import meets_all
    from chanakya.skills.brief import _bucket

    gone = ctx.facts.product_sync(sku)
    if gone is None:
        return None
    pack = ctx.packs.get(gone.category)
    home = home or (ctx.session.home if ctx.session else None)
    pool = [p for p in ctx.facts.products_sync(gone.category) if p.sku != sku and (p.qty > 0 or p.nearby_qty > 0)]
    if not pool or pack is None or home is None:
        return None
    g = ctx.facts.snap.guides
    fits = {p.sku: fit(p, home, pack, g) for p in pool}
    pool = [p for p in pool if not fits[p.sku].excluded] or pool
    if wants and any(meets_all(p, wants) for p in pool):
        pool = [p for p in pool if meets_all(p, wants)]
    key = _bucket(gone.category, gone)
    same = [p for p in pool if _bucket(p.category, p) == key] if key else []
    pool = same or pool
    return sorted(pool, key=lambda p: (-fits[p.sku].score, abs(p.price - gone.price)))[0]


def summary(ctx: SkillContext, at, st) -> tuple[str, object | None]:
    """One line about the earlier visit (last seen `at`), and the in-stock swap when the liked model is gone."""
    days = max(0, (ctx.now.date() - (repo.aware(at) or ctx.now).astimezone(ctx.now.tzinfo).date()).days)
    when = ctx.t("ret_today") if days == 0 else ctx.t("ret_days_ago", n=ctx.reg.num(days, f"session:{st.id}:age"))
    name = ctx.reg.raw(short_name(st.home.customer_name), "card:name") if st.home.customer_name else ctx.reg.raw(st.code, "session:code")
    parts = [name, when]
    swap = None
    sku = liked(st)
    p = ctx.facts.product_sync(sku) if sku else None
    if p is not None:
        lab = next((k for k, v in st.option_labels.items() if v == sku), "")
        parts.append(ctx.t("ret_liked", label=lab, model=ctx.reg.raw(p.model, f"product:{p.sku}")).strip())
        was = (st.extra.get("seen_prices") or {}).get(sku)
        if not (p.qty > 0 or p.nearby_qty > 0):
            parts.append(ctx.t("ret_out"))
            swap = alternative(ctx, sku, st.home, st.extra.get("wants"))
        elif was and p.price < was:
            parts.append(ctx.t("ret_cheaper", amt=ctx.reg.inr(was - p.price, f"calc:drop:{sku}")))
        elif was and p.price > was:
            parts.append(ctx.t("ret_dearer", amt=ctx.reg.inr(p.price - was, f"calc:rise:{sku}")))
        elif was:
            parts.append(ctx.t("ret_same_price"))
    elif st.home.category:
        parts.append(ctx.pick(ctx.packs.get(st.home.category).label))
    return " · ".join(x for x in parts if x), swap


async def list_view(ctx: SkillContext) -> Reply:
    """'Purana customer': the store's recent customers; one tap carries on from where they left."""
    rows = []
    for row, st in (await recent(ctx, exclude=ctx.session.id if ctx.session else None))[:9]:
        line, _ = summary(ctx, row.updated_at, st)
        title = line.split(" · ")[0] + " · " + fmt.date_short(repo.aware(row.updated_at) or ctx.now, ctx.lang)
        rows.append(ListRow(id=ctx.sid("retpick", st.id), title=truncate(title, 24),
                            description=truncate(" · ".join(line.split(" · ")[1:]), 72)))
    for r in rows:
        ctx.reg.raw(r.title + " " + (r.description or ""), "returning:list")
    if not rows:
        return ctx.reply(info_reply(ctx, ctx.t("ret_none"), [ctx.b("b_new_customer", "g:new")]))
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("ret_list_body"),
                                list_button=truncate_title(ctx.t("ret_list_button")), sections=[ListSection(rows=rows)],
                                allowed_numbers=ctx.reg.allowed()))


async def resume(ctx: SkillContext, old_id: str) -> Reply:
    """Carry on with a returning customer: their home, answers, picks and consent come into this conversation."""
    from chanakya.skills import common as C
    from chanakya.skills import recommend

    old = await ctx.svc.sessions.get(old_id)
    s = ctx.session
    if old is None or s is None:
        return await list_view(ctx)
    row = next((r for r, st in await recent(ctx, exclude=s.id) if st.id == old_id), None)
    line, swap = summary(ctx, row.updated_at, old) if row is not None else ("", None)
    s.home = old.home.model_copy(deep=True)
    for k in CARRY:
        if k in old.extra:
            s.extra[k] = old.extra[k]
    s.option_labels, s.shortlist, s.chosen_sku = dict(old.option_labels), list(old.shortlist), old.chosen_sku
    s.demo_done = old.demo_done
    if old.customer_wa_id and old.consent:
        s.customer_wa_id, s.consent = old.customer_wa_id, old.consent
    old.extra["continued_in"] = s.id
    await ctx.svc.sessions.save(old, ctx.now)
    ctx.ev("customer_returned", from_session=old_id, days=line)
    head = info_reply(ctx, ctx.t("ret_head", line=line))
    if swap is not None:
        head.buttons = [ctx.b("b_swap", ctx.sid("swap", swap.sku), model=swap.short_name())]
        head.kind = "buttons"
        head.body += "\n" + ctx.t("swap_line", model=ctx.reg.raw(swap.model, f"product:{swap.sku}"))
        head.allowed_numbers = ctx.reg.allowed()
    if s.shortlist:
        s.step = Step.RECOMMEND
        nxt = await recommend.show_options(ctx, override_checked=True)
        return ctx.reply(head, *nxt.messages)
    return ctx.reply(head, C.next_question_or_options(ctx))


async def name_match(ctx: SkillContext) -> OutMessage | None:
    """A note named the customer, and the store saw that customer before (another day): offer to carry on."""
    s = ctx.session
    name = (s.home.customer_name or "").strip().lower()
    if not name or s.shortlist or s.extra.get("ret_offered"):
        return None
    first = name.split()[0]
    for row, st in await recent(ctx, exclude=s.id):
        other = (st.home.customer_name or "").strip().lower()
        age = ctx.now - (repo.aware(row.updated_at) or ctx.now)
        if other and other.split()[0] == first and age >= timedelta(hours=2):
            s.extra["ret_offered"] = True
            line, _ = summary(ctx, row.updated_at, st)
            m = info_reply(ctx, ctx.t("ret_match", line=line),
                           [ctx.b("b_ret_continue", ctx.sid("retpick", st.id)), ctx.b("b_ret_fresh", ctx.sid("go"))])
            return m
    return None


async def swap(ctx: SkillContext, sku: str) -> Reply:
    """Choose the in-stock same-fit model instead (then its full cost, as for any pick)."""
    from chanakya.skills import complete

    if ctx.facts.product_sync(sku) is None:
        return ctx.reply(info_reply(ctx, ctx.t("fallback_body")))
    ctx.ev("stock_swap", sku=sku, was=ctx.session.chosen_sku)
    return await complete.choose_sku(ctx, sku)


def out_of_stock_reply(ctx: SkillContext, sku: str) -> OutMessage | None:
    """The model they picked is not sold today: say so, and offer the same-fit one in stock."""
    p = ctx.facts.product_sync(sku)
    if p is None or p.qty > 0 or p.nearby_qty > 0:
        return None
    alt = alternative(ctx, sku)
    if alt is None:
        return None
    ctx.ev("stock_swap_offered", sku=sku, alt=alt.sku)
    body = ctx.t("swap_out", model=ctx.reg.raw(p.model, f"product:{p.sku}")) + "\n" + ctx.t(
        "swap_line", model=ctx.reg.raw(alt.model, f"product:{alt.sku}"))
    return info_reply(ctx, body, [ctx.b("b_swap", ctx.sid("swap", alt.sku), model=alt.short_name()),
                                  ctx.b("b_options", ctx.sid("options"))])
