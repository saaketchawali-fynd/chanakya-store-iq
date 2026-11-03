"""Step 5 — Dikhao (Recommend & demonstrate): 3 options (no prices), one demo, "Dikha diya ✓"."""

from __future__ import annotations

from chanakya.brain import packs as P
from chanakya.brain.demos import pick_demo
from chanakya.brain.fit import fit
from chanakya.brain.options import OptionSet, compute_options
from chanakya.core import state
from chanakya.core.buttons import encode
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Button, Card, OutMessage, Reply, Step
from chanakya.skills import common as C
from chanakya.skills import fitview as FV
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
    best = ctx.facts.product_sync(state.best_sku(s)) if state.best_sku(s) else None
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
    ex_sku = set(ctx.x.get("excluded_skus") or [])
    ex_brand = set(ctx.x.get("excluded_brands") or [])
    if ex_sku or ex_brand:  # 'none of these': drop what they turned down (never below three choices if avoidable)
        left = [p for p in products if p.sku not in ex_sku and p.brand not in ex_brand]
        products = left if len([p for p in left if p.qty > 0 or p.nearby_qty > 0]) >= 1 else products
    return compute_options(products, ctx.session.home, pack, ctx.facts.snap.guides, ctx.session.extra.get("wants"))


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


def _diffs(ctx: SkillContext, p, best, lang: str, n: int = 2) -> list[dict]:
    """What is different from the best pick, from the key specs (size first): facts, not adjectives."""
    if best is None:
        return []
    out = []
    for key in PV.KEY_SPECS.get(p.category, []):
        if key == "warranty":
            continue
        a, b = PV.fmt_value(ctx, p, key, lang), PV.fmt_value(ctx, best, key, lang)
        if a and a != b:
            out.append({"label": ctx.reg.static(PV.label(key, lang)), "value": a})
        if len(out) >= n:
            break
    return out


def _stock_line(ctx: SkillContext, p, lang: str | None = None) -> str:
    """Associate text by default; with `lang`, the customer-facing wording (cards) in that language."""
    return C.stock_line(ctx, p, lang, customer=bool(lang))


def options_card(ctx: SkillContext, opts) -> Card:
    """The customer's pick card, in ctx.clang: their answers, the best pick's benefits for this home (with the
    spec that proves each), what differs in the others, and how each meets their needs."""
    cl = ctx.clang
    best = opts.by_role("best")
    best_p = best.product if best else None
    card_opts = []
    for o in opts.options:
        p = o.product
        card_opts.append(
            {
                "label": o.label,
                "title": C.product_title(ctx, p, cl),
                "role": o.role,
                "reason": _reason(ctx, o.reason_key, cl),
                "points": FV.points(ctx, p, cl, 3) if o.role == "best" else [],
                "diffs": _diffs(ctx, p, best_p, cl) if o.role != "best" else [],
                "stock_line": _stock_line(ctx, p, cl),
                "image": p.image,
                "is_best": o.role == "best",
                "dims_text": PV.dims_text(ctx, p),
                "offer": PV.offer_line(ctx, p, cl),
            }
        )
    labelled = sorted(((o.label, o.product) for o in opts.options), key=lambda x: x[0])
    data = {"options": card_opts, "grid": FV.needs_grid(ctx, labelled, cl), "home": FV.home_chips(ctx, cl)}
    return Card(template="options", lang=cl, data=data)  # type: ignore[arg-type]


WANT_LABEL = {"size_in": '{v}"', "brand": "{v}", "panel": "{v}", "ton": "{v} ton", "capacity_kg": "{v} kg",
              "capacity_l": "{v} L", "star": "{v} star", "load": "{v} load", "ram_gb": "{v} GB RAM"}


def want_labels(ctx: SkillContext, wants: dict) -> list[str]:
    from chanakya.truth import fmt

    out = []
    for k, v in (wants or {}).items():
        val = fmt.num(v) if isinstance(v, (int, float)) else str(v)
        out.append(ctx.reg.raw(WANT_LABEL.get(k, "{v}").format(v=val), f"note:want:{k}"))
    return out


