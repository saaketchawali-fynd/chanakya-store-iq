"""Step 1 — Taiyari (Before the shift), one step at a time (user flow, 2 Nov 2026):

1. the pre-read: today's stock, the live offers and what to keep in mind, strictly from the data;
2. today's clip (the associate's weakest step);
3. "I'm ready";
4. then, and only then, the customer button.

The pre-read card is in English (one card, like every visual); the messages are in the associate's language."""

from __future__ import annotations

import re

from chanakya.channel.limits import truncate_title
from chanakya.config import ROOT
from chanakya.core import repo
from chanakya.core.buttons import encode
from chanakya.core.clock import now, today
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Button, Card, HomeCard, OutMessage, Reply
from chanakya.skills.base import SkillContext
from chanakya.skills.review import STEPS
from chanakya.truth import fmt

CLIPS = ROOT / "data" / "clips"
_EMPTY_HOME = HomeCard()
SOON_DAYS = 3  # an offer ending within this many days is called out in "keep in mind"
BADGE_DAYS = 7  # … and marked "ends soon" on its row


async def focus_step(ctx: SkillContext) -> tuple[str, int | None]:
    rows = await repo.scores_for(ctx.actor_wa_id, limit=5)
    if not rows:
        return "translate", None
    avg = {st: sum((r.steps or {}).get(st, 0) for r in rows) / len(rows) for st in STEPS}
    weakest = min(STEPS, key=lambda st: (avg[st], STEPS.index(st)))
    return weakest, (rows[0].steps or {}).get(weakest)


def _dept(ctx: SkillContext) -> str:
    st = ctx.staff
    return st.dept if st and st.dept in ctx.packs.packs else "tv"


# ---------------------------------------------------------------- stock (from the catalogue rows only)
def _bucket(cat: str, p) -> tuple[float, str] | None:
    """The key spec a customer shops by: TV size, AC tonnage, washer kg, fridge litres, laptop RAM."""
    if cat == "tv":
        v = p.spec("size_in")
        return (float(v), f'{fmt.num(v)}"') if v else None
    if cat == "ac":
        v = p.spec("ton")
        return (float(v), f"{fmt.num(v)} ton") if v else None
    if cat == "wm":
        v = p.spec("capacity_kg")
        return (float(v), f"{fmt.num(v)} kg") if v else None
    if cat == "fridge":
        v = p.spec("capacity_l")
        if not v:
            return None
        for hi, label in ((250, "Under 250 L"), (400, "250–399 L"), (600, "400–599 L")):
            if v < hi:
                return float(hi), label
        return 9999.0, "600 L+"
    if cat == "laptop":
        v = p.spec("ram_gb")
        return (float(v), f"{fmt.num(v)} GB RAM") if v else None
    return None


def stock_section(ctx: SkillContext, dept: str) -> dict:
    """How many models can be sold today, and the four biggest groups by the key spec with their price span."""
    allp = ctx.facts.products_sync(dept)
    avail = [p for p in allp if p.qty > 0 or p.nearby_qty > 0]
    groups: dict[str, list] = {}
    order: dict[str, float] = {}
    for p in avail:
        b = _bucket(dept, p)
        if b is None:
            continue
        order[b[1]] = b[0]
        groups.setdefault(b[1], []).append(p)
    top = sorted(groups, key=lambda k: -len(groups[k]))[:4]
    tiles = []
    for k in sorted(top, key=lambda k: order[k]):
        ps = groups[k]
        lo, hi = min(ps, key=lambda p: p.price), max(ps, key=lambda p: p.price)
        span = ctx.reg.inr(lo.price, lo.price_source)
        if hi.price != lo.price:
            span += " – " + ctx.reg.inr(hi.price, hi.price_source)
        tiles.append({"label": ctx.reg.raw(k, f"spec:bucket:{dept}"),
                      "count": ctx.t("brief_models", "en", n=ctx.reg.num(len(ps), f"stock:{dept}:{k}")),
                      "span": span})
    cat_word = ctx.t(f"brief_cat_{dept}", "en")
    return {
        "n": ctx.reg.num(len(avail), f"stock:{dept}:available"),
        "of": ctx.t("brief_of_total", "en", total=ctx.reg.num(len(allp), f"catalog:{dept}:count"), cat=cat_word),
        "tiles": tiles,
        "_avail": len(avail),
        "_total": len(allp),
    }


def stock_changes(ctx: SkillContext, changes: list[dict]) -> list[dict]:
    """Since yesterday: out of stock, back in stock; 'only N left' only where the data has real counts."""
    out = []
    for c in changes[:3]:
        key = {"back_in_stock": "brief_back", "out_of_stock": "brief_out", "low": "brief_low"}[c["kind"]]
        out.append({"kind": c["kind"], "text": ctx.t(key, "en", model=ctx.reg.raw(c["model"], c["source"]),
                                                     qty=ctx.reg.num(c["qty"], c["source"]))})
    return out


