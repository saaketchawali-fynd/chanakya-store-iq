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


def _avail(ctx: SkillContext) -> list:
    return [p for p in ctx.facts.products_sync(ctx.pack.category) if p.qty > 0 or p.nearby_qty > 0]


def skipped(ctx: SkillContext) -> set[str]:
    """The home questions today's catalogue cannot act on (brain.data_questions), worked out once per customer and
    catalogue; kept on the session for the options gate, with how many data questions can stand in for them."""
    from chanakya.brain import data_questions as DQ

    s = ctx.session
    key = f"{ctx.pack.category}:{len(ctx.facts.snap.products)}"
    if s.extra.get("skip_key") != key:
        avail = _avail(ctx)
        skip = DQ.skip_slots(ctx.pack, avail, s.home, ctx.facts.snap.guides)
        if not ctx.pack.placeable and not any(e.category == ctx.pack.category for e in ctx.facts.snap.exchange):
            skip |= {"trigger", "old_size"}  # 'why now' only feeds the exchange value (no exchange data) and the try-on
        n_dq = 0
        if skip:
            done: set[str] = set()
            while (q := DQ.next_data_question(ctx.pack, avail, done, {})) is not None:
                done.add(q[0])
            n_dq = len(done)
        s.extra.update(skip_key=key, skip_slots=sorted(skip), dq_avail=n_dq)
    return set(s.extra.get("skip_slots") or [])


def pick_next_slot(ctx: SkillContext, exclude: set[str] | None = None, phase: str | None = None) -> str | None:
    """The next question in the pack's fixed order (why now, then the home questions); None once it is done.
    Questions today's catalogue cannot act on are skipped."""
    if not ctx.pack or not ctx.session:
        return None
    return ordered_next(ctx.session.home, ctx.pack, (exclude or set()) | skipped(ctx), phase)


def data_question(ctx: SkillContext, footer: str | None = None, force: bool = False) -> OutMessage | None:
    """In place of the questions the data cannot act on: one about what the models on sale really differ on,
    with the answers from the stock itself (and 'any'). `force`: asked from 'Tell me more' after the options."""
    from chanakya.brain import data_questions as DQ

    s = ctx.session
    if not force and (not skipped(ctx) or s.shortlist):
        return None
    done = set(s.extra.get("dq_done") or [])
    q = DQ.next_data_question(ctx.pack, _avail(ctx), done, s.extra.get("wants") or {})
    if q is None:
        return None
    key, vals = q
    s.extra.setdefault("dq_vals", {})[key] = [v for v, _ in vals]
    labels = [ctx.reg.raw(DQ.label(key, v), f"stock:{ctx.pack.category}:{key}") for v, _ in vals]
    avail = _avail(ctx)
    parts = []
    for lab, (v, n) in zip(labels, vals, strict=False):
        ps = [p for p in avail if DQ._value(p, key) is not None and (DQ._value(p, key) == v or str(DQ._value(p, key)) == str(v))]
        lo = min((p for p in ps), key=lambda p: p.price, default=None)
        span = f" {ctx.t('dq_from', price=ctx.reg.inr(lo.price, lo.price_source))}" if lo else ""
        parts.append(f"{ctx.reg.num(n, f'stock:{ctx.pack.category}:{key}')} {lab}{span}")
    counts = " · ".join(parts)
    what_c, what_a = ctx.t(f"dq_whatc_{key}", ctx.clang), ctx.t(f"dq_what_{key}")
    say = ctx.tc("dq_ask", what=what_c, a=labels[0], b=labels[1])
    why = ctx.t("dq_why", what=what_a, counts=counts, cat=ctx.t(f"brief_cat_{ctx.pack.category}"))
    from chanakya.brain import explain

    btns = [Button(id=ctx.sid("dq", key, i), title=truncate_title(lab)) for i, lab in enumerate(labels[:2])]
    if explain.can_explain(key, [v for v, _ in vals]):  # most customers don't know LED vs QLED, Google TV vs webOS
        btns.insert(0, ctx.b("b_dq_explain", ctx.sid("dqx", key)))  # 'any is fine' is one tap later, after the why
    else:
        btns.append(ctx.b("b_dq_any", ctx.sid("dq", key, "any")))
    ctx.x["questions_asked"] = questions_asked(ctx) + 1
    ctx.ev("data_question_asked", key=key, values=[str(v) for v in s.extra["dq_vals"][key]])
    return coach_reply(ctx, say, why, known_cites(ctx), btns, footer=footer, do="do_data_question")


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
    alt = sd.alt if sd.alt and by_highlights(ctx, slot_id) else None  # asked for what the highlights act on
    say = say_override or ctx.pick((alt or {}).get("ask") or sd.ask, ctx.clang)
    why = why_override or ctx.pick((alt or {}).get("why") or sd.why)
    if not why_override:
        tail = question_tail(ctx, slot_id)
        if tail:
            why = f"{tail} · {why.rstrip('.')}"  # the reason from today's stock first: the line is cut to fit
    if buttons is None and alt and alt.get("quick"):
        buttons = [Button(id=ctx.sid("slot", slot_id, v), title=truncate_title(sd.value_title(v, ctx.lang)))
                   for v in alt["quick"][:2]]
    btns = list(buttons) if buttons is not None else quick_buttons(ctx, slot_id)
    if buttons is None or len(btns) < 3:  # voice first: a voice button on every question (all answers one tap away)
        btns = btns[:2] + [ctx.b("b_voice_answer", ctx.sid("voiceq", slot_id))]
    ctx.x["questions_asked"] = questions_asked(ctx) + 1
    ctx.x["last_question"] = slot_id
    ctx.ev("question_asked", slot=slot_id)
    # voice first: every question says it can simply be answered by voice (any of the three languages)
    do = "do_setup" if slot_id in (pack.complete_slots + pack.site_needs) else "do_question"  # with the customer
    return coach_reply(ctx, say, why, (extra_cites or []) + [slot_id], btns, footer=ctx.t("footer_voice_q"), do=do)