def _note_reasons(ctx: SkillContext, p, note_slots: set[str], wants: dict, n: int = 2) -> list[str]:
    """Why this product, in terms of what the note said: the fit rules its answers switched on that this product
    meets (strongest first), then the explicit asks it meets ('55" as asked')."""
    from chanakya.brain.wants import meets

    fr = fit(p, ctx.session.home, ctx.pack, ctx.facts.snap.guides)
    rules = {r.id: r for r in ctx.pack.fit_rules}
    out = []
    for c in sorted(fr.contributions, key=lambda c: -(c.weight * c.match)):
        r = rules.get(c.rule_id)
        if r and c.match >= 1 and r.slot in note_slots and c.rule_id in ctx.pack.reasons:
            txt = _reason(ctx, c.rule_id, ctx.lang)
            if txt and txt not in out:
                out.append(txt)
        if len(out) >= n:
            break
    met = meets(p, wants)
    if met:
        out.append(ctx.t("note_as_asked", what=", ".join(want_labels(ctx, {k: wants[k] for k in met}))))
    return out[: n + 1]


async def show_options(ctx: SkillContext, override_checked: bool = False, note: dict | None = None) -> Reply:
    """note: the associate described the needs in one voice note ({'slots': [...], 'wants': {...}}): the fixed
    questions are skipped and the reply says the picks come from that note, each with the reasons it matched."""
    s = ctx.session
    pack = ctx.pack
    if note is None and not state.options_gate_open(s, pack):
        return gate_options_reply(ctx)
    if not ctx.facts.snap.products:  # nothing loaded from the backend (yet): say so, not "no model fits"
        return ctx.reply(info_reply(ctx, ctx.t("catalog_unavailable")))
    opts = _option_set(ctx)
    if opts is None:
        return ctx.reply(info_reply(ctx, ctx.t("no_options"), [ctx.b("b_expert", ctx.sid("expert"))]))
    s.option_labels = {o.label: o.product.sku for o in opts.options}
    s.extra["option_roles"] = {o.label: o.role for o in opts.options}
    best, value, step = opts.by_role("best"), opts.by_role("value"), opts.by_role("step")
    s.shortlist = [o.product.sku for o in (best, value, step) if o]
    from chanakya.skills.tryon_flow import add_interest

    add_interest(ctx, *s.shortlist)
    state.advance(s, Step.RECOMMEND)
    ctx.ev(
        "options_shown",
        best=best.product.sku,
        value=value.product.sku if value else None,
        stepup=step.product.sku if step else None,
    )
    lines, btns = [], []
    for o in opts.options:
        p = o.product
        name = C.product_name(ctx, p)
        reason_a = _reason(ctx, o.reason_key, ctx.lang)
        extra = "" if p.qty > 0 else f" ({_stock_line(ctx, p)})"
        tag = f" ({ctx.t('best_pick')})" if o.role == "best" else ""
        lines.append(f"*{o.label}* {name}{tag}: {reason_a}{extra}")
        btns.append(C.option_button(ctx, o.label, p))
    if note is not None:  # from the note: say so, and give the best pick's reasons in the note's own terms
        from chanakya.skills.assess import note_facts

        s.extra["from_note"] = True
        facts = (note_facts(ctx) + want_labels(ctx, note.get("wants") or {}))[:6]
        ack = ctx.t("note_ack", facts=" · ".join(facts), n=ctx.reg.num(len(opts.options), "options:count"),
                    cat=ctx.t(f"brief_cat_{pack.category}"))
        b = best
        why = _note_reasons(ctx, b.product, set(note.get("slots") or []), note.get("wants") or {})
        line_b = f"*{b.label}* {C.product_name(ctx, b.product)} ({ctx.t('best_pick')}): " + " · ".join(
            why or [_reason(ctx, b.reason_key, ctx.lang)])
        rest = [f"*{o.label}* {C.product_name(ctx, o.product)}: {_reason(ctx, o.reason_key, ctx.lang)}"
                for o in opts.options if o.role != "best"]
        lines = [ack, line_b, " · ".join(rest)] if rest else [ack, line_b]
        ctx.ev("note_shortlist", slots=list(note.get("slots") or []), wants=list((note.get("wants") or {}).keys()),
               best=b.product.sku)
    card = C.english(ctx, lambda: options_card(ctx, opts))
    demo = pick_demo(opts, s.home, pack, ctx.facts.snap.guides)
    if demo:
        d, other, _ = demo
        s.demo_recipe = d.id
        ctx.x["demo_other"] = other
        ctx.x["demos_sent"] = [d.id]
    btns = [ctx.b("b_demo_next", ctx.sid("demo")), ctx.b("b_offers_on", ctx.sid("ofs"))]  # offers with the products
    btns.append(ctx.b("b_tryon", ctx.sid("tryon")) if pack.placeable else ctx.b("b_they_chose", ctx.sid("prefer")))
    m1 = info_reply(ctx, "\n".join(lines), btns, card=card)
    if ctx.x.pop("rerank_note", None):
        m1.footer = ctx.t(f"rr_footer_{ctx.x.get('rerank_last', 'lower')}")
    return ctx.reply(m1)


