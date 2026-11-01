"""Step 4 — Saral Samjhao (Translate): label photo → SKU → 2–3 benefits for this home. Never a price here."""

from __future__ import annotations

from datetime import datetime

from chanakya.ai.schemas import ImageRead
from chanakya.brain import packs as P
from chanakya.brain.fit import fit
from chanakya.channel.limits import truncate
from chanakya.core import state
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Card, ListRow, ListSection, OutMessage, Reply
from chanakya.skills import common as C
from chanakya.skills import product_view as PV
from chanakya.skills.base import SkillContext

# technical terms: English in every language
SPEC_LABELS = {
    "hdmi_ports": {"hi": "HDMI ports", "mr": "HDMI ports", "en": "HDMI ports"},
    "speaker_w": {"hi": "Speaker watts", "mr": "Speaker watts", "en": "Speaker watts"},
    "dolby_atmos": {"hi": "Dolby Atmos", "mr": "Dolby Atmos", "en": "Dolby Atmos"},
    "os": {"hi": "Operating system", "mr": "Operating system", "en": "Operating system"},
    "resolution": {"hi": "Resolution", "mr": "Resolution", "en": "Resolution"},
    "wide_angle": {"hi": "Viewing angle", "mr": "Viewing angle", "en": "Viewing angle"},
    "anti_glare": {"hi": "Anti-glare", "mr": "Anti-glare", "en": "Anti-glare"},
    "panel": {"hi": "Panel type", "mr": "Panel type", "en": "Panel type"},
    "refrigerant": {"hi": "Refrigerant", "mr": "Refrigerant", "en": "Refrigerant"},
    "noise_db": {"hi": "Noise (dB)", "mr": "Noise (dB)", "en": "Noise (dB)"},
    "rpm": {"hi": "Spin RPM", "mr": "Spin RPM", "en": "Spin RPM"},
    "storage_gb": {"hi": "Storage", "mr": "Storage", "en": "Storage"},
    "cpu_tier": {"hi": "Processor", "mr": "Processor", "en": "Processor"},
    "inverter": {"hi": "Inverter", "mr": "Inverter", "en": "Inverter"},
    "quick_wash": {"hi": "Quick wash", "mr": "Quick wash", "en": "Quick wash"},
    "heater": {"hi": "Heater", "mr": "Heater", "en": "Heater"},
}
ICONS = {
    "b_motion": "motion",
    "b_bright": "sun",
    "b_size": "size",
    "b_dialogue": "voice",
    "b_angle": "angle",
    "b_dark": "film",
    "b_apps": "apps",
}
UNITS = {
    "refresh_hz": "",
    "brightness_nits": "",
    "size_in": "",
    "capacity_kg": "",
    "ram_gb": "",
    "battery_h": "",
    "weight_kg": "",
    "ton": "",
    "rpm": "",
    "noise_db": "",
    "storage_gb": "",
    "speaker_w": "",
}


def _spec_keys(cond: dict) -> set[str]:
    return set((cond or {}).keys())


def matching(ctx: SkillContext, p) -> list:
    pack = ctx.pack
    g = ctx.facts.snap.guides
    home = ctx.session.home
    out = []
    for i, b in enumerate(pack.benefits):
        if b.when_spec and not P.eval_spec_cond(p, home, b.when_spec, pack, g):
            continue
        if b.when_spec_any and not P.eval_spec_any(p, home, b.when_spec_any, pack, g):
            continue
        if not P.eval_slots_map(home, b.when_slots, pack):
            continue
        out.append((b.weight, -i, b))
    return [b for _, _, b in sorted(out, key=lambda x: (-x[0], -x[1]))]


