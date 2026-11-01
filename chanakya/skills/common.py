"""Helpers shared by the step skills: next question, Home Card, budget list, cites, product labels."""

from __future__ import annotations

from chanakya.brain import packs as P
from chanakya.brain.next_question import next_slot
from chanakya.channel.limits import truncate, truncate_title
from chanakya.core import state
from chanakya.core.buttons import encode
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Button, Card, ListRow, ListSection, OutMessage
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
    pack = ctx.pack
    if not pack:
        return None
    products = ctx.facts.products_sync(pack.category, ctx.staff.store_id if ctx.staff else None)
    guides = ctx.facts.snap.guides
    return next_slot(
        ctx.session.home,
        pack,
        products,
        guides,
        questions_asked(ctx),
        options_shown=bool(ctx.session.shortlist),
        exclude=exclude,
        phase=phase,
    )


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


def photo_question(ctx: SkillContext, footer: str | None = None) -> OutMessage:
    """One question, two answers only: does the customer have a photo of the room?"""
    ctx.x["photo_asked"] = True
    cites = known_cites(ctx, ["seat_distance", "room_size", "space"]) or []
    btns = [ctx.b("b_photo_yes", ctx.sid("photo_yes")), ctx.b("b_photo_no", ctx.sid("photo_no"))]
    if not cites:
        return info_reply(ctx, ctx.tc("bolo_photo_ask"), btns)
    return coach_reply(ctx, ctx.tc("bolo_photo_ask"), ctx.t("kyun_photo_ask"), cites, btns, footer=footer)


def next_question_or_options(ctx: SkillContext, lead: str | None = None, footer: str | None = None) -> OutMessage:
    """Exactly ONE next step, chosen from what is known: the next home question; once the options gate opens,
    the budget (if the customer has not said it), then 'room photo?' (once), then questions with Options first."""
    pack = ctx.pack
    s = ctx.session
    gate_open = state.options_gate_open(s, pack)
    if gate_open and not s.shortlist and lead is None:
        if s.home.start_level_inr is None and not ctx.x.get("budget_asked"):
            ctx.x["budget_asked"] = True
            return budget_list(ctx)
        if not ctx.x.get("photo_asked") and not s.tryon_done and pack.category in ("tv", "ac", "wm"):
            return photo_question(ctx, footer=footer)
    slot = pick_next_slot(ctx)
    if slot is None:
        cites = known_cites(ctx)
        if not cites:
            return info_reply(
                ctx,
                ctx.t("fallback_body"),
                [ctx.b("b_options", ctx.sid("options")), ctx.b("b_status", encode("g", "status"))],
            )
        btns = [ctx.b("b_options", ctx.sid("options")), ctx.b("b_status", encode("g", "status"))]
        return coach_reply(ctx, ctx.tc("bolo_options_ready"), ctx.t("kyun_options_ready"), cites, btns, footer=footer)
    btns = quick_buttons(ctx, slot)
    if gate_open and not s.shortlist:
        btns = [ctx.b("b_options", ctx.sid("options"))] + btns[:2]
    msg = question_reply(ctx, slot, btns, say_override=lead)
    if footer:
        msg.footer = footer
    if gate_open and not s.shortlist and not ctx.x.get("gate_card_sent"):
        # the Home Card rides on this same message when the options open (no separate message)
        ctx.x["gate_card_sent"] = True
        msg.card = home_card(ctx)
        ctx.ev("home_card_sent", known=pack.known_required(s.home), total=len(pack.required_slots))
    return msg


def home_card(ctx: SkillContext) -> Card:
    pack = ctx.pack
    home = ctx.session.home
    rows = []
    for sid in pack.required_slots + [x for x in pack.cost_slots if x not in pack.required_slots] + pack.site_needs:
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
    if home.start_level_inr:
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
