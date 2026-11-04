"""What each option means for THIS home, from data only (options card).

- points(): the pack benefits whose conditions hold for this product AND this customer's answers (the same
  rules the label scan uses), each with the product's own spec value as proof. Never a generic feature list.
- needs_grid(): one row per fit rule the customer's answers switched on ("Sunny room", "Every match, smooth"),
  one cell per option with that product's real value and whether it meets the need.
- home_chips(): the customer's own answers, so the card visibly starts from their home.
Every number is registered with its data source (spec / Home Card)."""

from __future__ import annotations

from chanakya.brain import packs as P
from chanakya.brain.fit import fit
from chanakya.i18n.lang import fill, pick
from chanakya.skills import product_view as PV
from chanakya.skills.base import SkillContext

BAND_KEY = {"size_band": "size_in", "ton_band": "ton"}
PLACEHOLDER_SLOT = {"distance": "seat_distance", "people": "family_size", "sqft": "room_size"}


def _value(ctx: SkillContext, p, key: str, lang: str) -> str | None:
    """A spec as short display text: numbers with units, a true flag as its name, widths in cm."""
    raw = PV.raw_value(p, key)
    if raw is None or raw == "" or raw == []:
        return None
    if key == "width_mm":
        return ctx.reg.num(round(float(raw) / 10), f"spec:{p.sku}:width_mm", "cm")
    if isinstance(raw, bool):
        return ctx.reg.static(PV.label(key, lang)) if raw else None
    if isinstance(raw, list):
        return ctx.reg.raw(", ".join(map(str, raw[:2])), f"spec:{p.sku}:{key}")
    return PV.fmt_value(ctx, p, key, lang)


def _cond_keys(cond: dict | None) -> list[str]:
    return [k for k in (cond or {}) if not k.startswith(("spec_", "in_"))]


def _proof(ctx: SkillContext, p, benefit, lang: str) -> str | None:
    """The spec that makes the benefit true for this product (the alternative it actually meets)."""
    home, pack, g = ctx.session.home, ctx.pack, ctx.facts.snap.guides
    keys = _cond_keys(benefit.when_spec)
    for alt in benefit.when_spec_any or []:
        if P.eval_spec_cond(p, home, alt, pack, g):
            keys += _cond_keys(alt)
            break
    for k in keys:
        v = _value(ctx, p, k, lang)
        if v:
            return v
    return None


def points(ctx: SkillContext, p, lang: str, n: int = 3) -> list[dict]:
    """[{text, proof}] — benefits that hold for this product and this home, strongest first. With the product they
    have now known: first, what this one adds over it (a bigger size; a highlight their model lacks)."""
    from chanakya.skills.translate import matching

    out = over_current(ctx, p)[:1]
    for b in matching(ctx, p):
        text = pick(b.card, lang) if b.card else None
        if not text or P.placeholders(text):
            continue
        out.append({"text": ctx.reg.static(text), "proof": _proof(ctx, p, b, lang)})
        if len(out) >= n:
            break
    from chanakya.brain import highlights as HL
    from chanakya.skills.translate import hl_points

    def add_hl(general: bool, upto: int) -> None:
        for pt in hl_points(ctx, p, n=6, general=general):
            if len(out) >= upto:
                break
            text = ctx.reg.static(HL.card_text(pt))
            if all((o.get("text") if isinstance(o, dict) else o) != text for o in out) and (general or pt.need):
                out.append({"text": text, "proof": ctx.reg.raw(pt.proof, f"highlight:{p.sku}")})

    add_hl(False, n)  # the product's own highlights that answer their needs (live data has few spec benefits)
    from chanakya.skills.common import asked_for

    for _, val in asked_for(ctx, lang):  # what they asked for about the product itself (picture type, brand …)
        if len(out) >= n + 2:
            break
        out.append(val)
    if len(out) < 2:
        add_hl(True, 2)  # nothing more for this home: the product's own best highlight, still its own words
    return out


def _rule_keys(rule, pack, p, ctx: SkillContext) -> list[str]:
    if rule.kind in BAND_KEY:
        return [BAND_KEY[rule.kind]]
    if rule.kind == "capacity_band":
        return ["capacity_kg" if pack.category == "wm" else "capacity_l"]
    if rule.kind == "width_fit":
        return ["width_mm"]
    if rule.tiers:
        return _cond_keys((rule.tiers[0].get("spec_all") or [{}])[0])
    alts = rule.spec_any or []
    home, g = ctx.session.home, ctx.facts.snap.guides
    met = next((a for a in alts if P.eval_spec_cond(p, home, a, pack, g)), None)
    keys = _cond_keys(met) if met else []
    for a in alts:  # not met: show the measurable one (e.g. brightness, not 'anti-glare: no')
        keys += [k for k in _cond_keys(a) if not isinstance(PV.raw_value(p, k), bool)]
    return keys