async def demo(ctx: SkillContext) -> Reply:
    """The one demo that shows the difference between the options (picked when the options were shown)."""
    s = ctx.session
    if not s.shortlist:
        return await show_options(ctx)
    d = ctx.pack.demo(s.demo_recipe) if s.demo_recipe else None
    if d is None:
        opts = _option_set(ctx)
        nxt = pick_demo(opts, s.home, ctx.pack, ctx.facts.snap.guides) if opts else None
        if nxt is None:
            return await prefer(ctx)
        d, other, _ = nxt
        s.demo_recipe = d.id
        ctx.x["demo_other"] = other
    from chanakya.brain.demos import _rule_for_spec

    rule = _rule_for_spec(ctx.pack, d.differs_on) if d.differs_on else None  # the reason THIS demo shows
    reason_key = rule.id if rule is not None and rule.id in ctx.pack.reasons else None
    sent = ctx.x.setdefault("demos_sent", [])
    if d.id not in sent:  # so "Dusra demo" shows a different one
        sent.append(d.id)
    ctx.ev("demo_recipe_sent", recipe=d.id)
    return ctx.reply(demo_message(ctx, d, ctx.x.get("demo_other"), reason_key=reason_key))


async def prefer(ctx: SkillContext) -> Reply:
    """'Which one felt right for your home?' — the three options and 'none of these', as one list."""
    from chanakya.channel.limits import truncate, truncate_title
    from chanakya.core.contracts import ListRow, ListSection

    s = ctx.session
    if not s.option_labels:
        return await show_options(ctx)
    rows = []
    for lab, sku in sorted(s.option_labels.items()):
        p = ctx.facts.product_sync(sku)
        role = {"value": "value_fit", "step": "step_up"}.get(state.option_role(s, lab))
        reason = _reason(ctx, role, ctx.lang) if role else ""
        if state.option_role(s, lab) == "best":
            from chanakya.brain.fit import fit

            top = fit(p, s.home, ctx.pack, ctx.facts.snap.guides).top_rule()
            reason = _reason(ctx, top if top in ctx.pack.reasons else "value_fit", ctx.lang)
        rows.append(ListRow(id=ctx.sid("opt", lab), title=truncate(f"{lab}: {p.model}", 24), description=truncate(reason, 72)))
        ctx.reg.raw(rows[-1].title, f"product:{sku}")
    rows.append(ListRow(id=ctx.sid("nonefit"), title=ctx.t("row_none_fit"), description=ctx.t("row_none_fit_d")))
    ctx.reg.static(" ".join((r.description or "") for r in rows))
    cites = C.known_cites(ctx)
    key = "bolo_prefer_personal" if ctx.pack.category == "laptop" else "bolo_prefer"  # a laptop is for a person
    msg = coach_reply(ctx, ctx.tc(key), ctx.t("kyun_prefer"), cites, [])
    msg.kind = "list"
    msg.list_button = truncate_title(ctx.t("list_prefer"))
    msg.sections = [ListSection(rows=rows)]
    msg.allowed_numbers = ctx.reg.allowed()
    return ctx.reply(msg)


