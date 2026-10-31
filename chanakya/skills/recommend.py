"""Step 5 — Dikhao (Recommend & demonstrate): 3 options (no prices), one demo, "Dikha diya ✓"."""

from __future__ import annotations

from chanakya.brain import packs as P
from chanakya.brain.demos import pick_demo
from chanakya.brain.options import OptionSet, compute_options
from chanakya.core import state
from chanakya.core.buttons import encode
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Button, Card, OutMessage, Reply, Step
from chanakya.skills import common as C
from chanakya.skills import product_view as PV
from chanakya.skills.base import SkillContext


def gate_options_reply(ctx: SkillContext) -> Reply:
    n = state.missing_for_options(ctx.session, ctx.pack)
    body = ctx.t("gate_options", n=ctx.reg.num(n, "calc:gate_missing"))
    btns = [
        ctx.b("b_next_q", ctx.sid("go")),
        ctx.b("b_override_hurry", ctx.sid("override", "options", "hurry")),
        ctx.b("b_status", encode("g", "status")),
    ]
    return ctx.reply(info_reply(ctx, body, btns))


def gate_price_reply(ctx: SkillContext) -> Reply:
    s = ctx.session
    best = ctx.facts.product_sync(s.option_labels["B"]) if s.option_labels.get("B") else None
    if not state.options_gate_open(s, ctx.pack):
        n = state.missing_for_options(s, ctx.pack)
        body = ctx.t("gate_price_assess", n=ctx.reg.num(n, "calc:gate_missing"))
        btns = [
            ctx.b("b_next_q", ctx.sid("go")),
            ctx.b("b_override_asked", ctx.sid("override", "price", "asked")),
            ctx.b("b_status", encode("g", "status")),
        ]
        return ctx.reply(info_reply(ctx, body, btns))
    body = ctx.t("gate_price", best=C.product_name(ctx, best)) if best else ctx.t("gate_price_no_options")
    first = ctx.b("b_shown", ctx.sid("shown")) if s.shortlist else ctx.b("b_options", ctx.sid("options"))
    btns = [
        first,
        ctx.b("b_override_asked", ctx.sid("override", "price", "asked")),
        ctx.b("b_status", encode("g", "status")),
    ]
    return ctx.reply(info_reply(ctx, body, btns))


def _option_set(ctx: SkillContext) -> OptionSet | None:
    pack = ctx.pack
    products = ctx.facts.products_sync(pack.category)
    return compute_options(products, ctx.session.home, pack, ctx.facts.snap.guides)


def _reason(ctx: SkillContext, key: str, lang: str) -> str:
    pack = ctx.pack
    tpl = ctx.pick(pack.reasons.get(key) or pack.reasons.get("value_fit") or {}, lang)
    home = ctx.session.home
    vals = {}
    for ph in P.placeholders(tpl):
        slot = {
            "distance": "seat_distance",
            "sqft": "room_size",
            "people": "family_size",
            "kg": "family_size",
            "ton": "room_size",
        }.get(ph)
        n = P.number(home, slot, pack) if slot else None
        vals[ph] = ctx.reg.num(n, f"card:{slot}") if n is not None else ""
    from chanakya.i18n.lang import fill

    return fill(tpl, **vals)


def _stock_line(ctx: SkillContext, p) -> str:
    if p.qty > 0:
        return ctx.t("stock_in_store", qty=ctx.reg.num(p.qty, f"stock:{p.sku}@{p.store_id}"))
    store = ctx.facts.store_name(p.nearby_store_id).replace("Reliance Digital ", "").replace(" (TEST)", "")
    return ctx.t(
        "stock_nearby",
        days=ctx.reg.num(p.delivery_days, f"stock:{p.sku}@{p.store_id}:delivery_days"),
        store=ctx.reg.raw(store, f"store:{p.nearby_store_id}"),
    )


async def show_options(ctx: SkillContext, override_checked: bool = False) -> Reply:
    s = ctx.session
    pack = ctx.pack
    if not state.options_gate_open(s, pack):
        return gate_options_reply(ctx)
    opts = _option_set(ctx)
    if opts is None:
        return ctx.reply(info_reply(ctx, ctx.t("no_options"), [ctx.b("b_expert", ctx.sid("expert"))]))
    s.option_labels = {o.label: o.product.sku for o in opts.options}
    best, value, step = opts.by_role("best"), opts.by_role("value"), opts.by_role("step")
    s.shortlist = [o.product.sku for o in (best, value, step) if o]
    state.advance(s, Step.RECOMMEND)
    ctx.ev(
        "options_shown",
        best=best.product.sku,
        value=value.product.sku if value else None,
        stepup=step.product.sku if step else None,
    )
    lines, card_opts, btns = [], [], []
    for o in opts.options:
        p = o.product
        name = C.product_name(ctx, p)
        reason_a = _reason(ctx, o.reason_key, ctx.lang)
        extra = "" if p.qty > 0 else f" ({_stock_line(ctx, p)})"
        lines.append(f"{o.label}: {name} — {reason_a}{' ⭐' if o.role == 'best' else ''}{extra}")
        card_opts.append(
            {
                "label": o.label,
                "title": C.product_title(ctx, p, ctx.clang),
                "reason": _reason(ctx, o.reason_key, ctx.clang),
                "fit_str": ctx.reg.num(o.fit.score, f"calc:fit:{p.sku}") + "%",
                "stock_line": _stock_line(ctx, p),
                "image": p.image,
                "is_best": o.role == "best",
                "features": PV.features(ctx, p, ctx.clang, 3),
                "dims_text": PV.dims_text(ctx, p),
            }
        )
        btns.append(C.option_button(ctx, o.label, p))
    card = Card(template="options", lang=ctx.lang, data={"options": card_opts})  # type: ignore[arg-type]
    m1 = info_reply(ctx, "\n".join(lines), btns, card=card)
    demo = pick_demo(opts, s.home, pack, ctx.facts.snap.guides)
    msgs: list[OutMessage] = [m1]
    if demo:
        d, other, _ = demo
        s.demo_recipe = d.id
        ctx.x["demos_sent"] = [d.id]
        msgs.append(demo_message(ctx, d, other, reason_key=best.reason_key))
        ctx.ev("demo_recipe_sent", recipe=d.id)
    return ctx.reply(*msgs)


