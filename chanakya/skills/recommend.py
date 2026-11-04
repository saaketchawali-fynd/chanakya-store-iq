"""Step 5 — Dikhao (Recommend & demonstrate): 3 options (no prices), one demo, "Dikha diya ✓"."""

from __future__ import annotations

import re

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


def _price_later(ctx: SkillContext, step: str) -> str:
    """The price asked before the demo: a line to say to the customer (in their language), why it waits, and how to
    keep them with you while it does."""
    from chanakya.core.coach import bolo_block, do_block, kyun_block

    return "\n\n".join([bolo_block(ctx, ctx.tc("bolo_price_later")),
                        kyun_block(ctx, ctx.t("kyun_price_later") + " · " + step), do_block(ctx, "do_price_later")])


def gate_price_reply(ctx: SkillContext) -> Reply:
    s = ctx.session
    best = ctx.facts.product_sync(state.best_sku(s)) if state.best_sku(s) else None
    if not state.options_gate_open(s, ctx.pack):
        n = state.missing_for_options(s, ctx.pack)
        body = _price_later(ctx, ctx.t("gate_price_assess", n=ctx.reg.num(n, "calc:gate_missing")))
        btns = [
            ctx.b("b_next_q", ctx.sid("go")),
            ctx.b("b_override_asked", ctx.sid("override", "price", "asked")),
            ctx.b("b_status", encode("g", "status")),
        ]
        return ctx.reply(info_reply(ctx, body, btns))
    body = _price_later(ctx, ctx.t("gate_price", best=C.product_name(ctx, best)) if best
                        else ctx.t("gate_price_no_options"))
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
    products = upgrade_pool(ctx, products)
    opts = compute_options(products, ctx.session.home, pack, ctx.facts.snap.guides, ctx.session.extra.get("wants"))
    keep = ctx.x.get("keep_options")
    if keep and opts is not None:  # more options asked for: the earlier best fits stay, the new / broader ones join
        opts = merge_options(ctx, keep, opts, products)
    return opts


MAX_OPTIONS = 5
LETTERS = "ABCDE"


def merge_options(ctx: SkillContext, keep: dict, fresh, products: list):
    """User, 4 Nov 2026: after the follow-up questions the earlier best fits stay (their labels and roles); the
    products the new answers pick that were not shown join as 'new fit'; if the answers change nothing, the next best
    fits for this home join as 'more options' (broader). At most five in all."""
    from chanakya.brain.fit import fit
    from chanakya.brain.options import Option, OptionSet

    pack, home, g = ctx.pack, ctx.session.home, ctx.facts.snap.guides
    roles = ctx.x.get("keep_roles") or {}
    reasons = ctx.x.get("keep_reasons") or {}
    out = []
    for lab in sorted(keep):
        p = ctx.facts.product_sync(keep[lab])
        if p is not None:
            out.append(Option(lab, roles.get(lab, "best" if not out else "value"), p, fit(p, home, pack, g),
                              reasons.get(lab, "value_fit")))
    have = {o.product.sku for o in out}
    new = [o for o in fresh.options if o.product.sku not in have]
    kind = "new"
    if not new:  # the same picks: widen to the next best fits for this home that are on sale
        kind = "broader"
        pool = [p for p in products if p.sku not in have and (p.qty > 0 or p.nearby_qty > 0)
                and p.brand not in set(ctx.x.get("excluded_brands") or [])]
        scored = sorted(((fit(p, home, pack, g), p) for p in pool), key=lambda x: (-x[0].score, x[1].price))
        new = [Option("", "broader", p, f, "alt") for f, p in scored if not f.excluded][:2]
    free = [x for x in LETTERS if x not in {o.label for o in out}]
    for o, lab in zip(new[: MAX_OPTIONS - len(out)], free, strict=False):
        out.append(Option(lab, kind, o.product, o.fit, "alt"))
    merged = OptionSet(out)
    merged.merged = kind  # type: ignore[attr-defined]
    return merged


def current_options(ctx: SkillContext):
    """The options as shown (labels, roles, reasons kept on the session), for the later steps; else computed."""
    from chanakya.brain.fit import fit
    from chanakya.brain.options import Option, OptionSet

    s = ctx.session
    if not s.option_labels:
        return _option_set(ctx)
    roles = s.extra.get("option_roles") or {}
    reasons = s.extra.get("option_reason_keys") or {}
    out = []
    for lab in sorted(s.option_labels):
        p = ctx.facts.product_sync(s.option_labels[lab])
        if p is not None:
            out.append(Option(lab, roles.get(lab) or state.option_role(s, lab) or "value", p,
                              fit(p, s.home, ctx.pack, ctx.facts.snap.guides), reasons.get(lab, "value_fit")))
    return OptionSet(out) if out else _option_set(ctx)


def upgrade_pool(ctx: SkillContext, products: list) -> list:
    """The product they have now is never offered back (user, 4 Nov 2026): not the same model; for a replacement or
    an upgrade nothing smaller than it; and, asked for something newer, not the same brand at the same size again
    unless they asked for exactly that. Each rule only applies if models remain on sale after it."""
    home, wants = ctx.session.home, ctx.session.extra.get("wants") or {}
    ex = home.existing or {}
    if not ex:
        return products

    def keep(pool: list) -> list:
        return pool if any(p.qty > 0 or p.nearby_qty > 0 for p in pool) else products

    out = [p for p in products if p.sku != ex.get("sku")]
    size = ex.get("size_in")
    if size and "size_in" not in wants:
        out = keep([p for p in out if (p.spec("size_in") or 0) >= float(size)])
    if home.upgrade and size and ex.get("brand") and not (wants.get("brand") and "size_in" in wants):
        out = keep([p for p in out if not ((p.brand or "").lower() == str(ex["brand"]).lower()
                                           and float(p.spec("size_in") or 0) == float(size))])
    return out


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


