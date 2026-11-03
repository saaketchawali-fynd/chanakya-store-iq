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
    from chanakya.core.clock import today
    from chanakya.jobs.price_watch import remember

    for o in opts.options:  # for the price watch: what they saw today
        remember(s, o.product, ctx.facts, today())
    s.extra["option_roles"] = {o.label: o.role for o in opts.options}
    best, value, step = opts.by_role("best"), opts.by_role("value"), opts.by_role("step")
    s.shortlist = [o.product.sku for o in (best, value, step) if o]
    from chanakya.skills.tryon_flow import add_interest, prefetch_photos

    add_interest(ctx, *s.shortlist)
    if pack.placeable and ctx.svc.orchestrator is not None:  # photos ready before 'Ghar mein dekho' is tapped
        ctx.svc.orchestrator.spawn(prefetch_photos(ctx.facts, list(s.shortlist)))
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


# the demo plan (user, 4 Nov 2026): what they asked for first, then their needs compared with the real options,
# then one more difference the data shows; each step names its order and the actual values on both models
FEATURE_KEYS = {
    "tv": ["resolution", "hdr_formats", "panel", "os", "refresh_hz", "brightness_nits", "speaker_w"],
    "ac": ["inverter", "star", "noise_db", "wifi"],
    "wm": ["load", "automatic", "rpm", "heater"],
    "fridge": ["frost_free", "door", "star_rating", "inverter_compressor", "convertible"],
    "laptop": ["ram_gb", "storage_gb", "battery_h", "gpu", "weight_kg", "screen_in"],
}
MAX_STEPS = 3


def _val(p, key: str):
    v = p.brand if key == "brand" else p.spec(key)
    return None if v in (None, "", []) else v


def demo_plan(ctx: SkillContext) -> list[dict]:
    """Up to three demo steps for THIS customer, in order. Each compares the best pick with an option that really
    differs on it (both values from the data)."""
    from chanakya.brain.demos import rank_demos

    s = ctx.session
    opts = _option_set(ctx)
    best = opts.by_role("best") if opts else None
    if best is None:
        return []
    others = [o for o in opts.options if o is not best]

    def differs(key: str | None) -> str | None:
        if not key:
            return others[0].label if others else None
        bv = _val(best.product, key)
        if bv is None:
            return None
        return next((o.label for o in others if _val(o.product, key) is not None and str(_val(o.product, key)) != str(bv)),
                    None)

    steps, used = [], set()
    for key in s.extra.get("wants") or {}:  # 1. what they asked for (picture type, brand, size …), shown first
        if _val(best.product, key) is None:
            continue
        steps.append({"kind": "want", "key": key, "other": differs(key)})  # no other differs: show it on the best
        used.add(key)
    for d, other, _ in rank_demos(opts, s.home, ctx.pack, ctx.facts.snap.guides):  # 2. their needs, strongest first
        if len(steps) >= MAX_STEPS - 1 or (d.differs_on and d.differs_on in used):
            continue
        steps.append({"kind": "recipe", "id": d.id, "other": other, "key": d.differs_on})
        if d.differs_on:
            used.add(d.differs_on)
    for key in FEATURE_KEYS.get(ctx.pack.category, []):  # 3. one more real difference in the data
        if len(steps) >= MAX_STEPS:
            break
        if key not in used and (lab := differs(key)):
            steps.append({"kind": "feature", "key": key, "other": lab})
            break
    return steps[:MAX_STEPS]


def _step_title(ctx: SkillContext, st: dict, lang: str) -> str:
    from chanakya.i18n.lang import pick

    if st["kind"] == "recipe":
        d = ctx.pack.demo(st["id"])
        return pick(d.title, lang) if d else st["id"]
    key = st["key"]
    if i18n_has(f"want_label_{key}"):
        return ctx.t(f"want_label_{key}", lang)
    return pick(PV.LABELS.get(key, ({"en": key}, ""))[0], lang)


def i18n_has(key: str) -> bool:
    from chanakya.i18n import lang as i18n

    return i18n.has(key)


def _values(ctx: SkillContext, key: str | None, best_lab: str, bp, other_lab: str, op) -> str:
    if not key:
        return ""
    from chanakya.brain import data_questions as DQ

    def fmt(p):
        if key == "brand":
            return ctx.reg.raw(p.brand, f"product:{p.sku}")
        v = PV.fmt_value(ctx, p, key, ctx.lang, customer=False)
        if v is None and isinstance(_val(p, key), bool):
            v = DQ.label(key, _val(p, key))
        return v

    vb, vo = fmt(bp), fmt(op)
    return ctx.t("demo_vals", a=best_lab, va=vb, b=other_lab, vb=vo) if vb and vo else ""


