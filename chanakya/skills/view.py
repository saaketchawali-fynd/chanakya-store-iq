"""Viewing comfort from their sofa (user, 4 Nov 2026): how much of the view each screen fills at the customer's own
distance, as a cinema seat. Plain geometry from two facts (their distance, the screen's width from its size):
angle = 2·atan(width / 2 / distance). Cinema guidance: about 30° (SMPTE) to 40° (THX) is the best seats."""

from __future__ import annotations

import math

from chanakya.brain import packs as P
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import Card, Reply
from chanakya.skills import common as C
from chanakya.skills.base import SkillContext
from chanakya.tryon.scale import tv_width_cm

SEATS = ((20, "back"), (28, "middle"), (40, "best"), (999, "front"))  # degrees → the seat it feels like


def angle(size_in: float, distance_ft: float) -> float:
    w = tv_width_cm(size_in)
    return math.degrees(2 * math.atan(w / 2 / (distance_ft * 30.48)))


def seat(deg: float) -> str:
    return next(name for top, name in SEATS if deg < top)


async def show(ctx: SkillContext) -> Reply:
    s = ctx.session
    dist = P.number(s.home, "seat_distance", ctx.pack) if ctx.pack else None
    picks = [(lab, ctx.facts.product_sync(sku)) for lab, sku in sorted(s.option_labels.items())]
    picks = [(lab, p) for lab, p in picks if p is not None and p.spec("size_in")]
    if not dist or not picks or (ctx.pack and ctx.pack.category != "tv"):
        return ctx.reply(info_reply(ctx, ctx.t("view_need"), [ctx.b("b_options", ctx.sid("options"))]))
    rows = []
    for lab, p in picks:
        size = float(p.spec("size_in"))
        deg = angle(size, dist)
        rows.append({"label": lab, "size": ctx.reg.num(size, f"spec:{p.sku}:size_in", "inch"), "model": p.short_name(),
                     "deg": ctx.reg.num(round(deg), f"calc:view:{p.sku}"), "deg_f": round(deg, 1), "seat": seat(deg),
                     "seat_text": ctx.t(f"view_seat_{seat(deg)}", "en"),
                     "dx": round(min(460.0, 300 * math.tan(math.radians(deg / 2))), 1)})
    with ctx.as_lang("en"):
        card = Card(template="view", lang="en", data={  # type: ignore[arg-type]
            "title": ctx.t("view_title"), "distance": ctx.reg.num(dist, "card:seat_distance", "ft"),
            "rows": rows, "note": ctx.t("view_note")})
    best = max(rows, key=lambda r: -abs(r["deg_f"] - 35))  # nearest to the cinema sweet spot
    say = ctx.tc("view_bolo", ft=ctx.reg.num(dist, "card:seat_distance"), label=best["label"], size=best["size"],
                 seat=ctx.t(f"view_seat_{best['seat']}", ctx.clang))
    why = ctx.t("view_kyun", lines=" · ".join(f'{r["label"]} {r["deg"]}°' for r in rows))
    ctx.ev("viewing_comfort_shown", options=[r["label"] for r in rows])
    btns = [ctx.b("b_they_chose", ctx.sid("prefer")), ctx.b("b_tryon", ctx.sid("tryon"))]
    return ctx.reply(coach_reply(ctx, say, why, C.known_cites(ctx, ["seat_distance"]), btns, card=card))