async def fill(ctx: SkillContext, text: str, p, lang: str) -> str:
    """Fill benefit placeholders from data / Home Card / registered calculations."""
    ctx.reg.static(text)
    pack = ctx.pack
    home = ctx.session.home
    vals: dict[str, str] = {}
    for key in P.placeholders(text):
        if key in vals:
            continue
        v = None
        if key == "who":
            v = C.who_title(ctx, lang) or ""
        elif key == "size":
            v = ctx.reg.num(p.spec("size_in"), f"spec:{p.sku}:size_in")
        elif key == "distance":
            n = P.number(home, "seat_distance", pack)
            v = ctx.reg.num(n, "card:seat_distance") if n is not None else ""
        elif key == "apps":
            v = ", ".join(p.spec("apps") or [])
        elif key in ("kg", "ton"):
            v = ctx.reg.num(p.spec("capacity_kg" if key == "kg" else "ton"), f"spec:{p.sku}:{key}")
        elif key in ("sqft", "people", "hours"):
            slot = {"sqft": "room_size", "people": "family_size", "hours": "hours_per_day"}[key]
            n = P.number(home, slot, pack)
            v = ctx.reg.num(n, f"card:{slot}") if n is not None else ""
        elif key in ("monthly_bill", "five_year_saving"):
            from chanakya.truth.hisaab import lifetime_cost

            lt = await lifetime_cost(p, ctx.facts, ctx.reg)
            v = (lt or {}).get("monthly_str" if key == "monthly_bill" else "five_year_saving_str", "")
        else:
            raw = p.spec(key)
            if isinstance(raw, bool):
                v = ""
            elif isinstance(raw, (int, float)):
                v = ctx.reg.num(raw, f"spec:{p.sku}:{key}")
            elif isinstance(raw, list):
                v = ", ".join(map(str, raw))
            elif raw is not None:
                v = ctx.reg.raw(str(raw), f"spec:{p.sku}:{key}")
        vals[key] = v or ""
    from chanakya.i18n.lang import fill as _fill

    return " ".join(_fill(text, **vals).split())


def ignored_specs(ctx: SkillContext, p, used_benefits: list) -> list[str]:
    used = set()
    for b in used_benefits:
        used |= _spec_keys(b.when_spec)
        for c in b.when_spec_any:
            used |= _spec_keys(c)
    return [k for k in p.specs if k not in used and k not in ("size_in", "apps")]


async def on_label(ctx: SkillContext, read: ImageRead) -> Reply:
    s = ctx.session
    sp = ctx.facts.product_sync(read.sku) if read.sku else None
    cat = s.home.category or (sp.category if sp else None) or (ctx.staff.dept if ctx.staff else None) or "tv"
    cands = ctx.facts.rank_label(read, cat)
    if read.raw_text:
        ctx.ev("label_text_ignored_as_instructions", text=read.raw_text[:80])
    if not cands or cands[0][1] < 40:
        return ctx.reply(info_reply(ctx, ctx.t("label_unknown")))
    sku, conf = cands[0]
    if conf < 70:
        rows = [
            ListRow(id=ctx.sid("label", c[0]), title=truncate(ctx.facts.product_sync(c[0]).model, 24))
            for c in cands
            if c[1] >= 40
        ][:3]
        for r in rows:
            ctx.reg.raw(r.title, "product:label_match")
        return ctx.reply(
            OutMessage(
                to=ctx.actor_wa_id,
                kind="list",
                body=ctx.t("which_model"),
                list_button=ctx.t("list_choose"),
                sections=[ListSection(rows=rows)],
                allowed_numbers=ctx.reg.allowed(),
            )
        )
    return await show_sku(ctx, sku, conf)


async def show_sku(ctx: SkillContext, sku: str, conf: float = 100.0) -> Reply:
    x = ctx.x
    prod = ctx.facts.product_sync(sku)
    home = ctx.session.home
    if prod and ctx.packs.get(prod.category) and (home.category is None or (home.category != prod.category and not home.slots)):
        home.category = prod.category  # a scanned label tells us the product
    prev = x.get("label_sku")
    prev_at = x.get("label_at")
    x["label_sku"] = sku
    x["label_at"] = ctx.now.isoformat()
    from chanakya.skills.tryon_flow import add_interest

    add_interest(ctx, sku)
    x.setdefault("benefits_said", {})
    ctx.ev("label_read", sku=sku, confidence=round(conf, 1))
    if (
        prev
        and prev != sku
        and (
            x.pop("compare_pending", False)
            or (prev_at and (ctx.now - datetime.fromisoformat(prev_at)).total_seconds() <= 120)
        )
    ):
        return await compare(ctx, prev, sku)
    return await benefit(ctx, first=True)


