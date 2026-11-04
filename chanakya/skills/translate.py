"""Step 4 — Saral Samjhao (Translate): label photo → SKU → 2–3 benefits for this home. Never a price here."""

from __future__ import annotations

from datetime import datetime

from chanakya.ai.schemas import ImageRead
from chanakya.brain import packs as P
from chanakya.brain.fit import fit
from chanakya.channel.limits import truncate
from chanakya.core import state
from chanakya.core.coach import bolo_block, coach_reply, do_block, info_reply
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


async def on_label(ctx: SkillContext, read: ImageRead) -> Reply:
    s = ctx.session
    sp = ctx.facts.product_sync(read.sku) if read.sku else None
    cat = s.home.category or (sp.category if sp else None) or (ctx.staff.dept if ctx.staff else None) or "tv"
    cands = ctx.facts.rank_label(read, cat)
    if read.raw_text:
        ctx.ev("label_text_ignored_as_instructions", text=read.raw_text[:80])
    if not ctx.facts.snap.products:  # nothing loaded from the backend (yet) to match the label against
        return ctx.reply(info_reply(ctx, ctx.t("catalog_unavailable")))
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
    return await show_sku(ctx, sku, conf, scanned=True)


async def show_sku(ctx: SkillContext, sku: str, conf: float = 100.0, scanned: bool = False) -> Reply:
    """`scanned`: a photo of the shelf label (its price is printed there, so it is said at once); a product asked
    about by voice or a button keeps the price for after the demo."""
    x = ctx.x
    if scanned:
        x["label_scanned"] = sku
    elif x.get("label_scanned") != sku:
        x.pop("label_scanned", None)
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


def _wants(ctx: SkillContext) -> dict:
    return ctx.session.extra.get("wants") or {}


def hl_points(ctx: SkillContext, p, n: int = 3, general: bool = False) -> list:
    """The product's own highlights that answer this customer's needs (brain/highlights.py)."""
    from chanakya.brain import highlights as HL

    if ctx.pack is None or p.category != ctx.pack.category:
        return []
    return HL.points(p, ctx.session.home, _wants(ctx), ctx.pack, n=n, general=general)


def hl_say(ctx: SkillContext, p, pt, lang: str) -> str:
    """The customer line for a highlight point, its proof registered from the product's own highlight."""
    from chanakya.brain import highlights as HL

    ctx.reg.raw(pt.proof, f"highlight:{p.sku}")
    return ctx.reg.static(HL.say(pt, lang))


def _next_benefit(ctx: SkillContext, p, said: list, general: bool = False):
    """('rule', benefit) from the pack (spec-proven), else ('hl', point) from the product's own highlights; the
    strongest not yet said. A general highlight only when asked for (nothing for this home yet)."""
    for b in matching(ctx, p):
        if b.id not in said:
            return "rule", b
    for pt in hl_points(ctx, p, n=8, general=general):
        if f"hl_{pt.theme}" not in said and (general or pt.need):
            return "hl", pt
    return None, None