def note_block(ctx: SkillContext, note: dict) -> str:
    """What the note was understood as, in bullets: the home facts; what they asked for; the TV they have now and
    the wish for something newer; the needs it must meet."""
    from chanakya.skills.assess import note_facts

    home = ctx.session.home
    facts = note_facts(ctx)
    wants = want_labels(ctx, note.get("wants") or {})
    meaning = meaning_labels(ctx)
    asks = meaning[: len(home.asks)]
    rest = meaning[len(home.asks):]
    out = [ctx.t("note_head")]
    if facts:
        out.append("• " + " · ".join(facts[:5]))
    if wants:
        out.append("• " + ctx.t("note_line_wants", what=", ".join(wants)))
    if rest:
        out.append("• " + " · ".join(rest))
    if asks:
        out.append("• " + ctx.t("note_line_needs", what=", ".join(asks)))
    return "\n".join(out)


def option_block(ctx: SkillContext, o, opts, why: list[str] | None = None) -> str:
    """'*A · Name* — best fit' and, as bullets, why it fits them: its reason, then its own points with proof."""
    from chanakya.skills import fitview as FV

    p = o.product
    tag = {"best": "best_pick", "new": "reason_new", "broader": "reason_broader"}.get(o.role)
    head = f"*{o.label} · {C.product_name(ctx, p)}*" + (f" — _{ctx.t(tag)}_" if tag else "")
    bullets = list(why or []) or [option_reason(ctx, o, opts, ctx.lang)]
    lang = ctx.lang  # the associate's own language (Hindi in Roman Hinglish)
    for pt in FV.points(ctx, p, lang, 4):
        if len(bullets) >= (3 if o.role == "best" else 2):
            break
        text = pt.get("text") if isinstance(pt, dict) else str(pt)
        proof = pt.get("proof") if isinstance(pt, dict) else None
        line = f"{text} ({proof})" if proof and proof not in text else text
        same_size = "size" in text.lower() and any("size" in b.lower() for b in bullets)
        if text and not same_size and not any(text.lower() in b.lower() or b.lower() in text.lower() for b in bullets):
            bullets.append(line)
    if not (p.qty > 0):
        bullets.append(_stock_line(ctx, p))
    return "\n".join([head] + [f"• {b}" for b in bullets if b])


def meaning_labels(ctx: SkillContext, lang: str | None = None) -> list[str]:
    """The note's meaning said back: the needs asked (by theme), the product they have now, an upgrade wish."""
    from chanakya.brain import highlights as HL

    home, lang = ctx.session.home, lang or ctx.lang
    out = []
    for a in home.asks:
        th = HL.theme(ctx.pack.category, a)
        if th:
            out.append(ctx.reg.static(th.label))
    ex = home.existing or {}
    if ex.get("brand") or ex.get("size_in"):
        what = " ".join(x for x in (str(ex.get("brand") or ""), f'{int(float(ex["size_in"]))}"' if ex.get("size_in") else "") if x)
        out.append(ctx.t("note_existing", lang, what=ctx.reg.raw(what, "note:existing")))
    if home.upgrade:
        out.append(ctx.t("note_upgrade", lang))
    return out


async def more_menu(ctx: SkillContext) -> Reply:
    """'Tell me more' with the products: only what the note (or the answers) did not cover yet, from the questions
    the data can act on; then the offers on these, and a different budget or brand."""
    from chanakya.brain import data_questions as DQ
    from chanakya.brain import highlights as HL
    from chanakya.channel.limits import truncate, truncate_title
    from chanakya.core.contracts import ListRow, ListSection

    s, pack, home = ctx.session, ctx.pack, ctx.session.home
    skip = C.skipped(ctx)
    avail = C._avail(ctx)
    cands = list(dict.fromkeys([*pack.ask_order, *pack.required_slots]))
    for th in HL.themes(pack.category):  # the needs the products' own highlights can act on (elders, seating …)
        cands += [n.slot for n in th.needs if n.slot and n.w >= HL.STRONG and HL.informative(th, avail)]
    rows = []
    for sid in dict.fromkeys(cands):
        sd = pack.slot(sid)
        if sd is None or P.known(home, sid) or sid in skip or sd.phase in ("site", "close"):
            continue
        if sid in ("trigger", "old_size") and home.existing:
            continue
        if sd.ask_if and not P.eval_slot_cond(home, sd.ask_if["slot"], {k: v for k, v in sd.ask_if.items() if k != "slot"},
                                              pack):
            continue
        if any(th.id in home.asks for th in HL.themes(pack.category) if any(n.slot == sid for n in th.needs)):
            continue  # the note already named the need this question is for
        rows.append(ListRow(id=ctx.sid("askq", sid), title=truncate(ctx.pick(sd.title), 24),
                            description=truncate(ctx.pick(sd.ask, ctx.lang), 72)))
    done = set(s.extra.get("dq_done") or [])
    q = DQ.next_data_question(pack, avail, done, s.extra.get("wants") or {})
    if q is not None:
        short = f"want_label_{q[0]}"  # a row title is 24 characters: the short label ('Picture type'), never cut
        title = ctx.t(short) if i18n_has(short) else (ctx.t(f"dq_what_{q[0]}") if i18n_has(f"dq_what_{q[0]}")
                                                      else DQ.label(q[0], True))
        rows.append(ListRow(id=ctx.sid("askdq"), title=truncate(title, 24), description=truncate(ctx.t("more_dq_d"), 72)))
    rows = rows[:7] + [ListRow(id=ctx.sid("ofs"), title=ctx.t("row_more_offers"), description=ctx.t("row_more_offers_d")),
                       ListRow(id=ctx.sid("nonefit"), title=ctx.t("row_more_other"), description=ctx.t("row_more_other_d"))]
    for r in rows:
        ctx.reg.static(r.title + " " + (r.description or ""))
    body = ctx.t("more_body" if len(rows) > 2 else "more_body_none")
    m = OutMessage(to=ctx.actor_wa_id, kind="list", body=body, list_button=truncate_title(ctx.t("list_more")),
                   sections=[ListSection(rows=rows[:10])], footer=ctx.t("more_footer"), allowed_numbers=ctx.reg.allowed())
    ctx.ev("more_menu", open=[r.id.split(":")[-1] for r in rows])
    return ctx.reply(m)


