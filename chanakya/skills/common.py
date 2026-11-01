"""Helpers shared by the step skills: next question, Home Card, budget list, cites, product labels."""

from __future__ import annotations

from chanakya.brain import packs as P
from chanakya.brain.next_question import next_slot, ordered_next
from chanakya.channel.limits import truncate, truncate_title
from chanakya.core import state
from chanakya.core.buttons import encode
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Button, Card, ListRow, ListSection, OutMessage
from chanakya.i18n import lang as i18n
from chanakya.skills.base import SkillContext


def questions_asked(ctx: SkillContext) -> int:
    return int(ctx.x.get("questions_asked", 0))


def quick_buttons(ctx: SkillContext, slot_id: str, limit: int = 3) -> list[Button]:
    pack = ctx.pack
    sd = pack.slot(slot_id) if pack else None
    if not sd:
        return []
    out = []
    for q in sd.quick[:limit]:
        title = ctx.pick(q.title)
        out.append(Button(id=ctx.sid("slot", slot_id, q.id), title=truncate_title(title)))
    return out


def known_cites(ctx: SkillContext, prefer: list[str] | None = None) -> list[str]:
    home = ctx.session.home
    for s in prefer or []:
        if P.known(home, s):
            return [s]
    pack = ctx.pack
    for s in pack.required_slots if pack else []:
        if P.known(home, s):
            return [s]
    for s, v in home.slots.items():
        if v:
            return [s]
    return []


def pick_next_slot(ctx: SkillContext, exclude: set[str] | None = None, phase: str | None = None) -> str | None:
    """The next question in the pack's fixed order (why now, then the home questions); None once it is done."""
    if not ctx.pack or not ctx.session:
        return None
    return ordered_next(ctx.session.home, ctx.pack, exclude, phase)


def info_gain_slot(ctx: SkillContext, exclude: set[str] | None = None) -> str | None:
    """Spec 10.6 next-best question (the unknown slot that most changes the top 3), for extra questions only."""
    pack = ctx.pack
    if not pack:
        return None
    products = ctx.facts.products_sync(pack.category, ctx.staff.store_id if ctx.staff else None)
    return next_slot(ctx.session.home, pack, products, ctx.facts.snap.guides, questions_asked(ctx),
                     options_shown=bool(ctx.session.shortlist), exclude=exclude)


def question_reply(
    ctx: SkillContext,
    slot_id: str,
    buttons: list[Button] | None = None,
    say_override: str | None = None,
    why_override: str | None = None,
    extra_cites: list[str] | None = None,
) -> OutMessage:
    pack = ctx.pack
    sd = pack.slot(slot_id)
    ctx.asking.add(slot_id)
    say = say_override or ctx.pick(sd.ask, ctx.clang)
    why = why_override or ctx.pick(sd.why)
    btns = buttons if buttons is not None else quick_buttons(ctx, slot_id)
    ctx.x["questions_asked"] = questions_asked(ctx) + 1
    ctx.x["last_question"] = slot_id
    ctx.ev("question_asked", slot=slot_id)
    return coach_reply(ctx, say, why, (extra_cites or []) + [slot_id], btns)


def category_message(ctx: SkillContext) -> OutMessage:
    """Opener: the welcome line to say, and the product as one list (the associate's department first)."""
    from chanakya.channel.limits import truncate

    cats = [c for c in ("tv", "ac", "wm", "fridge", "laptop") if ctx.packs.get(c)]
    dept = ctx.staff.dept if ctx.staff else None
    if dept in cats:
        cats.remove(dept)
        cats.insert(0, dept)
    rows = [ListRow(id=ctx.sid("cat", c), title=truncate(ctx.t(f"cat_{c}"), 24), description=truncate(ctx.t(f"cat_{c}_d"), 72))
            for c in cats]
    for r in rows:
        ctx.reg.static(r.title + " " + (r.description or ""))
    code = ctx.reg.raw(ctx.session.code, "session:code")
    body = (f'{ctx.t("label_bolo")}: "{ctx.tc("bolo_opener")}"\n{ctx.t("label_kyun")}: {ctx.t("kyun_opener")}\n'
            + ctx.t("st_new", code=code))
    return OutMessage(to=ctx.actor_wa_id, kind="list", body=body, list_button=truncate_title(ctx.t("list_product")),
                      sections=[ListSection(rows=rows)], footer=ctx.t("footer_voice"), allowed_numbers=ctx.reg.allowed())