async def benefit(ctx: SkillContext, first: bool = False) -> Reply:
    x = ctx.x
    sku = x.get("label_sku") or ctx.session.chosen_sku or state.best_sku(ctx.session)
    p = ctx.facts.product_sync(sku) if sku else None
    if p is None:
        return ctx.reply(info_reply(ctx, ctx.t("label_send")))
    said = x.setdefault("benefits_said", {}).setdefault(sku, [])
    if first:  # a (re)scan starts the benefits for this product again
        said.clear()
    gate = state.options_gate_open(ctx.session, ctx.pack)
    kind, nxt = _next_benefit(ctx, p, said)
    if nxt is None and first and gate:  # the home is known but nothing of it matches: the product's own best point
        kind, nxt = _next_benefit(ctx, p, said, general=True)
    if nxt is None and first:
        # no home facts match yet: show the product from the data, and steer back to the home questions
        card = await product_card_en(ctx, p)
        slot = C.pick_next_slot(ctx)
        btns = [ctx.b("b_next_q", ctx.sid("go")), ctx.b("b_compare", ctx.sid("compare")),
                ctx.b("b_options", ctx.sid("options"))]
        n = state.missing_for_options(ctx.session, ctx.pack)
        say = ctx.tc("bolo_label_intro" if n else "bolo_label_ready", product=C.product_name(ctx, p))
        why = ctx.t("kyun_label_gate", n=ctx.reg.num(n, "calc:gate_missing")) if n else ctx.t("kyun_options_ready")
        cites = C.known_cites(ctx) or ([slot] if slot else [])
        if slot and not C.known_cites(ctx):
            ctx.asking.add(slot)
        facts = card_facts(ctx, p, card)
        if not cites:
            return ctx.reply(info_reply(ctx, bolo_block(ctx, say) + "\n\n" + "\n".join(f"• {f}" for f in facts)
                                        + "\n\n" + do_block(ctx, "do_benefit"), btns, card=card))
        return ctx.reply(coach_reply(ctx, say, " · ".join([why, *facts]), cites, btns, card=card, do="do_benefit"))
    if nxt is None or len(said) >= 2:
        return await ignore_line(ctx, p)
    lang = ctx.clang
    if kind == "rule":
        text = ctx.pick(nxt.say_who, lang) if (nxt.say_who.get(lang) and ctx.session.home.sports_viewer) else ""
        say = await fill(ctx, text or ctx.pick(nxt.say, lang), p, lang)
        rid, cites_all = nxt.id, list(nxt.cites)
        cite = nxt.cites[0] if nxt.cites else None
        sd = ctx.pack.slot(cite) if cite else None
        vt = ctx.reg.static(", ".join(sd.value_title(v, ctx.lang) for v in P.values(ctx.session.home, cite))) if sd else ""
        why = ctx.t("kyun_benefit", value=vt)
    else:
        say = hl_say(ctx, p, nxt, lang)
        rid = f"hl_{nxt.theme}"
        cites_all = [n.slot for n in _theme_needs(ctx, nxt.theme) if n.slot and P.known(ctx.session.home, n.slot)]
        why = ctx.t("kyun_hl" if nxt.need else "kyun_hl_general", proof=ctx.reg.raw(nxt.proof, f"highlight:{p.sku}"))
    said.append(rid)
    ctx.ev("benefit_said", rule_id=rid, cites=cites_all, sku=sku)
    say = f"{say} {ctx.tc('trial_q')}"  # trial close: does this benefit matter to them?
    btns = [
        ctx.b("b_matters", ctx.sid("bt", "yes", rid)),
        ctx.b("b_not_really", ctx.sid("bt", "no", rid)),
        ctx.b("b_compare", ctx.sid("compare")),
    ]
    card = None
    if first:
        card = await product_card_en(ctx, p)
        if not gate:  # judge the fit only once the home is understood
            n = state.missing_for_options(ctx.session, ctx.pack)
            why = ctx.t("kyun_label_gate", n=ctx.reg.num(n, "calc:gate_missing"))
            btns = [btns[0], btns[1], ctx.b("b_next_q", ctx.sid("go"))]
    cites = [c for c in cites_all if P.known(ctx.session.home, c)] or cites_all or C.known_cites(ctx)
    facts = card_facts(ctx, p, card) if card is not None else []
    if not cites:
        body = bolo_block(ctx, say) + ("\n\n" + "\n".join(f"• {f}" for f in facts) if facts else "")
        return ctx.reply(info_reply(ctx, body + "\n\n" + do_block(ctx, "do_benefit"), btns, card=card))
    return ctx.reply(coach_reply(ctx, say, " · ".join([why, *facts]), cites, btns, card=card, do="do_benefit"))


def card_facts(ctx: SkillContext, p, card: Card) -> list[str]:
    """What the product card does not show (it is the reference design, user 5 Nov 2026), said in the message: a
    scanned label's shelf price and MRP saving, its live offer (or none), and the match for this home."""
    d = card.data or {}
    out = []
    if d.get("price_str"):
        out.append(ctx.t("fact_price", price=d["price_str"]) + (f", {d['mrp_str'].replace(' · ', ', ')}"
                                                                if d.get("mrp_str") else ""))
    out.append(ctx.t("fact_offer", offer=PV.offer_line(ctx, p, ctx.lang)["text"].replace(" · ", ", ")))
    if d.get("fit_str"):
        out.append(ctx.t("fact_fit", fit=d["fit_str"]))
    return out