def demo_message(ctx: SkillContext, d, other_label: str | None, reason_key: str | None = None) -> OutMessage:
    s = ctx.session
    pack = ctx.pack
    best_p = ctx.facts.product_sync(s.option_labels["B"]) if s.option_labels.get("B") else None
    other_p = (
        ctx.facts.product_sync(s.option_labels[other_label])
        if other_label and s.option_labels.get(other_label)
        else None
    )
    best = C.product_name(ctx, best_p) if best_p else ctx.t("this_product", ctx.clang)
    other = C.product_name(ctx, other_p) if other_p else ctx.t("other_product")
    dist = P.number(s.home, "seat_distance", pack)
    steps = ctx.reg.num(max(1, round(dist / 2.5)), "calc:steps_from_distance") if dist else ""
    from chanakya.i18n.lang import fill

    say = fill(ctx.pick(d.say, ctx.clang), best=best)
    why = fill(ctx.pick(d.steps), best=best, other=other, steps=steps)
    if reason_key:
        why += " — " + ctx.t("because") + " " + _reason(ctx, reason_key, ctx.lang)
    cites = [k for k in d.when_slots if P.known(s.home, k)] or C.known_cites(ctx)
    btns = [ctx.b("b_shown", ctx.sid("shown")), ctx.b("b_demo2", ctx.sid("demo2")), ctx.b("b_tryon", ctx.sid("tryon"))]
    return coach_reply(ctx, say, why, cites, btns)


async def shown(ctx: SkillContext) -> Reply:
    s = ctx.session
    if not s.shortlist:
        return await show_options(ctx)
    s.demo_done = True
    state.advance(s, Step.DEMONSTRATED)
    ctx.ev("demo_shown", recipe=s.demo_recipe)
    best_p = ctx.facts.product_sync(s.option_labels["B"])
    from chanakya.i18n.lang import fill

    say = fill(ctx.pick(ctx.packs.common_line("trial_close"), ctx.clang), best=C.product_name(ctx, best_p))
    demo = ctx.pack.demo(s.demo_recipe) if s.demo_recipe else None
    cites = ([k for k in demo.when_slots if P.known(s.home, k)] if demo else []) or C.known_cites(ctx)
    btns = [C.option_button(ctx, lab, ctx.facts.product_sync(sku)) for lab, sku in sorted(s.option_labels.items())]
    return ctx.reply(coach_reply(ctx, say, ctx.t("kyun_trial_close"), cites, btns))


async def demo2(ctx: SkillContext) -> Reply:
    s = ctx.session
    opts = _option_set(ctx)
    if opts is None or not s.shortlist:
        return await show_options(ctx)
    used = set(ctx.x.get("demos_sent", []))
    nxt = pick_demo(opts, s.home, ctx.pack, ctx.facts.snap.guides, skip=used)
    if nxt is None:
        ctx.x["demos_sent"] = []
        nxt = pick_demo(opts, s.home, ctx.pack, ctx.facts.snap.guides)
    d, other, _ = nxt
    s.demo_recipe = d.id
    ctx.x.setdefault("demos_sent", []).append(d.id)
    ctx.ev("demo_recipe_sent", recipe=d.id)
    return ctx.reply(demo_message(ctx, d, other))


async def override(ctx: SkillContext, gate: str, reason: str) -> Reply:
    s = ctx.session
    state.add_override(s, gate, reason, ctx.now)
    ctx.ev("override", gate=gate, reason=reason)
    note = info_reply(ctx, ctx.t("override_logged"))
    if gate == "options":
        r = await show_options(ctx)
        r.messages.insert(0, note)
        return r
    if ctx.x.get("pending_after_override") == "offers":
        from chanakya.skills import close

        r = await close.offers(ctx)
    else:
        from chanakya.skills import complete

        if not s.chosen_sku and not s.option_labels:
            r = await show_options(ctx)
        else:
            r = await complete.show_cost(ctx)
    r.messages.insert(0, note)
    return r


def tryon_buttons(ctx: SkillContext, skus: list[str]) -> list[Button]:
    out = []
    for sku in skus[:2]:
        p = ctx.facts.product_sync(sku)
        size = ctx.reg.num(p.spec("size_in") or 0, f"spec:{sku}:size_in")
        out.append(Button(id=ctx.sid("tpick", sku), title=ctx.t("tryon_like", size=size)[:20]))
    out.append(ctx.b("b_other_wall", ctx.sid("photo")))
    return out