async def none_fit(ctx: SkillContext) -> Reply:
    btns = [ctx.b("b_rr_lower", ctx.sid("rerank", "lower")), ctx.b("b_rr_higher", ctx.sid("rerank", "higher")),
            ctx.b("b_rr_brand", ctx.sid("rerank", "brand"))]
    ctx.ev("options_rejected", shown=list(ctx.session.option_labels.values()))
    return ctx.reply(coach_reply(ctx, ctx.tc("bolo_missing"), ctx.t("kyun_missing"), C.known_cites(ctx), btns))


async def rerank(ctx: SkillContext, how: str) -> Reply:
    """New options from what the customer said was missing: lower cost, bigger / more, or another brand."""
    s = ctx.session
    shown = [ctx.facts.product_sync(sku) for sku in s.option_labels.values()]
    shown = [p for p in shown if p]
    before = sorted(s.option_labels.values())
    ex = set(ctx.x.get("excluded_skus") or [])
    if how == "lower" and shown:
        low = min(p.price for p in shown)
        s.home.start_level_inr = int(low * 0.92 / 1.3)
        ex |= {p.sku for p in shown if p.price > low}
    elif how == "higher" and shown:
        high = max(p.price for p in shown)
        s.home.start_level_inr = int(high * 1.25)
        ex |= {p.sku for p in shown if p.price < high}
    elif how == "brand":
        ctx.x["excluded_brands"] = sorted(set(ctx.x.get("excluded_brands") or []) | {p.brand for p in shown})
    s.home.start_level_source = "associate_tap"
    ctx.x.pop("range_id", None)
    ctx.x["excluded_skus"] = sorted(ex)
    ctx.x["rerank_note"] = True
    ctx.x["rerank_last"] = how
    ctx.ev("options_reranked", how=how)
    r = await show_options(ctx)
    if sorted(s.option_labels.values()) == before:
        r.messages[0].footer = ctx.t("rr_none")[:60]
    return r


def demo_message(ctx: SkillContext, d, other_label: str | None, reason_key: str | None = None) -> OutMessage:
    s = ctx.session
    pack = ctx.pack
    best_p = ctx.facts.product_sync(state.best_sku(s)) if state.best_sku(s) else None
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
    btns = [ctx.b("b_shown", ctx.sid("shown")), ctx.b("b_demo2", ctx.sid("demo2"))]
    btns.append(ctx.b("b_tryon", ctx.sid("tryon")) if pack.placeable else ctx.b("b_they_chose", ctx.sid("prefer")))
    return coach_reply(ctx, say, why, cites, btns)


async def shown(ctx: SkillContext) -> Reply:
    """Demo done (the price gate opens): ask which one felt right."""
    s = ctx.session
    if not s.shortlist:
        return await show_options(ctx)
    s.demo_done = True
    state.advance(s, Step.DEMONSTRATED)
    ctx.ev("demo_shown", recipe=s.demo_recipe)
    return await prefer(ctx)


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
    if nxt is None:  # no other honest difference to show: ask which one felt right
        return await prefer(ctx)
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
    if not s.option_labels and not state.options_gate_open(s, ctx.pack):
        from chanakya.skills import validate  # nothing known about the home yet: an honest range + one question

        r = await validate.price_rescue(ctx)
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