async def made_for(ctx: SkillContext, p) -> OutMessage | None:
    """Saral Samjhao at the moment of choice (user, 4 Nov 2026): the customer picked this one, so say back, in their
    words, why it is right for THEIR home (their need + the product's own proof), with the 'Made for your home' card.
    Once per chosen model; the next step (site questions, then the full cost) follows in the same reply."""
    x = ctx.x
    if x.get("made_for_sku") == p.sku or p.category != ctx.pack.category:
        return None
    lang = ctx.clang
    rule = next(iter(matching(ctx, p)), None)
    pts = hl_points(ctx, p, n=1, general=False)
    if rule is not None:
        say = await fill(ctx, ctx.pick(rule.say, lang), p, lang)
        rid = rule.id
    elif pts:
        say, rid = hl_say(ctx, p, pts[0], lang), f"hl_{pts[0].theme}"
    else:
        return None  # nothing proven for this home: no empty praise
    x["made_for_sku"] = p.sku
    with ctx.as_lang("en"):
        chip = ctx.t("pdp_picked")
    card = await product_card_en(ctx, p, chip=chip, chip_kind="best")
    card.data["picked"] = True
    if not card.data.get("made"):
        return None
    ctx.ev("made_for_sent", sku=p.sku, lead=rid, points=len(card.data["made"]))
    msg = coach_reply(ctx, ctx.tc("bolo_made_for", line=say), ctx.t("kyun_made_for"), C.known_cites(ctx), [], card=card,
                      status=False, do="do_made_for")
    msg.kind = "image"
    return msg


def _theme_needs(ctx: SkillContext, theme_id: str) -> tuple:
    from chanakya.brain import highlights as HL

    th = HL.theme(ctx.pack.category, theme_id)
    return th.needs if th else ()


async def benefit_reply(ctx: SkillContext, answer: str, rule_id: str | None) -> Reply:
    """Trial-close answer: 'matters' is remembered (it steers the options); then the next benefit, or what to ignore."""
    x = ctx.x
    sku = x.get("label_sku") or ctx.session.chosen_sku
    key = "benefits_liked" if answer == "yes" else "benefits_skipped"
    if rule_id:
        x.setdefault(key, {}).setdefault(sku or "-", []).append(rule_id)
    ctx.ev("benefit_trial_close", rule_id=rule_id, answer=answer, sku=sku)
    return await benefit(ctx)


IGNORE_KEYS = {  # what a customer is often sold on, though every model of that size may have it
    "tv": ["resolution", "wifi", "panel", "hdr_formats", "os"],
    "ac": ["inverter", "star"],
    "wm": ["automatic", "load"],
    "fridge": ["frost_free", "door"],
    "laptop": ["laptop_type", "os", "ram_gb"],
}
IGNORE_SHARE = 0.9
IGNORE_MIN = 5


def common_specs(ctx: SkillContext, p) -> list[tuple[str, str, int, int, str | None]]:
    """[(spec key, its value, models with it, models of its size, size label)]: what (nearly) every model of this
    product's key size in stock also has, so it is not a reason to pay more. From today's stock only."""
    from chanakya.skills.brief import _bucket

    cat = p.category
    avail = [q for q in ctx.facts.products_sync(cat) if q.qty > 0 or q.nearby_qty > 0]
    b = _bucket(cat, p)
    same = [q for q in avail if (qb := _bucket(cat, q)) and qb[0] == b[0]] if b else avail
    if len(same) < IGNORE_MIN:
        return []
    out = []
    for key in IGNORE_KEYS.get(cat, []):
        v = p.spec(key)
        if v is None or v is False or v == "":
            continue
        n = sum(1 for q in same if str(q.spec(key)) == str(v))
        if n >= IGNORE_SHARE * len(same):
            out.append((key, PV.label(key, "en") if v is True else str(v), n, len(same), b[1] if b else None))
    return out


async def ignore_line(ctx: SkillContext, p, ms: list | None = None) -> Reply:
    """After the benefits, honestly: what is NOT a reason to pay more, only when today's stock proves it (nearly every
    model of this size has it too). Otherwise nothing is called unimportant."""
    btns = [ctx.b("b_compare", ctx.sid("compare")), ctx.b("b_options", ctx.sid("options")),
            ctx.b("b_tryon", ctx.sid("tryon"))]
    if ctx.session.shortlist:
        btns[1] = ctx.b("b_cost_show", ctx.sid("cost_show"))
    common = common_specs(ctx, p)
    if not common:
        return ctx.reply(info_reply(ctx, ctx.t("benefits_done"), btns[:2]))
    key, val, n, total, size = common[0]
    ctx.ev("ignore_said", sku=p.sku, specs=[key], share=f"{n}/{total}")
    spec = ctx.reg.raw(val, f"spec:{p.sku}:{key}")
    size_t = ctx.reg.raw(size, f"stock:{p.category}:bucket") if size else ""
    cat = ctx.pick(ctx.packs.get(p.category).label, ctx.clang)
    say = ctx.tc("bolo_ignore_data" if size_t else "bolo_ignore_data_all", spec=spec, size=size_t, cat=cat)
    why = ctx.t("kyun_ignore_data", n=ctx.reg.num(n, f"stock:{p.category}:{key}"),
                total=ctx.reg.num(total, f"stock:{p.category}:bucket_count"), spec=spec)
    cites = C.known_cites(ctx)
    if not cites:
        return ctx.reply(info_reply(ctx, bolo_block(ctx, say) + "\n\n" + do_block(ctx, "do_ignore"), btns))
    return ctx.reply(coach_reply(ctx, say, why, cites, btns, do="do_ignore"))


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