def uncovered(ctx: SkillContext) -> list[str]:
    """The questions the note and the answers did not cover, that the data can act on (slot ids; 'dq' = the next
    question about what the models on sale differ on). Same rules as 'Tell me more'."""
    from chanakya.brain import data_questions as DQ
    from chanakya.brain import highlights as HL

    s, pack, home = ctx.session, ctx.pack, ctx.session.home
    skip, avail = C.skipped(ctx), C._avail(ctx)
    cands = list(dict.fromkeys([*pack.ask_order, *pack.required_slots]))
    for th in HL.themes(pack.category):
        cands += [n.slot for n in th.needs if n.slot and n.w >= HL.STRONG and HL.informative(th, avail)]
    out = []
    for sid in dict.fromkeys(cands):
        sd = pack.slot(sid)
        if sd is None or P.known(home, sid) or sid in skip or sd.phase in ("site", "close"):
            continue
        if sid in ("trigger", "old_size") and (home.existing or P.known(home, "trigger")):
            continue
        if sd.ask_if and not P.eval_slot_cond(home, sd.ask_if["slot"], {k: v for k, v in sd.ask_if.items() if k != "slot"},
                                              pack):
            continue
        if any(th.id in home.asks for th in HL.themes(pack.category) if any(n.slot == sid for n in th.needs)):
            continue
        out.append(sid)
    if DQ.next_data_question(pack, avail, set(s.extra.get("dq_done") or []), s.extra.get("wants") or {}) is not None:
        out.append("dq")
    return out


FOLLOWUPS_MAX = 2


async def more_options(ctx: SkillContext) -> Reply:
    """'More options' after the note (user, 4 Nov 2026): a follow-up or two the note did not cover, then the earlier
    best fits stay and the new (or broader) fits join, so they see all of them."""
    s = ctx.session
    ctx.x["keep_options"] = dict(s.option_labels)
    ctx.x["keep_roles"] = dict(s.extra.get("option_roles") or {})
    ctx.x["keep_reasons"] = dict(s.extra.get("option_reason_keys") or {})
    ctx.x["followups"] = 0
    ctx.ev("more_options_asked", shown=list(s.option_labels.values()))
    return await _next_followup(ctx)


async def _next_followup(ctx: SkillContext) -> Reply:
    todo = uncovered(ctx)
    if not todo or int(ctx.x.get("followups") or 0) >= FOLLOWUPS_MAX:
        ctx.x.pop("more_q", None)
        return await show_options(ctx, override_checked=True)
    q = todo[0]
    ctx.x["more_q"] = "chain:" + q
    lead = ctx.t("followup_lead", n=ctx.reg.num(int(ctx.x.get("followups") or 0) + 1, "followup:no"))
    if q == "dq":
        m = C.data_question(ctx, force=True)
        if m is None:
            ctx.x["followups"] = FOLLOWUPS_MAX
            return await _next_followup(ctx)
    else:
        m = C.question_reply(ctx, q)
    m.body = f"{lead}\n\n{m.body}"
    return ctx.reply(m)


async def followup_answered(ctx: SkillContext) -> Reply:
    ctx.x["followups"] = int(ctx.x.get("followups") or 0) + 1
    return await _next_followup(ctx)


async def side_by_side(ctx: SkillContext) -> Reply:
    """The shortlisted products side by side: on their own wall when a room photo is there (each drawn alone),
    else their product photos; their needs, benefits, features, cost and offers in one table."""
    from chanakya.skills import sidebyside, tryon_flow

    s = ctx.session
    if len(s.option_labels) < 2:
        return await demo(ctx)
    if ctx.pack.placeable and await tryon_flow.has_room(ctx):  # their room: draw each one in it, then compare
        return await tryon_flow.choose(ctx, "cmp", None)
    best = state.best_label(s)
    labs = [best] + [x for x in sorted(s.option_labels) if x != best]
    card = sidebyside.build(ctx, labs)
    if card is None:
        return await prefer(ctx)
    btns = [ctx.b("b_demo_guide", ctx.sid("demo")),
            ctx.b("b_tryon", ctx.sid("tryon")) if ctx.pack.placeable else ctx.b("b_they_chose", ctx.sid("prefer")),
            ctx.b("b_more_options", ctx.sid("moreopts"))]
    return ctx.reply(info_reply(ctx, sidebyside.summary(ctx, labs), btns, card=card))


async def ask_more(ctx: SkillContext, slot: str | None) -> Reply:
    """One question from 'Tell me more'; the answer re-picks the products at once."""
    ctx.x["more_q"] = slot or "dq"
    if slot is None:
        m = C.data_question(ctx, force=True)
        if m is None:
            ctx.x.pop("more_q", None)
            return await show_options(ctx, override_checked=True)
        return ctx.reply(m)
    return ctx.reply(C.question_reply(ctx, slot))