def photo_question(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    """One question, two answers only: does the customer have a photo of the room?"""
    ctx.x["photo_asked"] = True
    cites = known_cites(ctx, ["seat_distance", "room_size", "space", "space_width"]) or []
    cat = ctx.pack.category if ctx.pack else "tv"
    key = f"bolo_photo_ask_{cat}" if i18n.has(f"bolo_photo_ask_{cat}") else "bolo_photo_ask"
    btns = [ctx.b("b_photo_yes", ctx.sid("photo_yes")), ctx.b("b_photo_no", ctx.sid("photo_no"))]
    if not cites:
        return info_reply(ctx, ctx.tc(key), btns)
    return coach_reply(ctx, ctx.tc(key), ctx.t("kyun_photo_ask"), cites, btns, footer=footer)


def _fits(ctx: SkillContext) -> list:
    """Products in stock (or nearby) that suit this home: not excluded, inside the size band when it is known."""
    from chanakya.brain.fit import fit, in_band

    pack = ctx.pack
    home = ctx.session.home
    g = ctx.facts.snap.guides
    avail = [p for p in ctx.facts.products_sync(pack.category) if p.qty > 0 or p.nearby_qty > 0]
    ok = []
    for p in avail:
        if fit(p, home, pack, g).excluded:
            continue
        b = in_band(p, home, pack, g)
        if b is not None and b < 0.5:
            continue
        ok.append(p)
    return ok if len(ok) >= 3 else avail


def _nice(n: float) -> int:
    step = 5000 if n >= 60000 else 1000 if n >= 10000 else 500
    return int(round(n / step) * step)


def range_bands(ctx: SkillContext) -> list[dict]:
    """Three comfort ranges cut from the real prices of the products that fit this home (terciles, rounded)."""
    prices = sorted(p.price for p in _fits(ctx))
    if len(prices) < 3:
        return []
    c1 = _nice(prices[len(prices) // 3])
    c2 = _nice(prices[(2 * len(prices)) // 3])
    if c2 <= c1:
        c2 = _nice(c1 * 1.3)
    lo, hi = prices[0], prices[-1]
    return [
        {"id": "r1", "a": c1, "b": None, "level": (lo + c1) / 2, "n": sum(1 for x in prices if x <= c1)},
        {"id": "r2", "a": c1, "b": c2, "level": (c1 + c2) / 2, "n": sum(1 for x in prices if c1 < x <= c2)},
        {"id": "r3", "a": c2, "b": None, "level": (c2 + hi) / 2, "n": sum(1 for x in prices if x > c2)},
    ]


def range_title(ctx: SkillContext, band: dict, lang: str | None = None) -> str:
    a = ctx.reg.inr(band["a"], "calc:range_band")
    if band["id"] == "r1":
        return ctx.t("range_upto", lang, a=a)
    if band["id"] == "r3":
        return ctx.t("range_above", lang, a=a)
    return ctx.t("range_between", lang, a=a, b=ctx.reg.inr(band["b"], "calc:range_band"))


def comfort_range(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    """'So I show you the right ones: roughly what range?' — three ranges from the database, plus 'not decided'."""
    from chanakya.channel.limits import truncate

    ctx.x["budget_asked"] = True
    bands = range_bands(ctx)
    if not bands:
        return budget_list(ctx)
    ctx.x["range_bands"] = bands
    rows = [ListRow(id=ctx.sid("budget", b["id"]), title=truncate(range_title(ctx, b), 24),
                    description=truncate(ctx.t("range_d", n=ctx.reg.num(b["n"], "calc:range_count")), 72)) for b in bands]
    rows.append(ListRow(id=ctx.sid("budget", "none"), title=ctx.t("range_none"), description=ctx.t("range_none_d")))
    ctx.reg.static(" ".join(r.title + " " + (r.description or "") for r in rows[-1:]))
    msg = coach_reply(ctx, ctx.tc("bolo_range"), ctx.t("kyun_range"), known_cites(ctx), [], footer=footer)
    msg.kind = "list"
    msg.list_button = truncate_title(ctx.t("list_range"))
    msg.sections = [ListSection(rows=rows)]
    msg.allowed_numbers = ctx.reg.allowed()
    return msg


def readback_facts(ctx: SkillContext, lang: str) -> str:
    pack = ctx.pack
    home = ctx.session.home
    parts = []
    for sid in [x for x in pack.order if x != "trigger"]:
        sd = pack.slot(sid)
        vals = P.values(home, sid)
        if not sd or not vals:
            continue
        sv = home.slots[sid][-1]
        if sv.number is not None and sv.source in ("voice", "text") and sd.number:
            unit = {"seat_distance_ft": "ft", "room_size_sqft": "sq ft", "hours_per_day": "h", "fridge_width_cm": "cm",
                    "old_tv_in": "inch"}.get(sd.number, "")
            parts.append(ctx.reg.num(sv.number, f"card:{sid}", unit))
        else:
            parts.append(ctx.reg.static(", ".join(sd.value_title(v, lang) for v in vals)))
    return ", ".join(parts)


def readback(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    """Say the home back to the customer, with the Home Card: 'So, for your home: … Did I get that right?'"""
    ctx.ev("home_card_sent", known=ctx.pack.known_required(ctx.session.home), total=len(ctx.pack.required_slots))
    key = f"bolo_readback_{ctx.pack.category}"
    say = ctx.tc(key if i18n.has(key) else "bolo_readback", facts=readback_facts(ctx, ctx.clang))
    btns = [ctx.b("b_readback_ok", ctx.sid("readback_ok")), ctx.b("b_readback_edit", ctx.sid("readback_edit"))]
    cites = [s for s in ctx.pack.required_slots if P.known(ctx.session.home, s)] or known_cites(ctx)
    return coach_reply(ctx, say, ctx.t("kyun_readback"), cites, btns, card=home_card(ctx), footer=footer)


def doubts(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    ctx.x["doubts_asked"] = True
    btns = [ctx.b("b_no_doubt", ctx.sid("options")), ctx.b("b_doubt", ctx.sid("doubt")),
            ctx.b("b_price_q", ctx.sid("pricefirst_q"))]
    return coach_reply(ctx, ctx.tc("bolo_doubts"), ctx.t("kyun_doubts"), known_cites(ctx), btns, footer=footer)


def options_ready(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    cites = known_cites(ctx)
    btns = [ctx.b("b_options", ctx.sid("options")), ctx.b("b_status", encode("g", "status"))]
    if not cites:
        return info_reply(ctx, ctx.t("fallback_body"), btns)
    return coach_reply(ctx, ctx.tc("bolo_options_ready"), ctx.t("kyun_options_ready"), cites, btns, footer=footer)


def resume(ctx: SkillContext) -> OutMessage:
    """Asked for 'next' after the options: the sensible next steps for where the customer is."""
    from chanakya.core.contracts import Step

    s = ctx.session
    if s.step in (Step.CLOSE, Step.FOLLOW):
        btns = [ctx.b("b_book_delivery", ctx.sid("delivery")), ctx.b("b_not_today", ctx.sid("nottoday")),
                ctx.b("b_status", encode("g", "status"))]
    elif s.demo_done or s.chosen_sku:
        btns = [ctx.b("b_cost_show", ctx.sid("cost_show")), ctx.b("b_offers", ctx.sid("offers")),
                ctx.b("b_status", encode("g", "status"))]
    else:
        btns = [ctx.b("b_options", ctx.sid("options")), ctx.b("b_demo_next", ctx.sid("demo")),
                ctx.b("b_status", encode("g", "status"))]
    return info_reply(ctx, ctx.t("resume_body"), btns)


def next_question_or_options(ctx: SkillContext, lead: str | None = None, footer: str | None = None) -> OutMessage:
    """Exactly ONE next step. Assess runs in a fixed order: product → why now → the 4 home questions; then (once)
    room photo → comfort range → read it back → any doubts → options. Voice notes fill several at once; whatever is
    already known is skipped."""
    pack = ctx.pack
    s = ctx.session
    if s.home.category is None or ctx.packs.get(s.home.category) is None:
        return category_message(ctx)
    slot = pick_next_slot(ctx)
    if slot:
        msg = question_reply(ctx, slot, say_override=lead)
        if footer:
            msg.footer = footer
        return msg
    if not s.shortlist:
        x = ctx.x
        if pack.placeable and not x.get("photo_asked") and not s.tryon_done:
            return photo_question(ctx, footer=footer)
        if s.home.start_level_inr is None and not x.get("budget_asked"):
            return comfort_range(ctx, footer=footer)
        if not x.get("readback_done"):
            return readback(ctx, footer=footer)
        if not x.get("doubts_asked"):
            return doubts(ctx, footer=footer)
        return options_ready(ctx, footer=footer)
    return resume(ctx)


def home_card(ctx: SkillContext) -> Card:
    pack = ctx.pack
    home = ctx.session.home
    rows = []
    for sid in pack.order + pack.complete_slots + pack.cost_slots + pack.site_needs:
        if sid in [r.get("_id") for r in rows]:
            continue
        sd = pack.slot(sid)
        vals = P.values(home, sid)
        if not sd or not vals:
            continue
        txt = ctx.reg.static(", ".join(sd.value_title(v, ctx.lang) for v in vals))
        n = P.number(home, sid, pack)
        sv = home.slots[sid][-1]
        if sv.number is not None and sv.source in ("voice", "text") and sd.number:
            unit = {
                "seat_distance_ft": "ft",
                "room_size_sqft": "sq ft",
                "pipe_run_ft": "ft",
                "family_size": "",
                "hours_per_day": "h",
                "niche_width_cm": "cm",
                "fridge_width_cm": "cm",
                "old_tv_in": "inch",
            }.get(sd.number, "")
            txt = ctx.reg.num(n, f"card:{sid}", unit)
        rows.append({"_id": sid, "label": ctx.pick(sd.title) or sid, "value": txt})
        ctx.reg.static(rows[-1]["label"])
    if home.sports_viewer:
        who = (ctx.packs.common.get("who_titles") or {}).get(home.sports_viewer)
        if who:
            rows.append({"_id": "who", "label": ctx.t("home_row_sports"), "value": ctx.pick(who)})
    for r in rows:
        r.pop("_id", None)
    band = next((b for b in ctx.x.get("range_bands") or [] if b["id"] == ctx.x.get("range_id")), None)
    if band:
        start = ctx.t("start_level_band", band=range_title(ctx, band))
    elif home.start_level_inr:
        src = ctx.t("start_level_by_you")
        start = f"{ctx.reg.inr(home.start_level_inr, 'card:start_level')} ({src})"
    else:
        start = ctx.t("start_level_not_told")
    missing = [ctx.pick(pack.slot(s).title) for s in pack.missing_required(home) if pack.slot(s)]
    name = home.customer_name or ctx.reg.raw(ctx.session.code, "session:code")
    return Card(
        template="home",
        lang=ctx.lang,
        data={  # type: ignore[arg-type]
            "name": name,
            "category_label": ctx.pick(pack.label),
            "rows": rows,
            "start_level_text": start,
            "known": pack.known_required(home),
            "total": len(pack.required_slots),
            "missing": missing,
            "known_str": ctx.reg.num(pack.known_required(home), "card:known"),
            "total_str": ctx.reg.num(len(pack.required_slots), "pack:required"),
        },
    )


def home_card_message(ctx: SkillContext, force: bool = False) -> OutMessage | None:
    """Home Card image when ≥ 3 required slots are known and the card changed since the last image."""
    pack = ctx.pack
    s = ctx.session
    known = pack.known_required(s.home)
    if not force and (known < 3 or ctx.x.get("last_card_known") == known):
        return None
    first = not ctx.x.get("home_card_sent")
    ctx.x["last_card_known"] = known
    ctx.x["home_card_sent"] = True
    ctx.ev("home_card_sent", known=known, total=len(pack.required_slots))
    k = ctx.reg.num(known, "card:known")
    tt = ctx.reg.num(len(pack.required_slots), "pack:required")
    if first and not s.tryon_done:
        body = ctx.t("room_photo_ask")
        btns = [ctx.b("b_photo", ctx.sid("photo"))]
        if state.options_gate_open(s, pack) and not s.shortlist:
            btns.append(ctx.b("b_options", ctx.sid("options")))
        return info_reply(ctx, body, btns, card=home_card(ctx))
    body = ctx.t("home_card_caption", known=k, total=tt)
    btns = [ctx.b("b_options", ctx.sid("options"))] if state.options_gate_open(s, pack) and not s.shortlist else None
    return info_reply(ctx, body, btns, card=home_card(ctx))


def budget_list(ctx: SkillContext, body: str | None = None) -> OutMessage:
    pack = ctx.pack
    rows = [ListRow(id=ctx.sid("budget", b.id), title=truncate(ctx.pick(b.title), 24)) for b in pack.budget_bands[:10]]
    return OutMessage(
        to=ctx.actor_wa_id,
        kind="list",
        body=body or ctx.t("budget_ask"),
        list_button=truncate_title(ctx.t("list_budget")),
        sections=[ListSection(rows=rows)],
        allowed_numbers=ctx.reg.allowed(),
    )


def product_title(ctx: SkillContext, p, lang: str | None = None) -> str:
    return ctx.reg.raw(ctx.pick(p.title, lang or ctx.clang) or p.model, f"product:{p.sku}")


def product_name(ctx: SkillContext, p) -> str:
    return ctx.reg.raw(p.model, f"product:{p.sku}")


def option_button(ctx: SkillContext, label: str, p) -> Button:
    """'B: Meru 55 QLED Pro' when it fits in 20 characters, else 'B: Meru 55"', else 'Option B' — never cut off."""
    size = p.spec("size_in")
    for title in (f"{label}: {p.model}", f'{label}: {p.brand} {int(size)}"' if size else "", f"{label}: {p.brand}"):
        if title and len(title) <= 20:
            ctx.reg.raw(title, f"product:{p.sku}")
            return Button(id=ctx.sid("opt", label), title=title)
    return Button(id=ctx.sid("opt", label), title=f"Option {label}")


def who_title(ctx: SkillContext, lang: str) -> str | None:
    w = ctx.session.home.sports_viewer
    if not w:
        return None
    d = (ctx.packs.common.get("who_titles") or {}).get(w)
    return ctx.pick(d, lang) if d else None