# ---------------------------------------------------------------- offers (from the offer rows only)
def _offer_detail(ctx: SkillContext, o) -> str:
    """'10% off · up to ₹5,000 · HDFC cards · on bills over ₹30,000' — every number from the offer row."""
    src = f"offer:{o.id}"
    parts = []
    if o.pct:
        parts.append(ctx.t("brief_offer_pct", pct=ctx.reg.num(float(o.pct), src)))
        if o.max_discount:
            parts.append(ctx.t("brief_offer_max", amt=ctx.reg.inr(o.max_discount, src)))
    elif o.flat:
        key = "brief_offer_cashback" if o.kind == "cashback" else "brief_offer_flat"
        parts.append(ctx.t(key, amt=ctx.reg.inr(o.flat, src)))
    if o.bank:
        parts.append(ctx.t("brief_offer_bank", bank=ctx.reg.raw(o.bank, src)))
    if o.kind == "exchange_bonus":
        parts.append(ctx.t("brief_offer_exchange"))
    if o.min_amount:
        parts.append(ctx.t("brief_offer_min", amt=ctx.reg.inr(o.min_amount, src)))
    title = ctx.pick(o.title).lower()

    def repeats(part: str) -> bool:  # never repeat what the offer's title already says (any script)
        nums = re.findall(r"\d[\d,.]*%?", part)
        if nums:
            return all(n in title for n in nums)
        return bool(o.bank) and o.bank.lower() in part.lower() and o.bank.lower() in title

    return " · ".join(x for x in parts if not repeats(x))


def live_offers(ctx: SkillContext, dept: str) -> list:
    """Live offers for the department, ending soonest first."""
    d = today()
    rows = []
    for o in ctx.facts.snap.offers:
        cats = o.categories or []
        if not o.live(d) or (dept not in cats and not (dept == "tv" and "soundbar" in cats)):
            continue
        if any(k.startswith("with_tv") for k in (o.filter or {})):
            continue
        rows.append(o)
    return sorted(rows, key=lambda o: (o.valid_to, ctx.pick(o.title)))


def offer_rows(ctx: SkillContext, offers: list) -> list[dict]:
    d = today()
    out = []
    for o in offers:
        left = (o.valid_to - d).days
        kind = "ends" if left <= BADGE_DAYS else "new" if (d - o.valid_from).days <= 3 else "live"
        out.append({
            "kind": kind,
            "badge": ctx.t(f"brief_badge_{kind}"),
            "title": ctx.pick(o.title),
            "detail": _offer_detail(ctx, o),
            "till": ctx.t("brief_offer_till", date=ctx.reg.raw(fmt.date_short(o.valid_to, "en"), f"offer:{o.id}:valid_to")),
        })
    return out


# ---------------------------------------------------------------- keep in mind (each point is a data fact)
def _offer_cover(ctx: SkillContext, dept: str, offers: list) -> int:
    """How many models on sale today at least one live offer applies to (bank offers included; not bundles)."""
    from chanakya.truth.offers import eligible

    if not offers:
        return 0
    d = today()
    n = 0
    for p in ctx.facts.products_sync(dept):
        if p.qty <= 0 and p.nearby_qty <= 0:
            continue
        if any(eligible(o, p, ctx.session.home if ctx.session else _EMPTY_HOME, o.bank, bool(o.with_emi), d)
               for o in offers):
            n += 1
    return n


async def keep_points(ctx: SkillContext, dept: str, offers: list, n_avail: int, n_tasks: int) -> list[str]:
    """What the card does not already show, each a fact from the data: how far today's offers reach, an offer's
    last days, the protection plans on sale, follow-ups due."""
    d = today()
    cat = ctx.t(f"brief_cat_{dept}", "en")
    pts: list[str] = []
    cover = _offer_cover(ctx, dept, offers)
    if offers:
        pts.append(ctx.t("brief_keep_cover", "en", n=ctx.reg.num(cover, f"offers:{dept}:cover"),
                         total=ctx.reg.num(n_avail, f"stock:{dept}:available"), cat=cat))
    for o in [o for o in offers if (o.valid_to - d).days <= SOON_DAYS][:1]:
        left = (o.valid_to - d).days
        key = "brief_keep_last_day" if left == 0 else "brief_keep_days_left"
        pts.append(ctx.t(key, "en", title=ctx.pick(o.title, "en"), n=ctx.reg.num(left, f"offer:{o.id}:valid_to")))
    plans = await ctx.facts.protection(dept)
    if plans:
        lo, hi = min(p.price for p in plans), max(p.price for p in plans)
        src = f"protection:{dept}"
        span = ctx.reg.inr(lo, src) + (" – " + ctx.reg.inr(hi, src) if hi != lo else "")
        pts.append(ctx.t("brief_keep_plans", "en", n=ctx.reg.num(len(plans), src), span=span))
    if n_tasks:
        pts.append(ctx.t("brief_keep_tasks", "en", n=ctx.reg.num(n_tasks, "tasks:today")))
    return pts[:4]