def _price_ok(ctx: SkillContext, *skus: str) -> bool:
    """The price is said after the demo; a scanned label shows it at once (it is printed on the shelf tag)."""
    return state.price_gate_open(ctx.session, ctx.pack) or ctx.x.get("label_scanned") in skus


async def product_card_en(ctx: SkillContext, p, **chip) -> Card:
    """The product page: one card in English for the customer and the associate (no copy)."""
    with ctx.as_lang("en"):
        return await product_card(ctx, p, **chip)


async def product_card(ctx: SkillContext, p, chip: str | None = None, chip_label: str | None = None,
                       chip_kind: str | None = None) -> Card:
    """The product page (user, 5 Nov 2026: like an e-commerce product page), straight from the database: the brand
    (its logo when the backend has one), the name, a subtitle of its own specs, the photo and more real photos; what
    it means for THIS home first (once the home is understood: their needs and asks, each with the product's own
    proof), then its other highlights; the key features as tiles; the offer, the price (after the demo or a label
    scan), stock and warranty. `chip`: 'Best fit for your home' on an option (with its letter), 'Picked' once chosen;
    a scanned label shows the fit badge instead."""
    lang = ctx.clang
    s = ctx.session
    info = p.info or {}
    stock = C.stock_line(ctx, p, lang)
    off = PV.offer_line(ctx, p, lang)
    specs = PV.key_specs(ctx, p, lang, 6)
    feats = PV.features(ctx, p, lang, 5)
    full = C.product_title(ctx, p, lang)
    data = {"title": full if len(full) <= 28 else C.product_name(ctx, p),  # the backend's long names: the short one
            "brand": ctx.reg.static(p.brand or ""), "image": p.image,
            "brand_logo": info.get("brand_logo"), "subtitle": PV.subtitle(ctx, p, lang),
            "gallery": [u for u in info.get("gallery") or [] if u and u != p.image][:3],
            "key_specs": specs, "features": feats[:4], "stock_line": stock, "offer_text": off["text"],
            "offer_none": off["none"],
            "price_str": ctx.reg.inr(p.price, p.price_source) if _price_ok(ctx, p.sku) else None,
            "chip": chip, "chip_label": chip_label, "chip_kind": chip_kind}
    if not any(x.get("key") == "warranty" for x in specs):
        data["warranty"] = PV.fmt_value(ctx, p, "warranty", lang)
    if data["price_str"] and p.mrp and p.mrp > p.price:  # the backend's MRP: what they save today
        pct = round((p.mrp - p.price) * 100 / p.mrp)
        data["mrp_str"] = ctx.t("pc_mrp", lang, mrp=ctx.reg.inr(p.mrp, p.price_source),
                                pct=ctx.reg.num(pct, f"calc:mrp_saving:{p.sku}"))
    mn = info.get("model_number")
    if mn and not chip:
        data["note"] = ctx.t("label_note", lang, model=ctx.reg.raw(mn, f"product:{p.sku}"))
    also = [{"text": f, "icon": "sparkle"} for f in feats]
    if state.options_gate_open(s, ctx.pack) and p.category == ctx.pack.category:
        from chanakya.skills import fitview as FV

        pts = [x for x in FV.points(ctx, p, lang, 4) if isinstance(x, dict) and x.get("text")]  # for THIS home
        needs = [dict(x, tag=ctx.t("pdp_asked", lang) if x.get("kind") == "asked" else None)
                 for x in pts if x.get("kind") in ("need", "asked")]
        general = [x for x in pts if x.get("kind") == "general"]
        said = {x["text"].lower() for x in needs + general}
        also = general + [x for x in also if x["text"].lower() not in said]  # then its own words, not said twice
        data.update(needs=needs[:4], made=needs[:3] or pts[:3])
        if not chip:  # a scanned label: the fit for this home, and what to keep in mind
            pros, cons, score = await pros_cons(ctx, p, lang)
            badge, kind = _badge(ctx, score, lang)
            data.update(pros=pros, cons=cons, badge=badge, badge_kind=kind,
                        fit_str=ctx.reg.num(score, f"calc:fit:{p.sku}") + "%")
    tiles = [str(x.get("value") or "").lower() for x in specs if len(str(x.get("value") or "")) >= 3]
    also = [x for x in also if not any(v in x["text"].lower() for v in tiles)]  # not the tiles said again
    data["also"] = also[: 5 - min(len(data.get("needs") or []), 4)]
    # the card (the reference design): four rows, their needs first, each with the product's own proof; three tiles,
    # each one of its own photos (or a close view of its one photo) with its own specs
    rows, proofs = [], []
    for x in (data.get("needs") or []) + data["also"]:
        proof = x.get("proof")
        if any(pf in x["text"].lower() for pf in proofs):
            continue  # its words are an earlier row's proof ('YouTube, Netflix apps' after '… (YouTube, Netflix)')
        if proof:
            proofs.append(str(proof).lower())
        nb = str(proof).replace(" ", "\u00a0") if proof else ""  # the proof stays on one line ('120 Hz')
        text = f"{x['text']} ({nb})" if proof and str(proof).lower() not in x["text"].lower() else x["text"]
        plain = text.replace("\u00a0", " ").lower()
        if any(plain in r["text"].replace("\u00a0", " ").lower() for r in rows):
            continue  # an earlier row already says it ('20 W speakers' as its proof)
        rows.append({"text": text, "icon": x.get("icon") or "sparkle"})
    data["rows"] = rows[:4]
    shots = data["gallery"]
    tall = p.category in ("fridge", "wm")  # one photo only: close views of it, like the reference's details
    crops = ["zoom-top", None, "zoom-bottom"] if tall else ["zoom-corner", None, "zoom-base"]
    data["tiles"] = [dict(tl, image=shots[i] if i < len(shots) else p.image, crop=None if i < len(shots) else crops[i])
                     for i, tl in enumerate(PV.tiles(ctx, p, lang))]
    return Card(template="product", lang=lang, data=data)  # type: ignore[arg-type]