async def demo(ctx: SkillContext, i: int | None = None) -> Reply:
    """The demo plan, one step at a time: 'Demo 1/3 · what they asked for', with both models' values."""
    s = ctx.session
    if not s.shortlist:
        return await show_options(ctx)
    key = sorted(s.option_labels.values())
    if ctx.x.get("demo_plan_for") != key or not ctx.x.get("demo_plan"):
        ctx.x["demo_plan"], ctx.x["demo_plan_for"], ctx.x["demo_i"] = demo_plan(ctx), key, 0
    plan = ctx.x["demo_plan"]
    i = ctx.x.get("demo_i", 0) if i is None else i
    if not plan or i >= len(plan):
        ctx.x["demo_i"] = 0
        return await prefer(ctx)
    ctx.x["demo_i"] = i
    st = plan[i]
    best_lab = state.best_label(s) or "B"
    bp = ctx.facts.product_sync(s.option_labels[best_lab])
    alone = not st.get("other")  # what they asked for, and every option has it: show it on the best pick
    op = bp if alone else ctx.facts.product_sync(s.option_labels.get(st["other"]) or s.option_labels[best_lab])
    head = ctx.t("demo_head", i=ctx.reg.num(i + 1, "demo:step"), n=ctx.reg.num(len(plan), "demo:steps"))
    vals = "" if alone else _values(ctx, st.get("key"), best_lab, bp, st["other"], op)
    if alone and st.get("key"):
        v = PV.fmt_value(ctx, bp, st["key"], ctx.lang, customer=False) if st["key"] != "brand" else ctx.reg.raw(bp.brand, "product")
        vals = ctx.t("demo_val_one", a=best_lab, va=v) if v else ""
    has_next = i + 1 < len(plan)
    s.demo_recipe = st.get("id") or f"{st['kind']}:{st['key']}"
    sent = ctx.x.setdefault("demos_sent", [])
    if s.demo_recipe not in sent:
        sent.append(s.demo_recipe)
    ctx.ev("demo_recipe_sent", recipe=s.demo_recipe, demo_step=i + 1, of=len(plan))
    if st["kind"] == "recipe":
        from chanakya.brain.demos import _rule_for_spec

        d = ctx.pack.demo(st["id"])
        rule = _rule_for_spec(ctx.pack, d.differs_on) if d.differs_on else None
        reason_key = rule.id if rule is not None and rule.id in ctx.pack.reasons else None
        ctx.x["demo_other"] = st["other"]
        msg = demo_message(ctx, d, st["other"], reason_key=reason_key, why_prefix=head, why_suffix=vals,
                           has_next=has_next)
    else:
        what_c, what_a = _step_title(ctx, st, ctx.clang), _step_title(ctx, st, ctx.lang)
        say_key = "demo_want_one_say" if alone else f"demo_{st['kind']}_say"
        say = ctx.tc(say_key, what=what_c, best=C.product_name(ctx, bp), other=C.product_name(ctx, op))
        why = f"{head} · " + ctx.t(f"demo_{st['kind']}_why", what=what_a) + (f" · {vals}" if vals else "")
        msg = coach_reply(ctx, say, why, C.known_cites(ctx), _demo_buttons(ctx, has_next))
    if i == 0 and len(plan) > 1:  # the plan at a glance: what to show first, then next
        msg.footer = ctx.t("demo_plan_line", steps=" → ".join(_step_title(ctx, x, ctx.lang) for x in plan))[:60]
    return ctx.reply(msg)


def _demo_buttons(ctx: SkillContext, has_next: bool) -> list:
    btns = [ctx.b("b_shown", ctx.sid("shown"))]
    if has_next:
        btns.append(ctx.b("b_demo_next_step", ctx.sid("demo2")))
    btns.append(ctx.b("b_tryon", ctx.sid("tryon")) if ctx.pack.placeable else ctx.b("b_they_chose", ctx.sid("prefer")))
    return btns


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
    if ctx.pack.placeable and len(s.option_labels) >= 2:  # A vs B on their wall, in one picture
        rows.append(ListRow(id=ctx.sid("abwall"), title=truncate(ctx.t("row_abwall"), 24),
                            description=truncate(ctx.t("row_abwall_d"), 72)))
    if ctx.pack.category == "tv" and P.number(s.home, "seat_distance", ctx.pack):  # how big it feels from the sofa
        rows.append(ListRow(id=ctx.sid("view"), title=truncate(ctx.t("row_view"), 24),
                            description=truncate(ctx.t("row_view_d"), 72)))
    if state.price_gate_open(s, ctx.pack) and len(s.option_labels) >= 2:  # is the extra money worth it?
        rows.append(ListRow(id=ctx.sid("upgrade"), title=truncate(ctx.t("row_upgrade"), 24),
                            description=truncate(ctx.t("row_upgrade_d"), 72)))
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