KEY_SIZE = (("size_in", "inch"), ("ton", "ton"), ("capacity_kg", "kg"), ("capacity_l", "L"), ("ram_gb", "GB RAM"))
RANKS = {"resolution": ["HD", "Full HD", "4K", "8K"], "panel": ["LED", "Nanocell", "QLED", "MiniLED", "OLED"]}


def _hl_text(ctx: SkillContext, pt, p, lang: str) -> str:
    from chanakya.brain import highlights as HL

    proof = ctx.reg.raw(pt.proof, f"highlight:{p.sku}")
    text = HL.card_text(pt, lang)  # in this language: Hindi in Roman Hinglish, Marathi, English
    return ctx.reg.static(text) + f" ({proof})"


def option_reason(ctx: SkillContext, o, opts, lang: str) -> str:
    """Why this option, in facts (user, 4 Nov 2026): the best pick by the need it meets; a step up by what it really
    adds over the best pick (a bigger size, a better picture class, a highlight the best one lacks); never a fixed
    "bigger screen" when it is the same size. The value pick does cost less (options.py guarantees it)."""
    from chanakya.brain import highlights as HL

    best = opts.by_role("best")
    p = o.product
    if o.role == "best":
        if o.reason_key == "value_fit" or o.reason_key not in ctx.pack.reasons:  # no spec rule to name: their need
            pts = HL.points(p, ctx.session.home, ctx.session.extra.get("wants") or {}, ctx.pack, n=1, general=False)
            if pts:
                return _hl_text(ctx, pts[0], p, lang)
        return _reason(ctx, o.reason_key, lang)
    if o.role in ("new", "broader") and best is not None:  # joined after the follow-ups: what it adds, from the data
        diff = HL.differences(p, best.product, ctx.session.home, ctx.session.extra.get("wants") or {}, ctx.pack)
        if diff:  # the role itself ('New fit' / 'More option') is said in the option's head
            return _hl_text(ctx, diff[0], p, lang)
        return ctx.t(f"reason_{o.role}", lang)
    if o.role != "step" or best is None:
        return _reason(ctx, o.reason_key, lang)
    bp = best.product
    for key, unit in KEY_SIZE:
        a, b = p.spec(key), bp.spec(key)
        if a and b and float(a) > float(b):
            return ctx.t("reason_step_size", lang, a=ctx.reg.num(a, f"spec:{p.sku}:{key}", unit),
                         b=ctx.reg.num(b, f"spec:{bp.sku}:{key}", unit))
    for key, ladder in RANKS.items():
        a, b = p.spec(key), bp.spec(key)
        if a in ladder and b in ladder and ladder.index(a) > ladder.index(b):
            return ctx.t("reason_step_spec", lang, label=ctx.reg.static(PV.label(key, lang)),
                         a=ctx.reg.raw(str(a), f"spec:{p.sku}:{key}"), b=ctx.reg.raw(str(b), f"spec:{bp.sku}:{key}"))
    diff = HL.differences(p, bp, ctx.session.home, ctx.session.extra.get("wants") or {}, ctx.pack)
    if diff:
        return ctx.t("reason_step_hl", lang, what=_hl_text(ctx, diff[0], p, lang))
    return ctx.t("reason_step_other", lang)


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
                "reason": option_reason(ctx, o, opts, cl),
                "points": FV.points(ctx, p, cl, 3 if o.role == "best" else 2),  # each option: why for THEM
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
    s.extra["option_reason_keys"] = {o.label: o.reason_key for o in opts.options}
    merged = getattr(opts, "merged", None)
    ctx.x.pop("keep_options", None)
    best, value, step = opts.by_role("best"), opts.by_role("value"), opts.by_role("step")
    s.shortlist = [o.product.sku for o in opts.options if o.role == "best"] + [
        o.product.sku for o in opts.options if o.role != "best"]
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
    btns = [C.option_button(ctx, o.label, o.product) for o in opts.options]
    blocks = []
    if note is not None:  # from the note: what was understood, in bullets, then the products picked from it
        s.extra["from_note"] = True
        blocks.append(note_block(ctx, note))
        why_b = _note_reasons(ctx, best.product, set(note.get("slots") or []), note.get("wants") or {})
        ctx.ev("note_shortlist", slots=list(note.get("slots") or []), wants=list((note.get("wants") or {}).keys()),
               best=best.product.sku)
        blocks.append(ctx.t("note_fits_head", n=ctx.reg.num(len(opts.options), "options:count"),
                            cat=ctx.t(f"brief_cat_{pack.category}")))
    elif merged:  # after the follow-ups: what stayed and what joined
        why_b = []
        n_new = sum(1 for o in opts.options if o.role in ("new", "broader"))
        blocks.append(ctx.t(f"opts_head_{merged}", n=ctx.reg.num(n_new, "options:new"),
                            cat=ctx.t(f"brief_cat_{pack.category}")))
        ctx.ev("options_widened", kind=merged, added=[o.product.sku for o in opts.options if o.role in ("new", "broader")])
    else:
        why_b = []
        blocks.append(ctx.t("opts_head", n=ctx.reg.num(len(opts.options), "options:count"),
                            cat=ctx.t(f"brief_cat_{pack.category}")))
    # one product page per product (user, 5 Nov 2026: not cramped in one card), its own why-for-them as the caption;
    # after the follow-ups only the products that joined get a page (the earlier ones were just shown)
    from chanakya.skills.translate import product_card_en

    pages = []
    for o in [o for o in opts.options if not merged or o.role in ("new", "broader")] or opts.options:
        cap = option_block(ctx, o, opts, why_b if o.role == "best" else None)
        off = PV.offer_line(ctx, o.product, ctx.lang)["text"].replace(" · ", ", ")
        cap += "\n• " + ctx.t("fact_offer", offer=off)  # the card is the reference design: its offer said here
        if not pages:  # the first page also carries the heading (and what the note said)
            cap = "\n\n".join(blocks + [cap])
        with ctx.as_lang("en"):
            chip = ctx.t(f"pdp_role_{o.role}") if i18n_has(f"pdp_role_{o.role}") else ctx.t("pdp_role_option")
        card = await product_card_en(ctx, o.product, chip=chip, chip_label=o.label, chip_kind=o.role)
        pages.append(info_reply(ctx, cap, card=card))
    blocks = [ctx.t("opts_hint")]
    demo = pick_demo(opts, s.home, pack, ctx.facts.snap.guides)
    if demo:
        d, other, _ = demo
        s.demo_recipe = d.id
        ctx.x["demo_other"] = other
        ctx.x["demos_sent"] = [d.id]
    many = len(opts.options) >= 2
    if note is not None:  # after a note: are these right for them? yes → see them side by side; no → more options
        blocks.append(ctx.t("note_ask_happy"))
        btns = [ctx.b("b_happy", ctx.sid("cmpall") if many else ctx.sid("demo")),
                ctx.b("b_more_options", ctx.sid("moreopts")), ctx.b("b_demo_guide", ctx.sid("demo"))]
    else:  # the next steps with the products (user, 4 Nov 2026): compare in their home, the demo, or more
        btns = [ctx.b("b_compare_all", ctx.sid("cmpall")) if many else ctx.b("b_demo_guide", ctx.sid("demo")),
                ctx.b("b_demo_guide", ctx.sid("demo")) if many else (
                    ctx.b("b_tryon", ctx.sid("tryon")) if pack.placeable else ctx.b("b_they_chose", ctx.sid("prefer"))),
                ctx.b("b_more_needs", ctx.sid("more"))]
    last = info_reply(ctx, "\n\n".join(blocks), btns)  # the next steps, last, so the buttons sit at the bottom
    if ctx.x.pop("rerank_note", None):
        last.footer = ctx.t(f"rr_footer_{ctx.x.get('rerank_last', 'lower')}")
    return ctx.reply(*pages, last)


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
    opts = current_options(ctx)
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

    from chanakya.brain import highlights as HL

    steps, used, fallbacks = [], set(), []
    wants = s.extra.get("wants") or {}
    for key in wants:  # 1. what they asked for (picture type, brand, size …), shown first
        if _val(best.product, key) is None:
            continue
        steps.append({"kind": "want", "key": key, "other": differs(key)})  # no other differs: show it on the best
        used.add(key)
    for d, other, score in rank_demos(opts, s.home, ctx.pack, ctx.facts.snap.guides):  # 2. their needs, strongest first
        if score <= 0:
            fallbacks.append((d, other))
            continue
        if len(steps) >= MAX_STEPS - 1 or (d.differs_on and d.differs_on in used):
            continue
        steps.append({"kind": "recipe", "id": d.id, "other": other, "key": d.differs_on})
        if d.differs_on:
            used.add(d.differs_on)
    hl = []  # 2b. their needs, where the products' own highlights differ (either way: the honest comparison)
    for o in others:
        for a, b, bonus in ((best, o, 0.0), (o, best, -0.1)):
            pt = next((x for x in HL.differences(a.product, b.product, s.home, wants, ctx.pack) if x.need), None)
            if pt:
                hl.append((pt.weight + bonus, {"kind": "highlight", "theme": pt.theme, "on": a.label, "other": b.label}))
    for _, st in sorted(hl, key=lambda x: -x[0]):
        if len(steps) >= MAX_STEPS - 1 or f"hl:{st['theme']}" in used:
            continue
        steps.append(st)
        used.add(f"hl:{st['theme']}")
    for key in FEATURE_KEYS.get(ctx.pack.category, []):  # 3. one more real difference in the data
        if len(steps) >= MAX_STEPS:
            break
        if key not in used and (lab := differs(key)):
            steps.append({"kind": "feature", "key": key, "other": lab})
            used.add(key)
            break
    if len(steps) < MAX_STEPS and others:  # 3b. or one the products' own highlights show
        for o in others:
            pt = next((x for x in HL.differences(best.product, o.product, s.home, wants, ctx.pack)
                       if f"hl:{x.theme}" not in used), None)
            if pt:
                steps.append({"kind": "highlight", "theme": pt.theme, "on": best.label, "other": o.label})
                used.add(f"hl:{pt.theme}")
                break
    if not others:  # one product: show its own highlights for their needs, their asks first
        for pt in HL.points(best.product, s.home, wants, ctx.pack, n=4, general=False):
            if len(steps) < MAX_STEPS - 1 and f"hl:{pt.theme}" not in used:
                steps.append({"kind": "highlight", "theme": pt.theme, "on": best.label, "other": None})
                used.add(f"hl:{pt.theme}")
    if len(steps) < 2 and ctx.pack.category == "tv" and P.number(s.home, "seat_distance", ctx.pack) \
            and not any(st.get("id") == "distance_demo" for st in steps):
        same = all(_val(o.product, "size_in") == _val(best.product, "size_in") for o in others)
        steps.append({"kind": "distance", "other": None} if same else
                     {"kind": "recipe", "id": "distance_demo", "other": others[0].label if others else None,
                      "key": "size_in"})
    for d, other in fallbacks:  # last: a neutral demo that claims nothing (their own show)
        if not steps and d.id == "own_content":
            steps.append({"kind": "recipe", "id": d.id, "other": other, "key": None})
    return steps[:MAX_STEPS]