BAND_SPEC = {"size_band": ("size_in", "inch"), "ton_band": ("ton", "ton")}


def by_highlights(ctx: SkillContext, slot_id: str) -> bool:
    """This question is asked only because the products' own highlights act on its answer (its spec rules cannot
    judge today's stock): e.g. 'what do you watch?' on live TVs, which carry no refresh rate."""
    from chanakya.brain import data_questions as DQ
    from chanakya.brain import highlights as HL

    pack = ctx.pack
    rules = [r for r in pack.fit_rules if r.slot == slot_id]
    if not rules or ctx.session is None:
        return False
    avail = [p for p in ctx.facts.products_sync(pack.category) if p.qty > 0 or p.nearby_qty > 0]
    g = ctx.facts.snap.guides
    if any(DQ.rule_informative(r, avail, ctx.session.home, pack, g) for r in rules):
        return False
    return HL.slot_informative(pack, slot_id, avail)


def question_tail(ctx: SkillContext, slot_id: str) -> str | None:
    """What this answer decides, in numbers from today's stock (the reason the question is worth asking):
    'Sunny room: 41 of 98 TVs in stock qualify' / 'In stock: 32–85 inch; the answer picks the right size'."""
    pack = ctx.pack
    rules = [r for r in pack.fit_rules if r.slot == slot_id and (r.kind in BAND_SPEC or r.spec_any)]
    if not rules or ctx.session is None:
        return None
    avail = [p for p in ctx.facts.products_sync(pack.category) if p.qty > 0 or p.nearby_qty > 0]
    if len(avail) < 3:
        return None
    cat = ctx.t(f"brief_cat_{pack.category}")
    from chanakya.brain import data_questions as DQ
    from chanakya.brain import highlights as HL

    g0 = ctx.facts.snap.guides
    if not any(DQ.rule_informative(r, avail, ctx.session.home, pack, g0) for r in rules):
        st = HL.slot_stats(pack, slot_id, avail)  # asked because the products' own highlights act on the answer
        if not st:
            return None
        th = HL.theme(pack.category, st[0])
        return ctx.t("kyun_tail_hl", feature=ctx.reg.static(th.label if th else st[0]),
                     n=ctx.reg.num(st[1], f"stock:{pack.category}:hl_{st[0]}"),
                     total=ctx.reg.num(len(avail), f"stock:{pack.category}:available"), cat=cat)
    r = max(rules, key=lambda r: r.weight)
    if r.kind in BAND_SPEC:
        key, unit = BAND_SPEC[r.kind]
        vals = sorted({float(p.spec(key)) for p in avail if p.spec(key)})
        if len(vals) < 2:
            return None
        src = f"stock:{pack.category}:{key}"
        return ctx.t("kyun_tail_band", lo=ctx.reg.num(vals[0], src), hi=ctx.reg.num(vals[-1], src, unit))
    g = ctx.facts.snap.guides
    n = sum(1 for p in avail if P.eval_spec_any(p, ctx.session.home, r.spec_any, pack, g))
    vals = (r.when or {}).get("in") or (r.when or {}).get("has_any") or []
    sd = pack.slot(slot_id)
    if vals and sd is not None:  # the answer that switches the rule on, in the associate's words
        need = sd.value_title(vals[0], ctx.lang)
    else:
        nd = pack.needs.get(r.id)
        need = ctx.pick(nd, ctx.lang) if nd else None
    if not need or "{" in need or n == len(avail):
        return None  # every model meets it: the answer ranks, it does not narrow
    return ctx.t("kyun_tail_rule", need=need, n=ctx.reg.num(n, f"stock:{pack.category}:{r.id}"),
                 total=ctx.reg.num(len(avail), f"stock:{pack.category}:available"), cat=cat)


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
    rows.append(ListRow(id=ctx.sid("returning"), title=truncate(ctx.t("row_returning"), 24),
                        description=truncate(ctx.t("row_returning_d"), 72)))  # came back: carry on, no re-asking
    for r in rows:
        ctx.reg.static(r.title + " " + (r.description or ""))
    code = ctx.reg.raw(ctx.session.code, "session:code")
    from chanakya.core.coach import bolo_block, do_block, fit_line, kyun_block

    body = "\n".join([bolo_block(ctx, ctx.tc("bolo_opener")), kyun_block(ctx, ctx.t("kyun_opener")), do_block(ctx, "do_opener")])
    footer = fit_line([ctx.t("st_new", code=code)], ctx.t("footer_voice_short"), 60)  # 'Bolkar bhi · C1 · Naya customer'
    return OutMessage(to=ctx.actor_wa_id, kind="list", body=body, list_button=truncate_title(ctx.t("list_product")),
                      sections=[ListSection(rows=rows)], footer=footer, allowed_numbers=ctx.reg.allowed())