def demo_message(ctx: SkillContext, d, other_label: str | None, reason_key: str | None = None, why_prefix: str = "",
                 why_suffix: str = "", has_next: bool = True) -> OutMessage:
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
    if why_prefix:
        why = f"{why_prefix} · {why}"
    if why_suffix:
        why = f"{why} · {why_suffix}"
    cites = [k for k in d.when_slots if P.known(s.home, k)] or C.known_cites(ctx)
    _ = pack
    return coach_reply(ctx, say, why, cites, _demo_buttons(ctx, has_next))


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
    """The next step of the demo plan; after the last one, which one felt right."""
    if not ctx.session.shortlist:
        return await show_options(ctx)
    return await demo(ctx, int(ctx.x.get("demo_i", 0)) + 1)


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


def _gains(ctx: SkillContext, lower, higher) -> list[str]:
    """What the dearer one really adds FOR THIS HOME, from the data: the fit rules it meets and the cheaper one
    does not (only rules today's catalogue can judge), and a bigger key size."""
    from chanakya.brain.data_questions import rule_informative

    pack, home, g = ctx.pack, ctx.session.home, ctx.facts.snap.guides
    avail = [p for p in ctx.facts.products_sync(pack.category) if p.qty > 0 or p.nearby_qty > 0]
    lo = {c.rule_id: c.match for c in fit(lower, home, pack, g).contributions}
    rules = {r.id: r for r in pack.fit_rules}
    out = []
    for c in sorted(fit(higher, home, pack, g).contributions, key=lambda c: -(c.weight * (c.match - lo.get(c.rule_id, 0)))):
        r = rules.get(c.rule_id)
        if r and c.match > lo.get(c.rule_id, 0) + 0.25 and c.rule_id in pack.reasons and rule_informative(r, avail, home, pack, g):
            txt = _reason(ctx, c.rule_id, ctx.lang)
            if txt and txt not in out:
                out.append(txt)
    for key, unit in (("size_in", "inch"), ("ton", "ton"), ("capacity_kg", "kg"), ("capacity_l", "L"), ("ram_gb", "GB RAM")):
        a, b = lower.spec(key), higher.spec(key)
        if a and b and float(b) > float(a):
            out.append(ctx.t("upg_more", what=ctx.reg.num(b, f"spec:{higher.sku}:{key}", unit)))
            break
    return out[:2]


async def upgrade_view(ctx: SkillContext) -> Reply:
    """'Is the extra money worth it?': each step up, what it costs (and per day over 5 years), and what it really
    adds for this home. When it adds nothing for them, it says so: the honest answer that wins trust."""
    s = ctx.session
    if not state.price_gate_open(s, ctx.pack) or len(s.option_labels) < 2:
        return await prefer(ctx)
    picks = sorted(((lab, ctx.facts.product_sync(sku)) for lab, sku in s.option_labels.items()), key=lambda x: x[1].price)
    lines = [ctx.t("upg_head")]
    for (la, a), (lb, b) in zip(picks, picks[1:], strict=False):
        diff = b.price - a.price
        if diff <= 0:
            continue
        src = f"calc:upgrade:{a.sku}:{b.sku}"
        per_day = ctx.reg.inr(max(1, round(diff / (5 * 365))), src)
        gains = _gains(ctx, a, b)
        what = " · ".join(gains) if gains else ctx.t("upg_nothing", lower=la)
        lines.append(ctx.t("upg_line", a=la, b=lb, diff=ctx.reg.inr(diff, src), day=per_day, what=what))
    ctx.ev("upgrade_check", options=[lab for lab, _ in picks])
    btns = [ctx.b("b_they_chose", ctx.sid("prefer")), ctx.b("b_offers_on", ctx.sid("ofs"))]
    return ctx.reply(info_reply(ctx, "\n".join(lines[:3]), btns))