def _step_title(ctx: SkillContext, st: dict, lang: str) -> str:
    from chanakya.i18n.lang import pick

    if st["kind"] == "recipe":
        d = ctx.pack.demo(st["id"])
        return pick(d.title, lang) if d else st["id"]
    if st["kind"] == "highlight":
        from chanakya.brain import highlights as HL

        th = HL.theme(ctx.pack.category, st["theme"])
        return th.label if th else st["theme"]
    if st["kind"] == "distance":
        d = ctx.pack.demo("distance_demo")
        return pick(d.title, lang) if d else "distance"
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
    """The demo guide in ONE message (user, 4 Nov 2026): the guide card, and under it, for each product, numbered
    steps to run one after another: their need first (the wow), then the rest. No 'Demo 1/2' messages."""
    s = ctx.session
    if not s.shortlist:
        return await show_options(ctx)
    key = sorted(s.option_labels.values())
    if ctx.x.get("demo_plan_for") != key or not ctx.x.get("demo_plan"):
        ctx.x["demo_plan"], ctx.x["demo_plan_for"] = demo_plan(ctx), key
    plan = ctx.x["demo_plan"]
    first = plan[0] if plan else {"kind": "guide", "key": None}
    s.demo_recipe = first.get("id") or f"{first['kind']}:{first.get('theme') or first.get('key') or ''}"
    ctx.x["demos_sent"] = list(dict.fromkeys([*(ctx.x.get("demos_sent") or []), s.demo_recipe]))
    ctx.ev("demo_recipe_sent", recipe=s.demo_recipe, demo_step=1, of=len(plan))
    card = await demo_guide_card(ctx)
    body = await demo_guide_text(ctx)
    ctx.x["demo_guide_for"] = key
    ctx.ev("demo_guide_sent", products=len(card.data["products"]) if card else 0)
    btns = [ctx.b("b_shown", ctx.sid("shown")),
            ctx.b("b_compare_all", ctx.sid("cmpall")) if len(s.option_labels) >= 2 else ctx.b("b_they_chose", ctx.sid("prefer")),
            ctx.b("b_tryon", ctx.sid("tryon")) if ctx.pack.placeable else ctx.b("b_more_needs", ctx.sid("more"))]
    msg = info_reply(ctx, body, btns, card=card)
    msg.footer = ctx.t("guide_footer")
    return ctx.reply(msg)