async def benefit_card(ctx: SkillContext, p, ms: list) -> OutMessage:
    lang = ctx.clang
    benefits = [
        {"icon": ICONS.get(b.id, "check"), "text": await fill(ctx, ctx.pick(b.card, lang), p, lang)} for b in ms[:3]
    ]
    common = common_specs(ctx, p)
    ignore_text = ctx.t("ignore_prefix", lang) + " " + common[0][1] if common else ""
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
        if _price_ok(ctx, a.sku, b.sku):
            data["price_a"], data["price_b"] = ctx.reg.inr(a.price, a.price_source), ctx.reg.inr(b.price, b.price_source)
        found["winner"] = winner
        return Card(template="compare", lang=lang, data=data)  # type: ignore[arg-type]

    card = await C.english_async(ctx, build)
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
    return ctx.reply(info_reply(ctx, body, btns, card=card))


async def compare_smart(ctx: SkillContext) -> Reply:
    """'Compare' on a product: straight away with the customer's best pick (or, with no shortlist, the closest
    same-size model in stock); another label photo still works too."""
    from chanakya.skills.returning import alternative

    s = ctx.session
    sku = ctx.x.get("label_sku") or s.chosen_sku or state.best_sku(s)
    if not sku or ctx.facts.product_sync(sku) is None:
        return await compare_prompt(ctx)
    best = state.best_sku(s)
    other = best if best and best != sku else next((v for v in s.option_labels.values() if v != sku), None)
    if other is None:
        alt = alternative(ctx, sku)
        other = alt.sku if alt is not None else None
    if other is None:
        return await compare_prompt(ctx)
    ctx.ev("compare_direct", a=sku, b=other)
    return await compare(ctx, sku, other)


async def compare_prompt(ctx: SkillContext) -> Reply:
    ctx.x["compare_pending"] = True
    m = info_reply(ctx, ctx.t("compare_send"))
    m.footer = ctx.t("footer_compare")
    return ctx.reply(m)