def _greeting(ctx: SkillContext, name: str) -> str:
    h = now().hour
    key = "brief_hello_morning" if h < 12 else "brief_hello_afternoon" if h < 17 else "brief_hello_evening"
    return ctx.t(key, "en", name=name)


PICK_ORDER = ("tv", "ac", "wm", "fridge", "laptop")


def picker(ctx: SkillContext) -> OutMessage:
    """Which product's stock and offers first (user, 6 Nov 2026: "give the option to select the product ... before
    showing"): one list, the associate's own product first, each row with today's numbers from the data."""
    from chanakya.channel.limits import truncate
    from chanakya.core.contracts import ListRow, ListSection

    own = _dept(ctx)
    rows = []
    for cat in sorted(PICK_ORDER, key=lambda c: c != own):
        if cat not in ctx.packs.packs or not ctx.facts.products_sync(cat):
            continue
        st = stock_section(ctx, cat)
        n_off = ctx.reg.num(len(live_offers(ctx, cat)), f"offers:{cat}:live")
        title = ctx.t(f"brief_cat_{cat}")
        rows.append(ListRow(id=encode("g", "briefcat", cat), title=truncate(title[:1].upper() + title[1:], 24),
                            description=truncate(ctx.t("brief_pick_row", n=st["n"], k=n_off), 72)))
    ctx.reg.static(" ".join(r.title for r in rows))
    return OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("brief_pick_body"),
                      list_button=truncate_title(ctx.t("list_choose")), sections=[ListSection(rows=rows[:10])],
                      allowed_numbers=ctx.reg.allowed())


async def brief(ctx: SkillContext, dept: str | None = None) -> Reply:
    """Step 1 of the shift start: which product (today's numbers on each row), then that product's pre-read card
    from today's data, then one button: today's clip (and another product)."""
    staff = ctx.staff
    msgs: list[OutMessage] = []
    if dept in ctx.packs.packs:
        await repo.setting_set(f"brief_pick:{ctx.actor_wa_id}", None)
    if dept not in ctx.packs.packs:
        if staff.last_brief_date is None and staff.streak == 0 and not ctx.scratch.get("welcomed"):
            msgs.append(info_reply(ctx, ctx.t("welcome_new_staff", name=staff.name)))
        msgs.append(picker(ctx))
        await repo.setting_set(f"brief_pick:{ctx.actor_wa_id}", str(ctx.now.date()))  # 'tv' typed next = its brief
        ctx.ev("brief_picker")
        return ctx.reply(*msgs)
    d = today()
    changes = await ctx.facts.stock_changes(staff.store_id, dept)
    order = {"out_of_stock": 0, "back_in_stock": 1, "low": 2}
    changes = sorted(changes, key=lambda c: order[c["kind"]])
    stock = stock_section(ctx, dept)
    offers = live_offers(ctx, dept)
    with ctx.as_lang("en"):
        rows = offer_rows(ctx, offers[:4])
    tasks = [t for t in await repo.tasks_open(ctx.actor_wa_id) if t.due_at and repo.aware(t.due_at).date() <= d]
    keep = await keep_points(ctx, dept, offers, stock["_avail"], len(tasks))
    store = ctx.facts.store_name(staff.store_id).replace(" (TEST)", "").replace("Reliance Digital ", "")
    day = f"{fmt.day_name(d, 'en')} {fmt.date_short(d, 'en')}"
    card = Card(
        template="brief",
        lang="en",
        data={  # type: ignore[arg-type]
            "greeting": _greeting(ctx, staff.name),
            "date_line": ctx.reg.raw(f"{day} · {store}", "clock:date"),
            "cat": ctx.t(f"brief_cat_{dept}", "en"),
            "stock": {k: v for k, v in stock.items() if not k.startswith("_")},
            "changes": stock_changes(ctx, changes),
            "offers": rows,
            "offers_more": (ctx.t("brief_offers_more", "en", n=ctx.reg.num(len(offers) - len(rows), "offers:more"))
                            if len(offers) > len(rows) else None),
            "no_offers": None if offers else ctx.t("brief_no_offers", "en"),
            "keep": keep,
            "step": ctx.t("brief_step_of", "en", n="1"),
        },
    )
    ctx.reg.static(card.data["step"])
    caption = ctx.t("brief_pre_caption", n=stock["n"], cat=ctx.t(f"brief_cat_{dept}"),
                    n_off=ctx.reg.num(len(offers), "offers:live"))
    focus, _ = await focus_step(ctx)
    btns = [ctx.b("b_clip_next", encode("g", "clip", focus)), ctx.b("b_other_product", encode("g", "brief"))]
    msgs.append(info_reply(ctx, caption, btns, card=card))
    staff.last_brief_date = d
    ctx.ev("brief_shown", dept=dept, available=stock["_avail"], offers=len(offers), changes=len(changes))
    return ctx.reply(*msgs)


