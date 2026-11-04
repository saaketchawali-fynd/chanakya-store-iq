"""Side by side (user, 4 Nov 2026): the shortlisted products compared for an informed decision, one English card.

Each product in a column: on the customer's own wall when there is a room photo (each drawn alone, true size), else
its product photo. Under the pictures, a table in sections, every cell from the product data or their Home Card:
  For your needs  — each need their answers (or the note) switched on, and how each product meets it (its proof)
  Benefits        — what each means for them, in daily-life words, with the feature that proves it
  Features        — the key specs side by side; the better value marked where the data says so
  Cost            — the price, with the MRP saving (only once the price is open: after the demo, or a label scan)
  Offers          — each product's live offer, or plainly none
"""

from __future__ import annotations

from chanakya.core import state
from chanakya.core.contracts import Card
from chanakya.skills import common as C
from chanakya.skills import product_view as PV
from chanakya.skills.base import SkillContext

MAX_COLS = 3
FEATURE_ROWS = 6


def _better(ctx: SkillContext, products: list, key: str) -> int | None:
    """Index of the product the data says is better on this spec (the same rule as the label compare)."""
    from chanakya.skills.translate import _better as better2

    if len(products) != 2:
        return None
    w = better2(PV.raw_value(products[0], key), PV.raw_value(products[1], key), key)
    return {"a": 0, "b": 1}.get(w) if w else None


def build(ctx: SkillContext, labs: list[str], photos: dict[str, bytes] | None = None) -> Card | None:
    """The card for these option labels (best first). `photos`: {label: their room with that product drawn in}."""
    from chanakya.skills import fitview as FV
    from chanakya.skills import recommend as R

    s, pack = ctx.session, ctx.pack
    labs = [lab for lab in labs if s.option_labels.get(lab)][:MAX_COLS]
    prods = [ctx.facts.product_sync(s.option_labels[lab]) for lab in labs]
    if len([p for p in prods if p]) < 2:
        return None
    best = state.best_label(s)
    price_open = state.price_gate_open(s, pack)
    photos = photos or {}
    with ctx.as_lang("en"):
        cols = []
        for lab, p in zip(labs, prods, strict=True):
            size, w = p.spec("size_in"), p.spec("width_mm")
            cols.append({
                "label": lab, "title": C.product_name(ctx, p), "best": lab == best,
                "photo": photos.get(lab) or p.image, "is_room": lab in photos,
                "size": ctx.reg.num(size, f"spec:{p.sku}:size_in", "inch") if size else "",
                "width": ctx.reg.num(round(float(w) / 10), f"spec:{p.sku}:width_mm", "cm") if w else "",
            })
        sections = []
        labelled = list(zip(labs, prods, strict=True))
        three = len(prods) >= 3  # three columns: fewer rows, so the whole table fits one card
        needs = FV.needs_grid(ctx, labelled, "en", rows=3 if three else 4)
        if needs:
            sections.append({"title": "For your needs", "rows": [
                {"label": r["need"], "cells": [{"value": c["value"], "state": c["state"]} for c in r["cells"]]}
                for r in needs]})
        ben = []
        for p in prods:
            pts = [x for x in FV.points(ctx, p, "en", 3) if isinstance(x, dict) and x.get("text")]
            ben.append([{"text": x["text"], "proof": x.get("proof")} for x in pts[: 2 if three else 3]])
        if any(ben):
            sections.append({"title": "Benefits for you", "rows": [{"label": "", "cells": [
                {"list": b, "state": "list"} for b in ben]}]})
        feats = []
        for key in PV.KEY_SPECS.get(pack.category, []):
            if len(feats) >= (4 if three else FEATURE_ROWS):
                break
            vals = [PV.fmt_value(ctx, p, key, "en") for p in prods]
            if not any(vals):
                continue
            win = _better(ctx, prods, key)
            feats.append({"label": ctx.reg.static(PV.label(key, "en")), "cells": [
                {"value": v or "—", "state": "good" if win == i else ("info" if v else "no")} for i, v in enumerate(vals)]})
        if feats:
            sections.append({"title": "Features", "rows": feats})
        cost = []
        if price_open:
            cost.append({"label": "Price", "cells": [{"value": ctx.reg.inr(p.price, p.price_source), "state": "price"}
                                                     for p in prods]})
            if any(p.mrp and p.mrp > p.price for p in prods):
                cost.append({"label": "MRP saving", "cells": [
                    {"value": ctx.reg.inr(p.mrp - p.price, f"calc:mrp_saving:{p.sku}") if p.mrp and p.mrp > p.price else "—",
                     "state": "good" if p.mrp and p.mrp > p.price else "no"} for p in prods]})
        else:
            cost.append({"label": "Price", "cells": [{"value": "After the demo", "state": "no"} for _ in prods]})
        cost.append({"label": "Offer", "cells": [
            {"value": (o := PV.offer_line(ctx, p, "en"))["text"], "state": "no" if o["none"] else "offer"} for p in prods]})
        sections.append({"title": "Cost and offers", "rows": cost})
        chips = FV.home_chips(ctx, "en") + R.meaning_labels(ctx, "en")
    ctx.ev("side_by_side_sent", skus=[p.sku for p in prods], in_room=sorted(photos), sections=len(sections))
    return Card(template="homecompare", lang="en",  # type: ignore[arg-type]
                data={"cols": cols, "sections": sections, "chips": chips[:5], "in_room": bool(photos)})


def summary(ctx: SkillContext, labs: list[str]) -> str:
    """The message with the card: each product's top reason for them, as bullets (the associate's language)."""
    from chanakya.skills import recommend as R

    s = ctx.session
    opts = R.current_options(ctx)
    blocks = [ctx.t("sbs_head")]
    for lab in labs[:MAX_COLS]:
        o = opts.by_label(lab) if opts else None
        if o is not None:
            blocks.append(R.option_block(ctx, o, opts))
    if not state.price_gate_open(s, ctx.pack):
        blocks.append(ctx.t("sbs_tail_locked"))
    else:
        blocks.append(ctx.t("sbs_tail"))
    return "\n\n".join(blocks)