# the demo guide (user, 4 Nov 2026): for each product, the wow first (their top need, shown the way that proves it),
# then their next need, then the rest worth showing; every step names its proof from the data
GUIDE_STEPS = {"best": 3, "other": 2}


async def guide_steps(ctx: SkillContext, p, other, n: int = 3, lang: str = "en", say_lang: str = "en") -> list[dict]:
    """[{tag, title, proof, do, say}] for one product: their asks / needs first (pack benefits proven by a spec, the
    product's own highlights), the size from their sofa, then its other highlights and one real difference.
    `lang`: the associate's words (what to show and do, the benefit titles); `say_lang`: the
    customer's language for the line to say."""
    from chanakya.brain import highlights as HL
    from chanakya.i18n.lang import fill as _fill
    from chanakya.skills import fitview as FV
    from chanakya.skills.translate import fill, matching

    tl = lang  # titles in the associate's language too (user, 5 Nov 2026)
    s, pack, home = ctx.session, ctx.pack, ctx.session.home
    wants = s.extra.get("wants") or {}
    name, oname = C.product_name(ctx, p), (C.product_name(ctx, other) if other is not None else "")
    dist = P.number(home, "seat_distance", pack)
    steps_n = ctx.reg.num(max(1, round(dist / 2.5)), "calc:steps_from_distance") if dist else ""
    out, seen = [], set()

    def add(title, proof, do, say, key):
        if key in seen or not title:
            return
        seen.add(key)
        out.append({"title": ctx.reg.static(title), "proof": proof, "do": ctx.reg.static(do or ""),
                    "say": ctx.reg.static(say or "")})

    def hl_step(pt) -> None:
        th = HL.theme(pack.category, pt.theme)
        title = HL.card_text(pt, tl)  # in the associate's language (Hindi in Roman Hinglish)
        add(title, ctx.reg.raw(pt.proof, f"highlight:{p.sku}"), HL.demo_text(th, lang, alone=other is None),
            HL.say(pt, say_lang) if pt.need else "", f"hl_{pt.theme}")

    need_pts = HL.points(p, home, wants, pack, n=8, general=False)
    for pt in need_pts:  # 1. what they asked for in their own words (the note's meaning) leads
        if pt.need == "as_asked":
            hl_step(pt)
    from chanakya.brain import packs as PK

    for b in matching(ctx, p):  # 2. spec-proven benefits for their answers, with the demo for THAT spec
        if b.id == "b_size":
            continue  # the size is shown from their sofa (below), the same for one product or two
        bkeys = set((b.when_spec or {}).keys()) | {k for c in b.when_spec_any or [] for k in c}
        d = next((d for d in pack.demos if d.differs_on in bkeys and PK.eval_slots_map(home, d.when_slots, pack)), None)
        do = _fill(ctx.pick(d.steps, lang), best=name, other=oname, steps=steps_n) if d and other is not None else ""
        th = next((t for t in HL.themes(pack.category) if bkeys & {sp["key"] for sp in t.specs}), None)
        if th is not None:  # the same benefit as a highlight theme (apps → smart): one step, with that theme's demo
            seen.add(f"hl_{th.id}")
            do = do or HL.demo_text(th, lang, alone=other is None)
        add(ctx.pick(b.card, tl), FV._proof(ctx, p, b, tl), do, await fill(ctx, ctx.pick(b.say, say_lang), p, say_lang),
            b.id)
    for pt in need_pts:  # 3. their other needs its own highlights answer
        hl_step(pt)
    size = p.spec("size_in")
    if pack.category == "tv" and dist and size:
        add(ctx.t("guide_size_title", tl), ctx.reg.num(size, f"spec:{p.sku}:size_in", "inch"),
            ctx.t("guide_size_do", lang, steps=steps_n), ctx.t("guide_size_say", say_lang), "size")
    for pt in HL.points(p, home, wants, pack, n=8, general=True):  # then: its other highlights, its own words
        if pt.need is None:
            hl_step(pt)
    if other is not None:
        for d in _diffs(ctx, p, other, tl, 1):  # one real difference from the other option, as a fact
            add(f"{d['label']}: {d['value']}", None, ctx.t("guide_diff_do", lang, other=oname), "", f"diff_{d['label']}")
    tags = ["wow", "then", "more"]
    for i, st in enumerate(out):
        st["tag"] = tags[min(i, 2)]
    return out[:n]