async def benefit(ctx: SkillContext, first: bool = False) -> Reply:
    x = ctx.x
    sku = x.get("label_sku") or ctx.session.chosen_sku or ctx.session.option_labels.get("B")
    p = ctx.facts.product_sync(sku) if sku else None
    if p is None:
        return ctx.reply(info_reply(ctx, ctx.t("label_send")))
    said = x.setdefault("benefits_said", {}).setdefault(sku, [])
    if first:  # a (re)scan starts the benefits for this product again
        said.clear()
    ms = matching(ctx, p)
    nxt = next((b for b in ms if b.id not in said), None)
    if nxt is None and first:
        # no home facts match yet: show the product from the data, and steer back to the home questions
        card, copy = await C.dual_async(ctx, lambda: product_card(ctx, p))
        slot = C.pick_next_slot(ctx)
        btns = [ctx.b("b_next_q", ctx.sid("go")), ctx.b("b_compare", ctx.sid("compare")),
                ctx.b("b_options", ctx.sid("options"))]
        say = ctx.tc("bolo_label_intro", product=C.product_name(ctx, p))
        n = state.missing_for_options(ctx.session, ctx.pack)
        why = ctx.t("kyun_label_gate", n=ctx.reg.num(n, "calc:gate_missing")) if n else ctx.t("kyun_options_ready")
        cites = C.known_cites(ctx) or ([slot] if slot else [])
        if slot and not C.known_cites(ctx):
            ctx.asking.add(slot)
        if not cites:
            return ctx.reply(C.with_copy(ctx, info_reply(ctx, say, btns, card=card), copy))
        return ctx.reply(C.with_copy(ctx, coach_reply(ctx, say, why, cites, btns, card=card), copy))
    if nxt is None or len(said) >= 2:
        return await ignore_line(ctx, p, ms)
    lang = ctx.clang
    text = ctx.pick(nxt.say_who, lang) if (nxt.say_who.get(lang) and ctx.session.home.sports_viewer) else ""
    say = await fill(ctx, text or ctx.pick(nxt.say, lang), p, lang)
    said.append(nxt.id)
    ctx.ev("benefit_said", rule_id=nxt.id, cites=nxt.cites, sku=sku)
    cite = nxt.cites[0] if nxt.cites else None
    sd = ctx.pack.slot(cite) if cite else None
    vt = ctx.reg.static(", ".join(sd.value_title(v, ctx.lang) for v in P.values(ctx.session.home, cite))) if sd else ""
    n_ignored = len(ignored_specs(ctx, p, ms))
    why = ctx.t("kyun_benefit", value=vt, n=ctx.reg.num(n_ignored, f"calc:ignored_specs:{sku}"))
    say = f"{say} {ctx.tc('trial_q')}"  # trial close: does this benefit matter to them?
    btns = [
        ctx.b("b_matters", ctx.sid("bt", "yes", nxt.id)),
        ctx.b("b_not_really", ctx.sid("bt", "no", nxt.id)),
        ctx.b("b_compare", ctx.sid("compare")),
    ]
    card = copy = None
    if first:
        card, copy = await C.dual_async(ctx, lambda: product_card(ctx, p))
        if not state.options_gate_open(ctx.session, ctx.pack):  # judge the fit only once the home is understood
            n = state.missing_for_options(ctx.session, ctx.pack)
            why = ctx.t("kyun_label_gate", n=ctx.reg.num(n, "calc:gate_missing"))
            btns = [btns[0], btns[1], ctx.b("b_next_q", ctx.sid("go"))]
    cites = [c for c in nxt.cites if P.known(ctx.session.home, c)] or nxt.cites
    return ctx.reply(C.with_copy(ctx, coach_reply(ctx, say, why, cites, btns, card=card), copy))


async def benefit_reply(ctx: SkillContext, answer: str, rule_id: str | None) -> Reply:
    """Trial-close answer: 'matters' is remembered (it steers the options); then the next benefit, or what to ignore."""
    x = ctx.x
    sku = x.get("label_sku") or ctx.session.chosen_sku
    key = "benefits_liked" if answer == "yes" else "benefits_skipped"
    if rule_id:
        x.setdefault(key, {}).setdefault(sku or "-", []).append(rule_id)
    ctx.ev("benefit_trial_close", rule_id=rule_id, answer=answer, sku=sku)
    return await benefit(ctx)


