"""Step 4 — Saral Samjhao (Translate): label photo → SKU → 2–3 benefits for this home. Never a price here."""

from __future__ import annotations

from datetime import datetime

from chanakya.ai.schemas import ImageRead
from chanakya.brain import packs as P
from chanakya.channel.limits import truncate
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Card, ListRow, ListSection, OutMessage, Reply
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext

SPEC_LABELS = {
    "hdmi_ports": {"hi": "HDMI पोर्ट", "mr": "HDMI पोर्ट", "en": "HDMI ports"},
    "speaker_w": {"hi": "स्पीकर वॉट", "mr": "स्पीकर वॉट", "en": "Speaker watts"},
    "dolby_atmos": {"hi": "Dolby Atmos", "mr": "Dolby Atmos", "en": "Dolby Atmos"},
    "os": {"hi": "ऑपरेटिंग सिस्टम", "mr": "ऑपरेटिंग सिस्टम", "en": "Operating system"},
    "resolution": {"hi": "रेज़ोल्यूशन", "mr": "रिझोल्यूशन", "en": "Resolution"},
    "wide_angle": {"hi": "व्यूइंग एंगल", "mr": "व्ह्यूइंग अँगल", "en": "Viewing angle"},
    "anti_glare": {"hi": "एंटी-ग्लेयर", "mr": "अँटी-ग्लेअर", "en": "Anti-glare"},
    "panel": {"hi": "पैनल टाइप", "mr": "पॅनल प्रकार", "en": "Panel type"},
    "refrigerant": {"hi": "रेफ्रिजरेंट गैस", "mr": "रेफ्रिजरंट गॅस", "en": "Refrigerant"},
    "noise_db": {"hi": "आवाज़ (dB)", "mr": "आवाज (dB)", "en": "Noise (dB)"},
    "rpm": {"hi": "स्पिन RPM", "mr": "स्पिन RPM", "en": "Spin RPM"},
    "storage_gb": {"hi": "स्टोरेज", "mr": "स्टोरेज", "en": "Storage"},
    "cpu_tier": {"hi": "प्रोसेसर", "mr": "प्रोसेसर", "en": "Processor"},
    "inverter": {"hi": "इन्वर्टर", "mr": "इन्व्हर्टर", "en": "Inverter"},
    "quick_wash": {"hi": "क्विक वॉश", "mr": "क्विक वॉश", "en": "Quick wash"},
    "heater": {"hi": "हीटर", "mr": "हीटर", "en": "Heater"},
}
ICONS = {
    "b_motion": "🏏",
    "b_bright": "☀️",
    "b_size": "📏",
    "b_dialogue": "🗣",
    "b_angle": "↔️",
    "b_dark": "🎬",
    "b_apps": "📺",
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
    cat = s.home.category or "tv"
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
    prev = x.get("label_sku")
    prev_at = x.get("label_at")
    x["label_sku"] = sku
    x["label_at"] = ctx.now.isoformat()
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
        return compare(ctx, prev, sku)
    return await benefit(ctx, first=True)


async def benefit(ctx: SkillContext, first: bool = False) -> Reply:
    x = ctx.x
    sku = x.get("label_sku") or ctx.session.chosen_sku or ctx.session.option_labels.get("B")
    p = ctx.facts.product_sync(sku) if sku else None
    if p is None:
        return ctx.reply(info_reply(ctx, ctx.t("label_send")))
    said = x.setdefault("benefits_said", {}).setdefault(sku, [])
    ms = matching(ctx, p)
    nxt = next((b for b in ms if b.id not in said), None)
    if nxt is None:
        btns = [ctx.b("b_options", ctx.sid("options")), ctx.b("b_compare", ctx.sid("compare"))]
        return ctx.reply(info_reply(ctx, ctx.t("benefits_done"), btns))
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
    btns = [
        ctx.b("b_benefit_next", ctx.sid("benefit_next")),
        ctx.b("b_compare", ctx.sid("compare")),
        ctx.b("label_dikhao_btn", ctx.sid("options")),
    ]
    msgs = [coach_reply(ctx, say, why, [c for c in nxt.cites if P.known(ctx.session.home, c)] or nxt.cites, btns)]
    if first:
        msgs.append(await benefit_card(ctx, p, ms))
    return ctx.reply(*msgs)


async def benefit_card(ctx: SkillContext, p, ms: list) -> OutMessage:
    lang = ctx.clang
    benefits = [
        {"icon": ICONS.get(b.id, "✓"), "text": await fill(ctx, ctx.pick(b.card, lang), p, lang)} for b in ms[:3]
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


def compare(ctx: SkillContext, sku_a: str, sku_b: str) -> Reply:
    a, b = ctx.facts.product_sync(sku_a), ctx.facts.product_sync(sku_b)
    keys = sorted(set(a.specs) | set(b.specs))
    diffs = [k for k in keys if a.specs.get(k) != b.specs.get(k)]
    pack = ctx.pack
    rel = set()
    for bn in matching(ctx, a) + matching(ctx, b):
        rel |= _spec_keys(bn.when_spec)
        for c in bn.when_spec_any:
            rel |= _spec_keys(c)
    matter = [k for k in diffs if k in rel]
    names = ", ".join(ctx.pick(SPEC_LABELS.get(k, {"en": k.replace("_", " ")})) for k in matter[:3]) or "–"
    body = ctx.t(
        "compare_body",
        a=C.product_name(ctx, a),
        b=C.product_name(ctx, b),
        n=ctx.reg.num(len(diffs), "calc:compare_diffs"),
        k=ctx.reg.num(len(matter), "calc:compare_matter"),
        list=ctx.reg.static(names),
    )
    _ = pack
    btns = [ctx.b("b_options", ctx.sid("options")), ctx.b("b_benefit_next", ctx.sid("benefit_next"))]
    return ctx.reply(info_reply(ctx, body, btns))


async def compare_prompt(ctx: SkillContext) -> Reply:
    ctx.x["compare_pending"] = True
    return ctx.reply(info_reply(ctx, ctx.t("compare_send")))