async def clip(ctx: SkillContext, step: str) -> Reply:
    """Step 2: today's clip (the weakest step), then 'I'm ready'."""
    step = step if step in STEPS else "translate"
    _, last = await focus_step(ctx)
    f = CLIPS / f"{step}_{ctx.lang}.mp4"
    if not f.exists():
        f = CLIPS / f"{step}.mp4"
    why = (ctx.t("clip_why_last", step=ctx.t(f"step_{step}"), n=ctx.reg.num(last, "score:last"))
           if last is not None else ctx.t("clip_why_first", step=ctx.t(f"step_{step}")))
    btns = [ctx.b("b_ready", encode("g", "ready", step))]
    ctx.ev("clip_sent", focus=step)
    if f.exists():
        mid = await ctx.svc.channel.upload_media(f.read_bytes(), "video/mp4", f.name)
        return ctx.reply(
            OutMessage(to=ctx.actor_wa_id, kind="video", media_id=mid, caption=why, allowed_numbers=ctx.reg.allowed()),
            info_reply(ctx, ctx.t("clip_then_ready"), btns),
        )
    return ctx.reply(info_reply(ctx, why + "\n" + ctx.t("tip_card", step=ctx.t(f"step_{step}"), tip=ctx.t(f"tip_{step}")),
                                btns))


async def ready(ctx: SkillContext, step: str) -> Reply:
    """Step 3: ready for the floor. Only now the customer button (or simply a voice note)."""
    step = step if step in STEPS else "translate"
    ctx.ev("shift_ready", focus=step)
    from chanakya.skills import wins

    name = ctx.staff.name if ctx.staff and ctx.staff.name else ""
    first = ctx.t("ready_title", name=name) if name else ctx.t("ready_title_plain")
    won = await wins.line(ctx, yesterday=True)
    if won:  # yesterday's own wins (Family Cards, answers, sales, streak), in the same line: no zeros
        first += " " + won
    m = info_reply(ctx, first + "\n" + ctx.t("ready_body", tip=ctx.t(f"tip_{step}")), [ctx.b("b_new_customer", encode("g", "new"))])
    m.footer = ctx.t("footer_voice_q")
    return ctx.reply(m)


# ---------------------------------------------------------------- the quiz (kept for old buttons; not in the flow)
def _quiz_for(ctx: SkillContext, step: str):
    pack = ctx.packs.get(_dept(ctx))
    qs = pack.quiz if pack else []
    return next((q for q in qs if q.step == step), qs[0] if qs else None)


async def quiz(ctx: SkillContext, step: str) -> Reply:
    q = _quiz_for(ctx, step)
    if q is None:
        return ctx.reply(info_reply(ctx, ctx.t("tip_" + step if step in STEPS else "tip_translate")))
    btns = [Button(id=encode("g", "qa", q.id, a.id), title=truncate_title(ctx.pick(a.title))) for a in q.answers[:3]]
    return ctx.reply(info_reply(ctx, ctx.pick(q.q), btns))


async def answer(ctx: SkillContext, qid: str, aid: str) -> Reply:
    q = None
    for p in ctx.packs.packs.values():
        q = next((x for x in p.quiz if x.id == qid), None) or q
    if q is None:
        return ctx.reply(info_reply(ctx, ctx.t("fallback_body")))
    explain = ctx.pick(q.explain)
    if aid == q.correct:
        ctx.staff.streak = (ctx.staff.streak or 0) + 1
        body = ctx.t("quiz_right", explain=explain, streak=ctx.reg.num(ctx.staff.streak, "staff:streak"))
    else:
        right = next((a for a in q.answers if a.id == q.correct), None)
        body = ctx.t("quiz_wrong", answer=ctx.pick(right.title) if right else "", explain=explain)
    return ctx.reply(info_reply(ctx, body, [ctx.b("b_new_customer", encode("g", "new"))]))


def help_reply(ctx: SkillContext) -> Reply:
    btns = [ctx.b("b_new_customer", encode("g", "new")), ctx.b("b_status", encode("g", "status"))]
    return ctx.reply(info_reply(ctx, ctx.t("help_body"), btns))