async def ignore_line(ctx: SkillContext, p, ms: list | None = None) -> Reply:
    """After the benefits: the specs that do not matter for this home (only specs this product really has)."""
    ms = ms if ms is not None else matching(ctx, p)
    keys = [k for k in ignored_specs(ctx, p, ms) if k in PV.LABELS and not isinstance(p.spec(k), bool)]
    names = [PV.label(k, ctx.clang) for k in keys[:2]]
    btns = [ctx.b("b_compare", ctx.sid("compare")), ctx.b("b_options", ctx.sid("options")),
            ctx.b("b_tryon", ctx.sid("tryon"))]
    if ctx.session.shortlist:
        btns[1] = ctx.b("b_cost_show", ctx.sid("cost_show"))
    if not names:
        return ctx.reply(info_reply(ctx, ctx.t("benefits_done"), btns[:2]))
    ctx.ev("ignore_said", sku=p.sku, specs=keys[:2])
    say = ctx.tc("bolo_ignore", specs=ctx.reg.static(", ".join(names)))
    cites = C.known_cites(ctx)
    if not cites:
        return ctx.reply(info_reply(ctx, say, btns))
    return ctx.reply(coach_reply(ctx, say, ctx.t("kyun_ignore"), cites, btns))


def _badge(ctx: SkillContext, score: int, lang: str) -> tuple[str, str]:
    kind = "good" if score >= 85 else "partial" if score >= 60 else "poor"
    return ctx.t(f"badge_{kind}", lang), kind


async def pros_cons(ctx: SkillContext, p, lang: str) -> tuple[list[str], list[str], int]:
    """Pros / cons of this product for THIS home, from the fit rules (data + Home Card only)."""
    pack = ctx.pack
    f = fit(p, ctx.session.home, pack, ctx.facts.snap.guides)
    pros_t, cons_t = ctx.packs.common.get("fit_pros", {}), ctx.packs.common.get("fit_cons", {})
    pros, cons = [], []
    for c in sorted(f.contributions, key=lambda c: -c.weight):
        if c.match >= 0.99 and c.rule_id in pros_t:
            pros.append(await fill(ctx, ctx.pick(pros_t[c.rule_id], lang), p, lang))
        elif c.match < 0.75 and c.rule_id in cons_t:
            cons.append(await fill(ctx, ctx.pick(cons_t[c.rule_id], lang), p, lang))
    return pros[:4], cons[:3], f.score


async def product_card(ctx: SkillContext, p) -> Card:
    """Label scan → the product, straight from the database; the fit for this home once enough is known."""
    lang = ctx.clang
    s = ctx.session
    if p.qty > 0:
        stock = ctx.t("stock_in_store", lang, qty=ctx.reg.num(p.qty, f"stock:{p.sku}@{p.store_id}"))
    else:
        stock = ctx.t("stock_nearby", lang, days=ctx.reg.num(p.delivery_days, f"stock:{p.sku}:days"),
                      store=ctx.reg.raw(ctx.facts.store_name(p.nearby_store_id).replace("Reliance Digital ", "")
                                        .replace(" (TEST)", ""), "store"))
    data = {"title": C.product_title(ctx, p, lang), "image": p.image, "key_specs": PV.key_specs(ctx, p, lang, 6),
            "features": PV.features(ctx, p, lang, 4), "stock_line": stock, "offer_text": PV.offer_badge(ctx, p, lang),
            "price_str": ctx.reg.inr(p.price, p.price_source) if state.price_gate_open(s, ctx.pack) else None}
    mn = (p.info or {}).get("model_number")
    if mn:
        data["note"] = ctx.t("label_note", lang, model=ctx.reg.raw(mn, f"product:{p.sku}"))
    if state.options_gate_open(s, ctx.pack) and p.category == ctx.pack.category:
        pros, cons, score = await pros_cons(ctx, p, lang)
        badge, kind = _badge(ctx, score, lang)
        data.update(pros=pros, cons=cons, badge=badge, badge_kind=kind,
                    fit_str=ctx.reg.num(score, f"calc:fit:{p.sku}") + "%")
    return Card(template="product", lang=lang, data=data)  # type: ignore[arg-type]


async def benefit_card(ctx: SkillContext, p, ms: list) -> OutMessage:
    lang = ctx.clang
    benefits = [
        {"icon": ICONS.get(b.id, "check"), "text": await fill(ctx, ctx.pick(b.card, lang), p, lang)} for b in ms[:3]
    ]
    ign = ignored_specs(ctx, p, ms)[:3]
    ignore_text = (
        ctx.t("ignore_prefix", lang) + " " + ", ".join(ctx.pick(SPEC_LABELS.get(k, {"en": k}), lang) for k in ign)
        if ign
        else ""
    )
    card = Card(
        template="benefit",
        lang=lang,
        data={  # type: ignore[arg-type]
            "product_title": C.product_title(ctx, p, lang),
            "benefits": benefits,
            "ignore_text": ctx.reg.static(ignore_text),
            "product_image": p.image,
        },
    )
    return info_reply(ctx, ctx.t("benefit_card_caption"), card=card)