def photo_question(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    """One question, two answers only: does the customer have a photo of the room?"""
    ctx.x["photo_asked"] = True
    cites = known_cites(ctx, ["seat_distance", "room_size", "space", "space_width"]) or []
    cat = ctx.pack.category if ctx.pack else "tv"
    key = f"bolo_photo_ask_{cat}" if i18n.has(f"bolo_photo_ask_{cat}") else "bolo_photo_ask"
    btns = [ctx.b("b_photo_yes", ctx.sid("photo_yes")), ctx.b("b_photo_no", ctx.sid("photo_no"))]
    if not cites:
        from chanakya.core.coach import bolo_block, do_block

        return info_reply(ctx, bolo_block(ctx, ctx.tc(key)) + "\n" + do_block(ctx, "do_photo_ask"), btns)
    return coach_reply(ctx, ctx.tc(key), ctx.t("kyun_photo_ask"), cites, btns, footer=footer, do="do_photo_ask")


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
    msg = coach_reply(ctx, ctx.tc("bolo_range"), ctx.t("kyun_range"), known_cites(ctx), [], footer=footer, do="do_range")
    msg.kind = "list"
    msg.list_button = truncate_title(ctx.t("list_range"))
    msg.sections = [ListSection(rows=rows)]
    msg.allowed_numbers = ctx.reg.allowed()
    return msg


READBACK_MAX = 3  # said aloud: the three facts that decide the product (the Home Card shows them all)


def asked_for(ctx: SkillContext, lang: str | None = None) -> list[tuple[str, str]]:
    """What the customer asked for beyond the fixed questions: the data questions' answers (including 'any is
    fine') and explicit asks from a note ('55 inch Sony'), as (label, value) in the order they were said."""
    from chanakya.brain import data_questions as DQ
    from chanakya.truth import fmt

    s = ctx.session
    lang = lang or ctx.lang
    wants = s.extra.get("wants") or {}
    out = []
    for key, v in wants.items():
        if key == "size_in":
            val = f'{fmt.num(v)}"'
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and key in ("ton", "capacity_kg", "capacity_l", "ram_gb"):
            val = fmt.num(v) + {"ton": " ton", "capacity_kg": " kg", "capacity_l": " L", "ram_gb": " GB RAM"}[key]
        else:
            val = DQ.label(key, v)
        out.append((ctx.t(f"want_label_{key}", lang), ctx.reg.raw(val, f"note:want:{key}")))
    for key in s.extra.get("dq_done") or []:
        if key not in wants:  # answered 'any is fine': that is an answer too
            out.append((ctx.t(f"want_label_{key}", lang), ctx.t("b_dq_any", lang)))
    for label, _ in out:
        ctx.reg.static(label)
    return out


def readback_facts(ctx: SkillContext, lang: str) -> str:
    pack = ctx.pack
    home = ctx.session.home
    order = [x for x in pack.required_slots if x != "trigger"] + [x for x in pack.order if x not in pack.required_slots
                                                                  and x != "trigger"]
    parts = []
    for sid in order:
        sd = pack.slot(sid)
        vals = P.values(home, sid)
        if not sd or not vals:
            continue
        sv = home.slots[sid][-1]
        unit = {"seat_distance_ft": "ft", "room_size_sqft": "sq ft", "hours_per_day": "h", "fridge_width_cm": "cm",
                "old_tv_in": "inch"}.get(sd.number or "", "")
        if sv.number is not None and sv.source in ("voice", "text") and unit:  # a measurement, with its unit
            parts.append(ctx.reg.num(sv.number, f"card:{sid}", unit))
        else:
            parts.append(ctx.reg.static(value_title(sd, vals[0], lang)))
        if len(parts) >= READBACK_MAX:
            break
    for _, val in asked_for(ctx, lang):  # and what they asked for about the product itself
        if len(parts) >= READBACK_MAX + 1:
            break
        parts.append(val)
    return ", ".join(parts)


def readback_nudge(ctx: SkillContext) -> OutMessage:
    """Something unclear arrived while the read-back is waiting: a one-line nudge, not the two cards again."""
    btns = [ctx.b("b_readback_ok", ctx.sid("readback_ok")), ctx.b("b_readback_edit", ctx.sid("readback_edit"))]
    return info_reply(ctx, ctx.t("readback_nudge"), btns)


def readback(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    """Say the home back to the customer, with the Home Card: 'So, for your home: … Did I get that right?'"""
    ctx.x["readback_shown"] = True
    ctx.ev("home_card_sent", known=ctx.pack.known_required(ctx.session.home), total=len(ctx.pack.required_slots))
    key = f"bolo_readback_{ctx.pack.category}"
    say = ctx.tc(key if i18n.has(key) else "bolo_readback", facts=readback_facts(ctx, ctx.clang))
    btns = [ctx.b("b_readback_ok", ctx.sid("readback_ok")), ctx.b("b_readback_edit", ctx.sid("readback_edit"))]
    cites = [s for s in ctx.pack.required_slots if P.known(ctx.session.home, s)] or known_cites(ctx)
    return coach_reply(ctx, say, ctx.t("kyun_readback"), cites, btns, card=english(ctx, lambda: home_card(ctx)),
                       footer=footer, do="do_readback")


def doubts(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    ctx.x["doubts_asked"] = True
    btns = [ctx.b("b_no_doubt", ctx.sid("options")), ctx.b("b_doubt", ctx.sid("doubt")),
            ctx.b("b_price_q", ctx.sid("pricefirst_q"))]
    return coach_reply(ctx, ctx.tc("bolo_doubts"), ctx.t("kyun_doubts"), known_cites(ctx), btns, footer=footer, do="do_doubts")


def options_ready(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    cites = known_cites(ctx)
    btns = [ctx.b("b_options", ctx.sid("options")), ctx.b("b_status", encode("g", "status"))]
    if not cites:
        return info_reply(ctx, ctx.t("fallback_body"), btns)
    return coach_reply(ctx, ctx.tc("bolo_options_ready"), ctx.t("kyun_options_ready"), cites, btns, footer=footer,
                       do="do_options_ready")


def resume(ctx: SkillContext) -> OutMessage:
    """Asked for 'next' after the options: the sensible next steps for where the customer is."""
    from chanakya.core.contracts import Step

    s = ctx.session
    if s.extra.get("delivery_choice") or s.extra.get("outcome") == "sold" or s.consent:  # after the sale
        btns = [ctx.b("b_send_customer", ctx.sid("parivaar")), ctx.b("b_task", ctx.sid("task")),
                ctx.b("b_khatam", ctx.sid("khatam"))]
    elif s.step in (Step.CLOSE, Step.FOLLOW):
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
        dq = data_question(ctx, footer=footer)
        if dq is not None:
            return dq
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
        txt = ctx.reg.static(", ".join(value_title(sd, v, ctx.lang) for v in vals))
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
    for label, val in asked_for(ctx):  # the data questions and the note's asks: everything they told us
        rows.append({"_id": label, "label": label, "value": val})
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
    skip = skipped(ctx)  # questions today's catalogue cannot act on are not 'still to ask'
    missing = [ctx.pick(pack.slot(s).title) for s in pack.missing_required(home) if pack.slot(s) and s not in skip]
    name = home.customer_name or ctx.reg.raw(ctx.session.code, "session:code")
    known, total = state.required_progress(ctx.session, pack)
    return Card(
        template="home",
        lang=ctx.lang,
        data={  # type: ignore[arg-type]
            "name": name,
            "category_label": ctx.pick(pack.label),
            "rows": rows,
            "start_level_text": start,
            "known": known,
            "total": total,
            "missing": missing,
            "known_str": ctx.reg.num(known, "card:known"),
            "total_str": ctx.reg.num(total, "pack:required"),
        },
    )


def home_card_message(ctx: SkillContext, force: bool = False) -> OutMessage | None:
    """Home Card image when ≥ 3 required slots are known and the card changed since the last image."""
    pack = ctx.pack
    s = ctx.session
    known, total = state.required_progress(s, pack)  # data-question answers count, skipped questions do not
    if not force and (known < min(3, total) or ctx.x.get("last_card_known") == known):
        return None
    first = not ctx.x.get("home_card_sent")
    ctx.x["last_card_known"] = known
    ctx.x["home_card_sent"] = True
    ctx.ev("home_card_sent", known=known, total=total)
    k = ctx.reg.num(known, "card:known")
    tt = ctx.reg.num(total, "pack:required")
    if first and not s.tryon_done:
        body = ctx.t("room_photo_ask")
        btns = [ctx.b("b_photo", ctx.sid("photo"))]
        if state.options_gate_open(s, pack) and not s.shortlist:
            btns.append(ctx.b("b_options", ctx.sid("options")))
        return info_reply(ctx, body, btns, card=english(ctx, lambda: home_card(ctx)))
    body = ctx.t("home_card_caption", known=k, total=tt)
    btns = [ctx.b("b_options", ctx.sid("options"))] if state.options_gate_open(s, pack) and not s.shortlist else None
    return info_reply(ctx, body, btns, card=english(ctx, lambda: home_card(ctx)))


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


def stock_line(ctx: SkillContext, p, lang: str | None = None, customer: bool = False) -> str:
    """'3 in store' / 'Delivery in 2 days (from Belapur)'; for Jio products, only 'Available' (the API gives no
    counts). `customer` → the customer-facing wording used on cards."""
    sfx = "_c" if customer else ""
    if not p.stock_known:
        return ctx.t(("stock_available" if p.in_stock else "stock_unavailable") + sfx, lang)
    if p.qty > 0:
        return ctx.t("stock_in_store" + sfx, lang, qty=ctx.reg.num(p.qty, f"stock:{p.sku}@{p.store_id}"))
    store = ctx.facts.store_name(p.nearby_store_id).replace("Reliance Digital ", "").replace(" (TEST)", "")
    return ctx.t("stock_nearby" + sfx, lang,
                 days=ctx.reg.num(p.delivery_days, f"stock:{p.sku}@{p.store_id}:delivery_days"),
                 store=ctx.reg.raw(store, f"store:{p.nearby_store_id}"))


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


# ---------------------------------------------------------------- visuals: ONE card, in English (user rule, 2 Nov 2026)
def english(ctx: SkillContext, build):
    """The one visual for customer and associate, built in English. `build()` reads ctx.lang / ctx.clang."""
    with ctx.as_lang("en"):
        return build()


async def english_async(ctx: SkillContext, build):
    with ctx.as_lang("en"):
        return await build()


def value_title(sd, value: str, lang: str) -> str:
    """A slot value in this language. Hindi is Roman Hinglish everywhere the associate reads it, the lines they say
    to a Hindi-speaking customer included (user, 5 Nov 2026)."""
    return sd.value_title(value, lang)