async def offers_view(ctx: SkillContext) -> Reply:
    """'Offers' next to the products: every live offer on each option (or the product being looked at), as one
    list; one tap gives the line to say. Only offers from the data; nothing is invented when there are none."""
    from chanakya.channel.limits import truncate, truncate_title
    from chanakya.core.contracts import ListRow, ListSection, OutMessage
    from chanakya.skills.brief import _offer_detail
    from chanakya.truth import fmt

    s = ctx.session
    items = sorted(s.option_labels.items()) if s.option_labels else []
    if not items and s.chosen_sku:
        items = [("", s.chosen_sku)]
    if not items and ctx.x.get("label_sku"):  # a scanned product
        items = [("", ctx.x["label_sku"])]
    if not items:
        return await show_options(ctx)
    rows, n_off = [], 0
    for lab, sku in items:
        p = ctx.facts.product_sync(sku)
        if p is None:
            continue
        head = f"{lab} · " if lab else ""
        offs = PV.live_offers_for(ctx, p)
        if not offs:
            rows.append(ListRow(id=ctx.sid("ofd", "none", lab or "x"), title=truncate(head + p.short_name(), 24),
                                description=truncate(ctx.t("offer_none"), 72)))
            continue
        for o, _ in offs[: max(1, 9 // len(items))]:
            n_off += 1
            till = ctx.t("brief_offer_till", date=ctx.reg.raw(fmt.date_short(o.valid_to, ctx.lang), f"offer:{o.id}:valid_to"))
            desc = " · ".join(x for x in (p.short_name(), _offer_detail(ctx, o), till) if x)
            rows.append(ListRow(id=ctx.sid("ofd", o.id, lab or "x"), title=truncate(head + ctx.pick(o.title, "en"), 24),
                                description=truncate(desc, 72)))
    for r in rows[:10]:
        ctx.reg.raw(r.title + " " + (r.description or ""), "offers:list")
    ctx.ev("offers_viewed", skus=[sku for _, sku in items], offers=n_off)
    if not n_off:
        btns = [ctx.b("b_demo_next", ctx.sid("demo")) if s.shortlist else ctx.b("b_options", ctx.sid("options")),
                ctx.b("b_they_chose", ctx.sid("prefer")) if s.option_labels else ctx.b("b_next_q", ctx.sid("go"))]
        return ctx.reply(info_reply(ctx, ctx.t("offers_none_body"), btns))
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("offers_list_body"),
                                list_button=truncate_title(ctx.t("list_offers")), sections=[ListSection(rows=rows[:10])],
                                footer=ctx.t("offers_list_footer"), allowed_numbers=ctx.reg.allowed()))


async def offer_detail(ctx: SkillContext, offer_id: str, label: str) -> Reply:
    """One offer, tapped from the list: what it gives and till when (from its row), and the line to say."""
    from chanakya.skills.brief import _offer_detail
    from chanakya.truth import fmt

    s = ctx.session
    o = next((x for x in ctx.facts.snap.offers if x.id == offer_id), None)
    if o is None:
        return await offers_view(ctx)
    sku = s.option_labels.get(label) or s.chosen_sku or ctx.x.get("label_sku")
    p = ctx.facts.product_sync(sku) if sku else None
    cl = ctx.clang
    with ctx.as_lang(cl):
        detail = _offer_detail(ctx, o)
        till = ctx.t("brief_offer_till", date=ctx.reg.raw(fmt.date_short(o.valid_to, cl), f"offer:{o.id}:valid_to"))
    title = ctx.pick(o.title, cl)
    detail = detail.replace(" · ", ", ")  # a line to say, not a label
    say = ctx.tc("bolo_offer_line", title=title, detail=(detail + ", ") if detail else "", till=till)
    why = ctx.t("kyun_offer_line", product=C.product_name(ctx, p) if p else "")
    btns = [ctx.b("b_offers_on", ctx.sid("ofs"))]
    if s.shortlist and not s.demo_done:
        btns.insert(0, ctx.b("b_demo_next", ctx.sid("demo")))
    if s.option_labels:
        btns.append(ctx.b("b_they_chose", ctx.sid("prefer")))
    ctx.ev("offer_explained", offer=o.id, sku=sku)
    return ctx.reply(coach_reply(ctx, say, why, C.known_cites(ctx), btns[:3]))