def _guide_labs(ctx: SkillContext) -> tuple[str, list[str], dict]:
    s = ctx.session
    best_lab = state.best_label(s) or sorted(s.option_labels)[0]
    labs = [best_lab] + [lab for lab in sorted(s.option_labels) if lab != best_lab]
    return best_lab, labs, {lab: ctx.facts.product_sync(s.option_labels[lab]) for lab in labs}


def _other_for(best_lab: str, lab: str, labs: list[str], prods: dict):
    return prods.get(best_lab) if lab != best_lab else next((prods[x] for x in labs if x != best_lab), None)


async def demo_guide_text(ctx: SkillContext) -> str:
    """The whole demo in one message (user, 4 Nov 2026): for each product, numbered steps the associate runs one by
    one: what to show (their need first, the wow), its proof, how to show it, and the line to say."""
    best_lab, labs, prods = _guide_labs(ctx)
    words = {"wow": ctx.t("guide_tag_wow"), "then": ctx.t("guide_tag_then"), "more": ctx.t("guide_tag_more")}
    blocks = [ctx.t("guide_head")]
    for lab in labs[:3]:
        p = prods.get(lab)
        if p is None:
            continue
        steps = await guide_steps(ctx, p, _other_for(best_lab, lab, labs, prods),
                                  GUIDE_STEPS["best" if lab == best_lab else "other"], ctx.lang, ctx.clang)
        if not steps:
            continue
        head = f"*{lab} · {C.product_name(ctx, p)}*" + (f" — _{ctx.t('best_pick')}_" if lab == best_lab else "")
        lines = [head]
        for i, st in enumerate(steps, 1):
            proof = f" ({st['proof']})" if st.get("proof") else ""
            lines.append(f"{ctx.reg.num(i, 'guide:step_no')}. *{words.get(st['tag'], '')}:* {st['title']}{proof}")
            if st.get("do"):
                lines.append(f"   ▸ {st['do']}")
            if st.get("say") and st["tag"] == "wow":
                lines.append(f'   _"{st["say"]}"_')
        blocks.append("\n".join(lines))
    blocks.append(ctx.t("guide_tail"))
    text = "\n\n".join(blocks)
    if len(text) > 1000:  # WhatsApp's limit with buttons: the lines to say go first, then the other products' how-to
        text = "\n".join(ln for ln in text.split("\n") if not ln.strip().startswith('_"'))
    if len(text) > 1000:
        keep, out = True, []
        for ln in text.split("\n"):
            if re.match(r"^\*[A-E] · ", ln):  # a product's block: keep the how-to only for the best pick
                keep = ln.startswith(f"*{best_lab} ")
            if keep or not ln.strip().startswith("▸"):
                out.append(ln)
        text = "\n".join(out)
    return text[:1020]


async def demo_guide_card(ctx: SkillContext) -> Card | None:
    """The demo guide card, in the associate's own language (user, 5 Nov 2026: it is their script, not the
    customer's): for each product on the shortlist, the demo in order (wow first, then expand). The line to say
    stays in the customer's language."""
    from chanakya.skills import fitview as FV

    if not ctx.session.option_labels:
        return None
    best_lab, labs, prods = _guide_labs(ctx)
    cols = []
    for lab in labs:
        p = prods[lab]
        if p is None:
            continue
        steps = await guide_steps(ctx, p, _other_for(best_lab, lab, labs, prods),
                                  GUIDE_STEPS["best" if lab == best_lab else "other"], ctx.lang, ctx.clang)
        if steps:
            cols.append({"label": lab, "title": C.product_name(ctx, p), "image": p.image,
                         "best": lab == best_lab, "steps": steps})
    chips = FV.home_chips(ctx, ctx.lang) + meaning_labels(ctx, ctx.lang)
    if not cols:
        return None
    return Card(template="demo", lang=ctx.lang, data={"products": cols[:3], "chips": chips[:5]})  # type: ignore[arg-type]