LOWER_IS_BETTER = {"weight_kg", "noise_db", "kwh_year"}


def _better(va, vb, key: str) -> str | None:
    if isinstance(va, bool) and isinstance(vb, bool):
        return None if va == vb else ("a" if va else "b")
    if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va != vb:
        hi = va > vb
        return ("b" if hi else "a") if key in LOWER_IS_BETTER else ("a" if hi else "b")
    return None


async def compare(ctx: SkillContext, sku_a: str, sku_b: str) -> Reply:
    """Two labels → side by side from the database, with which is better for THIS home."""
    a, b = ctx.facts.product_sync(sku_a), ctx.facts.product_sync(sku_b)
    s = ctx.session
    found: dict = {}

    async def build() -> Card:
        lang = ctx.clang
        keys = list(dict.fromkeys(PV.KEY_SPECS.get(a.category, []) + PV.KEY_SPECS.get(b.category, [])))
        rows, wins = [], {"a": 0, "b": 0}
        for k in keys:
            va, vb = PV.fmt_value(ctx, a, k, lang), PV.fmt_value(ctx, b, k, lang)
            if va is None and vb is None:
                continue
            better = _better(PV.raw_value(a, k), PV.raw_value(b, k), k)
            if better:
                wins[better] += 1
            rows.append({"label": ctx.reg.static(PV.label(k, lang)), "a": va or "–", "b": vb or "–", "better": better})
        data = {"title": ctx.t("compare_title", lang),
                "a": {"title": C.product_title(ctx, a, lang), "image": a.image},
                "b": {"title": C.product_title(ctx, b, lang), "image": b.image}, "rows": rows[:8]}
        gate = state.options_gate_open(s, ctx.pack) and a.category == b.category == ctx.pack.category
        winner = None
        if gate:
            pa, _, sa = await pros_cons(ctx, a, lang)
            pb, _, sb = await pros_cons(ctx, b, lang)
            data["a"].update(fit_str=ctx.reg.num(sa, f"calc:fit:{a.sku}") + "%", badge_kind=_badge(ctx, sa, lang)[1])
            data["b"].update(fit_str=ctx.reg.num(sb, f"calc:fit:{b.sku}") + "%", badge_kind=_badge(ctx, sb, lang)[1])
            data["a_pros"], data["b_pros"] = pa[:3], pb[:3]
            if sa != sb:
                winner = a if sa > sb else b
                data["verdict"] = ctx.t("compare_verdict", lang, name=C.product_name(ctx, winner))
        if state.price_gate_open(s, ctx.pack):
            data["price_a"], data["price_b"] = ctx.reg.inr(a.price, a.price_source), ctx.reg.inr(b.price, b.price_source)
        found["winner"] = winner
        return Card(template="compare", lang=lang, data=data)  # type: ignore[arg-type]

    card, copy = await C.dual_async(ctx, build)
    winner = found.get("winner")
    ctx.ev("compare_shown", a=a.sku, b=b.sku, winner=winner.sku if winner else None)
    if winner:
        body = ctx.t("compare_body_fit", a=C.product_name(ctx, a), b=C.product_name(ctx, b),
                     winner=C.product_name(ctx, winner))
    else:
        body = ctx.t("compare_body_nofit", a=C.product_name(ctx, a), b=C.product_name(ctx, b))
    btns = [C.option_button(ctx, "1", a), C.option_button(ctx, "2", b), ctx.b("b_tryon", ctx.sid("tryon"))]
    btns[0] = btns[0].model_copy(update={"id": ctx.sid("tpick", a.sku)})
    btns[1] = btns[1].model_copy(update={"id": ctx.sid("tpick", b.sku)})
    return ctx.reply(C.with_copy(ctx, info_reply(ctx, body, btns, card=card), copy))


async def compare_prompt(ctx: SkillContext) -> Reply:
    ctx.x["compare_pending"] = True
    m = info_reply(ctx, ctx.t("compare_send"))
    m.footer = ctx.t("footer_compare")
    return ctx.reply(m)