def need_title(ctx: SkillContext, rule_id: str, lang: str) -> str | None:
    pack, home = ctx.pack, ctx.session.home
    tpl = pick(pack.needs.get(rule_id) or {}, lang)
    if not tpl:
        return None
    vals = {}
    for ph in P.placeholders(tpl):
        slot = PLACEHOLDER_SLOT.get(ph)
        num = P.number(home, slot, pack) if slot else None
        if num is None:
            plain = pick(pack.needs.get(f"{rule_id}_plain") or {}, lang)
            return ctx.reg.static(plain) if plain else None
        vals[ph] = ctx.reg.num(num, f"card:{slot}")
    return ctx.reg.static(fill(tpl, **vals)) if vals else ctx.reg.static(tpl)


def needs_grid(ctx: SkillContext, options: list[tuple[str, object]], lang: str, rows: int = 4) -> list[dict]:
    """[{need, cells: [{label, value, state}]}] for the fit rules this home switched on (heaviest first).
    state: good (meets the need), near (one size off), no (does not meet it)."""
    pack, home, g = ctx.pack, ctx.session.home, ctx.facts.snap.guides
    fits = {label: fit(p, home, pack, g) for label, p in options}
    applied = []
    for rule in pack.fit_rules:
        if any(c.rule_id == rule.id for f in fits.values() for c in f.contributions):
            applied.append(rule)
    applied.sort(key=lambda r: -r.weight)
    out = []
    for rule in applied:
        title = need_title(ctx, rule.id, lang)
        if not title:
            continue
        cells = []
        for label, p in options:
            m = next((c.match for c in fits[label].contributions if c.rule_id == rule.id), None)
            value = next((v for k in _rule_keys(rule, pack, p, ctx) if (v := _value(ctx, p, k, lang))), None)
            band = rule.kind in ("size_band", "ton_band", "capacity_band")
            state = "good" if m is not None and m >= 0.99 else ("near" if band and m and m >= 0.5 else "no")
            cells.append({"label": label, "value": value or "—", "state": state})
        out.append({"need": title, "cells": cells})
        if len(out) >= rows:
            break
    from chanakya.brain import highlights as HL

    wants = ctx.session.extra.get("wants") or {}
    for th in HL.themes(pack.category):  # their needs that only the products' own highlights can show (live data)
        if len(out) >= rows:
            break
        w, need = HL.triggered(th, home, wants, pack)
        if need is None or w < HL.STRONG:
            continue
        cells, any_has = [], False
        for label, p in options:
            f = HL.found(p, pack.category).get(th.id)
            any_has = any_has or f is not None
            cells.append({"label": label, "value": ctx.reg.raw(f.proof, f"highlight:{p.sku}") if f else "—",
                          "state": "good" if f else "no"})
        title = (th.label[:1].upper() + th.label[1:] + " (asked)") if need == "as_asked" else HL.need_label(need, lang)
        if any_has and title and not any(r["need"] == title for r in out):
            out.append({"need": ctx.reg.static(title), "cells": cells})
    return out


def home_chips(ctx: SkillContext, lang: str, n: int = 4) -> list[str]:
    """The customer's answers in ask order (the required ones first), short."""
    pack, home = ctx.pack, ctx.session.home
    order = [s for s in pack.required_slots] + [s for s in pack.order if s not in pack.required_slots]
    out = []
    for sid in order:
        sd = pack.slot(sid)
        vals = P.values(home, sid)
        if not sd or not vals or sid == "trigger":
            continue
        sv = home.slots[sid][-1]
        if sv.number is not None and sd.number and sv.source in ("voice", "text"):
            unit = {"seat_distance_ft": "ft", "room_size_sqft": "sq ft", "fridge_width_cm": "cm"}.get(sd.number, "")
            out.append(ctx.reg.num(sv.number, f"card:{sid}", unit))
        else:
            from chanakya.skills.common import value_title

            out.append(ctx.reg.static(value_title(sd, vals[0], lang)))
        if len(out) >= n:
            break
    return out


def over_current(ctx: SkillContext, p) -> list[dict]:
    """What this product adds over the one they have now, from the data: a bigger size than theirs; and, when their
    model is in the catalogue, a highlight it lacks. Nothing when it adds nothing."""
    from chanakya.brain import highlights as HL

    home = ctx.session.home
    ex = home.existing or {}
    out = []
    size, old = p.spec("size_in"), ex.get("size_in")
    if size and old and float(size) > float(old):
        out.append({"text": ctx.reg.static("Bigger than your current TV"),
                    "proof": ctx.reg.num(size, f"spec:{p.sku}:size_in", '"') + " vs " + ctx.reg.num(old, "note:existing", '"')})
    oldp = ctx.facts.product_sync(ex["sku"]) if ex.get("sku") else None
    if oldp is not None:
        diff = HL.differences(p, oldp, home, ctx.session.extra.get("wants") or {}, ctx.pack)
        if diff:
            out.append({"text": ctx.reg.static("New vs yours: " + HL.card_text(diff[0]).lower()),
                        "proof": ctx.reg.raw(diff[0].proof, f"highlight:{p.sku}")})
    return out