def _demo_extra(ctx: SkillContext, st: dict, head: str, best_lab: str, bp, has_next: bool):
    """A demo step from the products' own highlights ('For the elders: dialogue clearer on B, it has ClearAudio+; try
    it on B, then on A'), or the distance check on the best pick when the sizes are the same."""
    from chanakya.brain import highlights as HL

    s = ctx.session
    if st["kind"] == "distance":
        ft = P.number(s.home, "seat_distance", ctx.pack)
        size = bp.spec("size_in")
        say = ctx.tc("demo_distance_one_say", best=C.product_name(ctx, bp))
        why = f"{head} · " + ctx.t("demo_distance_one_why", ft=ctx.reg.num(ft, "card:seat_distance", "ft"),
                                   size=ctx.reg.num(size, f"spec:{bp.sku}:size_in", "inch") if size else "")
        return coach_reply(ctx, say, why, C.known_cites(ctx), _demo_buttons(ctx, has_next), do="do_demo")
    on_p = ctx.facts.product_sync(s.option_labels.get(st["on"]) or bp.sku)
    other_p = ctx.facts.product_sync(s.option_labels[st["other"]]) if s.option_labels.get(st.get("other") or "") else None
    pts = [x for x in HL.points(on_p, s.home, s.extra.get("wants") or {}, ctx.pack, n=10) if x.theme == st["theme"]]
    th = HL.theme(ctx.pack.category, st["theme"])
    if not pts or th is None:
        return coach_reply(ctx, ctx.tc("demo_feature_say", what=_step_title(ctx, st, ctx.clang), best=C.product_name(ctx, on_p),
                                       other=C.product_name(ctx, other_p or on_p)), head, C.known_cites(ctx),
                           _demo_buttons(ctx, has_next), do="do_demo")
    pt = pts[0]
    proof = ctx.reg.raw(pt.proof, f"highlight:{on_p.sku}")
    say = ctx.reg.static(HL.say(pt, ctx.clang))
    if other_p is not None:
        say += " " + ctx.tc("demo_hl_cmp", on=C.product_name(ctx, on_p), other=C.product_name(ctx, other_p))
    how = HL.demo_text(th, ctx.lang, alone=other_p is None)
    why = f"{head} · " + (f"{how} · " if how else "") + ctx.t("demo_hl_why", on=st["on"], proof=proof)
    return coach_reply(ctx, say, why, C.known_cites(ctx), _demo_buttons(ctx, has_next), do="do_demo")


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
    opts = current_options(ctx)
    for lab, sku in sorted(s.option_labels.items()):
        p = ctx.facts.product_sync(sku)
        o = opts.by_label(lab) if opts else None
        if o is not None and o.product.sku == sku:
            reason = option_reason(ctx, o, opts, ctx.lang)
        else:
            role = {"value": "value_fit", "step": "step_up"}.get(state.option_role(s, lab))
            reason = _reason(ctx, role, ctx.lang) if role else ""
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
    msg = coach_reply(ctx, ctx.tc(key), ctx.t("kyun_prefer"), cites, [], do="do_prefer")
    msg.kind = "list"
    msg.list_button = truncate_title(ctx.t("list_prefer"))
    msg.sections = [ListSection(rows=rows)]
    msg.allowed_numbers = ctx.reg.allowed()
    return ctx.reply(msg)


async def none_fit(ctx: SkillContext) -> Reply:
    btns = [ctx.b("b_rr_lower", ctx.sid("rerank", "lower")), ctx.b("b_rr_higher", ctx.sid("rerank", "higher")),
            ctx.b("b_rr_brand", ctx.sid("rerank", "brand"))]
    ctx.ev("options_rejected", shown=list(ctx.session.option_labels.values()))
    return ctx.reply(coach_reply(ctx, ctx.tc("bolo_missing"), ctx.t("kyun_missing"), C.known_cites(ctx), btns, do="do_missing"))


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
    return coach_reply(ctx, say, why, cites, _demo_buttons(ctx, has_next), do="do_demo")


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
    return ctx.reply(coach_reply(ctx, say, why, C.known_cites(ctx), btns[:3], do="do_offer_line"))


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
    from chanakya.brain import highlights as HL

    for pt in HL.differences(higher, lower, home, ctx.session.extra.get("wants") or {}, pack):
        if pt.need and len(out) < 2:  # only what answers their need: 'adds ClearAudio+ for the elders'
            out.append(_hl_text(ctx, pt, higher, ctx.lang))  # the line already says "Adds:"
    return out[:2]


def _lower1(s: str) -> str:
    """'Smooth cricket' → 'smooth cricket' inside a list ('HDR10+', 'OLED' stay as they are)."""
    return s[0].lower() + s[1:] if len(s) > 1 and s[0].isupper() and s[1].islower() else s


async def upgrade_view(ctx: SkillContext) -> Reply:
    """'Is the extra money worth it?': each step up, what it costs (and per day over 5 years), and what it really
    adds for this home. When it adds nothing for them, it says so: the honest answer that wins trust."""
    s = ctx.session
    if not state.price_gate_open(s, ctx.pack) or len(s.option_labels) < 2:
        return await prefer(ctx)
    picks = sorted(((lab, ctx.facts.product_sync(sku)) for lab, sku in s.option_labels.items()), key=lambda x: x[1].price)
    blocks = [ctx.t("upg_head")]
    for (la, a), (lb, b) in zip(picks, picks[1:], strict=False):
        diff = b.price - a.price
        if diff <= 0:
            continue
        src = f"calc:upgrade:{a.sku}:{b.sku}"
        per_day = ctx.reg.inr(max(1, round(diff / (5 * 365))), src)
        gains = _gains(ctx, a, b)
        blocks.append("\n".join([  # one block per step up: the two, the money (and per day), what it adds for them
            ctx.t("upg_step", a=la, b=lb, pa=C.product_name(ctx, a), pb=C.product_name(ctx, b)),
            ctx.t("upg_cost", diff=ctx.reg.inr(diff, src), day=per_day),
            ctx.t("upg_adds", what=", ".join([gains[0]] + [_lower1(g) for g in gains[1:]])) if gains
            else ctx.t("upg_nothing", lower=la)]))
    ctx.ev("upgrade_check", options=[lab for lab, _ in picks])
    from chanakya.core.coach import do_block

    blocks = blocks[:4] + [do_block(ctx, "do_upgrade")]
    btns = [ctx.b("b_they_chose", ctx.sid("prefer")), ctx.b("b_offers_on", ctx.sid("ofs"))]
    return ctx.reply(info_reply(ctx, "\n\n".join(blocks), btns))
