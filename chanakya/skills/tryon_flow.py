"""Ghar Mein Dekho glue (Section 17): photo in → instant ack → background try-on → image to the associate."""

from __future__ import annotations

import asyncio
import io

from PIL import Image

from chanakya.ai.schemas import ImageRead
from chanakya.brain import packs as P
from chanakya.brain.fit import fit, tv_size_range
from chanakya.brain.options import candidate_set
from chanakya.channel.limits import truncate
from chanakya.core import repo, state
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import CUSTOMER_PHONE_LANG, Button, ListRow, ListSection, OutMessage, Reply
from chanakya.log import get_log
from chanakya.skills import common as C
from chanakya.skills import product_view as PV
from chanakya.skills.base import SkillContext
from chanakya.tryon import TryonProduct, TryonRequest, run

log = get_log("tryon_flow")
OLD_TV_SIZES = (24, 32, 40, 43, 50, 55, 65)
TRYON_FIELDS = set(getattr(TryonProduct, "__dataclass_fields__", {}))
WALL_WIDTHS = {"w7": 7, "w9": 9, "w11": 11, "w13": 13}
WALL_TITLES = {"w7": "6-8 ft", "w9": "8-10 ft", "w11": "10-12 ft", "w13": "12 ft+"}


PLACEABLE = ("tv", "ac", "wm", "fridge")


# ---------------------------------------------------------------- what the customer is considering
def interested(ctx: SkillContext) -> list[str]:
    """Products in play, in a sensible order: options B, A, C, then labels scanned / compared / picked."""
    s = ctx.session
    best = state.best_label(s)
    out = [s.option_labels[k] for k in (best, "A", "B", "C") if s.option_labels.get(k)]
    out = list(dict.fromkeys(out))
    out += s.extra.get("interested", [])
    if s.chosen_sku:
        out.insert(0, s.chosen_sku)
    seen, res = set(), []
    for sku in out:
        p = ctx.facts.product_sync(sku)
        if p and p.category in PLACEABLE and sku not in seen:
            seen.add(sku)
            res.append(sku)
    return res


def add_interest(ctx: SkillContext, *skus: str) -> None:
    lst = ctx.x.setdefault("interested", [])
    for sku in skus:
        if sku and sku not in lst:
            lst.append(sku)


# ---------------------------------------------------------------- entry points
async def on_room_photo(ctx: SkillContext, data: bytes, filename: str | None) -> Reply:
    """Photo in. From 'Ghar mein dekho': draw now (one short note while the room is read). During the questions:
    only read the room in the background and keep the questions going; the picture comes with the options, when
    the associate taps 'Ghar mein dekho' (never an image dropped into the middle of the questions)."""
    s = ctx.session
    s.awaiting = None
    jpeg = _to_jpeg(data)
    await repo.media_put(s.id, "room_photo_raw", jpeg, "image/jpeg", ctx.now, days=1)
    s.extra["room_photo_name"] = filename
    if s.extra.pop("tryon_after_photo", False):
        s.extra["tryon_req"] = {"mode": "auto"}
        return ctx.reply(info_reply(ctx, ctx.t("tryon_drawing")), background=["tryon"])
    nxt = C.next_question_or_options(ctx, footer=ctx.t("footer_photo_saved"))
    return ctx.reply(nxt, background=["room_read"])


def _to_jpeg(data: bytes, max_side: int = 1600) -> bytes:
    """Upright (phone rotation applied) and at most 1600 px: faster to read, and drawn the right way up."""
    try:
        from PIL import ImageOps

        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        if max(im.size) > max_side:
            k = max_side / max(im.size)
            im = im.resize((round(im.width * k), round(im.height * k)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return data


async def tryon_request(ctx: SkillContext) -> Reply:
    """'Ghar mein dekho': photo first if there is none (two answers only), then the menu of what to show."""
    s = ctx.session
    raw = await repo.media_latest(s.id, "room_photo_raw")
    if raw is None:
        s.extra["tryon_after_photo"] = True
        return ctx.reply(C.photo_question(ctx))
    return await choose(ctx, "auto", None)  # straight to the picture (the best pick + the next size); more via 'Aur dikhao'


async def menu(ctx: SkillContext) -> Reply:
    """Ready-made choices, built from what the customer is considering (no thinking needed by the associate)."""
    s = ctx.session
    skus = interested(ctx)
    if not skus:
        skus = [p.sku for p in pick_products(ctx) if p]
    if len(skus) <= 1:
        return await choose(ctx, "auto", None)
    prods = [ctx.facts.product_sync(x) for x in skus]
    rows = []
    best = prods[0]
    rows.append(_row(ctx, "best", ctx.t("tm_best"), best.model))
    same = [p for p in prods[1:] if p.category == best.category and p.spec("size_in") != best.spec("size_in")]
    if best.category == "tv" and same:
        a = ctx.reg.num(best.spec("size_in"), f"spec:{best.sku}:size_in")
        b = ctx.reg.num(same[0].spec("size_in"), f"spec:{same[0].sku}:size_in")
        rows.append(_row(ctx, "size", ctx.t("tm_size", a=a, b=b), ctx.t("tm_size_d")))
    if len(s.option_labels) >= 2:
        rows.append(_row(ctx, "all", ctx.t("tm_all"), " · ".join(sorted(s.option_labels))))
    cats = list(dict.fromkeys(p.category for p in prods))
    if len(cats) >= 2:
        labels = " + ".join(ctx.pick(ctx.packs.get(c).label) for c in cats[:3])
        models = ", ".join(next(p.model for p in prods if p.category == c) for c in cats[:3])
        rows.append(_row(ctx, "combo", ctx.t("tm_combo", cats=labels), models))
    for p in prods[1:]:
        if len(rows) >= 10:
            break
        rows.append(_row(ctx, f"sku:{p.sku}", p.model, ctx.t("tm_one_d")))
    return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("tm_body"), list_button=ctx.t("tm_button"),
                                sections=[ListSection(rows=rows[:10])], allowed_numbers=ctx.reg.allowed()))


def _row(ctx: SkillContext, mode: str, title: str, desc: str | None) -> ListRow:
    t = truncate(title, 24)
    d = truncate(desc, 72) if desc else None
    ctx.reg.static(t + " " + (d or ""))
    return ListRow(id=ctx.sid("tmode", *mode.split(":")), title=t, description=d)


async def choose(ctx: SkillContext, mode: str, arg: str | None) -> Reply:
    ctx.x["tryon_req"] = {"mode": mode, "sku": arg}
    return ctx.reply(background=["tryon"])


# ---------------------------------------------------------------- what to draw for each choice
def pick_products(ctx: SkillContext) -> list:
    """Two sizes to show: options B and C if shown, else the best fit at the two ends of the size band."""
    s = ctx.session
    pack = ctx.pack
    f = ctx.facts
    if state.best_sku(s):
        out = [f.product_sync(state.best_sku(s))]
        c = s.option_labels.get("C")
        if c and f.product_sync(c).spec("size_in") != out[0].spec("size_in"):
            out.append(f.product_sync(c))
        return out
    products = candidate_set(f.products_sync(pack.category), s.home)
    g = f.snap.guides
    rng = tv_size_range(s.home, pack, g) if pack.category == "tv" else None
    sizes = sorted({p.spec("size_in") for p in products if p.spec("size_in")})
    if rng:
        sizes = [z for z in sizes if rng[0] <= z <= rng[1]] or sizes
    picks = []
    for z in [sizes[0], sizes[-1]] if len(sizes) > 1 else sizes[:1]:
        at = [p for p in products if p.spec("size_in") == z]
        if at:
            picks.append(max(at, key=lambda p: (fit(p, s.home, pack, g).score, -p.price)))
    return picks


def _plans(ctx: SkillContext, mode: str, sku: str | None) -> list[list[tuple]]:
    """One list per image; each item (product, solid)."""
    s = ctx.session
    f = ctx.facts
    skus = interested(ctx)
    prods = [f.product_sync(x) for x in skus]
    if mode == "sku" and sku and f.product_sync(sku):
        return [[(f.product_sync(sku), True)]]
    if mode == "best" and prods:
        return [[(prods[0], True)]]
    if mode == "size" and prods:
        best = prods[0]
        other = next((p for p in prods[1:] if p.category == best.category and p.spec("size_in") != best.spec("size_in")),
                     None)
        return [[(best, True)] + ([(other, False)] if other else [])]
    if mode == "all" and s.option_labels:
        return [[(f.product_sync(s.option_labels[k]), True)] for k in ("A", "B", "C") if s.option_labels.get(k)]
    if mode == "combo" and prods:
        firsts = {}
        for p in prods:
            firsts.setdefault(p.category, p)
        return [[(p, True) for p in firsts.values()]]
    picks = [p for p in pick_products(ctx) if p]
    return [[(p, i == 0) for i, p in enumerate(picks[:2])]] if picks else []


def _label(ctx: SkillContext, p) -> str:
    size = p.spec("size_in")
    if size:
        name = " ".join(w for w in p.model.split() if w != str(int(size)))
        return f'{ctx.reg.num(size, f"spec:{p.sku}:size_in")}" · {name}'
    return p.model


async def _photo(p) -> bytes | None:
    try:
        from chanakya.render.renderer import image_bytes

        return await asyncio.wait_for(image_bytes(p.image), timeout=6)
    except Exception:
        return None


async def _room(ctx: SkillContext):
    """(raw photo row, ImageRead | None) — read once per photo and cached in the session."""
    s = ctx.session
    raw = await repo.media_latest(s.id, "room_photo_raw")
    if raw is None:
        return None, None
    cached = s.extra.get("room_read") or {}
    if cached.get("media_id") == raw.id:
        return raw, (ImageRead.model_validate(cached["read"]) if cached.get("read") else None)
    read = None
    try:
        read = await ctx.ai.read_image(raw.data, s.extra.get("room_photo_name"), hint="room")
        if read.kind != "room":
            read = None
    except Exception:
        log.exception("room_read_failed")
    s.extra["room_read"] = {"media_id": raw.id, "read": read.model_dump() if read else None}
    s.extra.pop("oldtv_asked", None)
    return raw, read


async def prefetch(ctx: SkillContext) -> Reply:
    """Background: read the room photo while the associate looks at the menu (so the drawing is instant)."""
    await _room(ctx)
    return ctx.reply()


async def job(ctx: SkillContext) -> Reply:
    """Background job: draw the chosen products on the customer's photo, to true scale (target < 5 s)."""
    import time

    from chanakya.app import diag

    t0 = time.perf_counter()
    s = ctx.session
    req_spec = s.extra.get("tryon_req") or {"mode": "auto"}
    raw, read = await _room(ctx)
    if raw is None:
        return ctx.reply()
    t_read = int((time.perf_counter() - t0) * 1000)
    plans = [pl for pl in _plans(ctx, req_spec.get("mode", "auto"), req_spec.get("sku")) if pl]
    if not plans:
        return ctx.reply(info_reply(ctx, ctx.t("tryon_failed")))
    # A TV already on that wall is the best size reference: ask its size once (one tap), then draw.
    wants_tv = any(p.category == "tv" for pl in plans for p, _ in pl)
    old_tv = P.number(s.home, "existing_tv_in")
    osv = s.home.slots.get("old_size")
    if old_tv is None and osv and osv[-1].number and osv[-1].source in ("voice", "text"):
        old_tv = osv[-1].number  # an exact size the associate said: a scale reference for the old TV in the photo
    has_old_tv = bool(read and any(b.label == "existing_tv" for b in read.boxes))
    if wants_tv and has_old_tv and old_tv is None and not s.extra.get("oldtv_asked"):
        s.extra["oldtv_asked"] = True
        rows = [ListRow(id=ctx.sid("oldtv", str(n)), title=f'{n}"') for n in OLD_TV_SIZES]
        rows.append(ListRow(id=ctx.sid("oldtv", "none"), title=ctx.t("oldtv_none")))
        for r in rows:
            ctx.reg.static(r.title)
        return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("oldtv_ask"),
                                    list_button=ctx.t("list_choose"), sections=[ListSection(rows=rows)],
                                    allowed_numbers=ctx.reg.allowed()))
    mount = "stand" if P.value(s.home, "mounting") == "stand" else "wall"
    dist = P.number(s.home, "seat_distance", ctx.pack)
    msgs, drawn = [], []
    for pl in plans:
        tps = []
        for p, solid in pl:
            extra = {}
            if "dims_text" in TRYON_FIELDS:
                extra["dims_text"] = PV.dims_text(ctx, p)
            if "mount" in TRYON_FIELDS:
                extra["mount"] = mount
            if p.category == "fridge":
                extra["door"] = p.spec("door")
            if solid:  # the real product is drawn from its catalogue photo when that cuts out cleanly
                extra["photo"] = await _photo(p)
            tps.append(TryonProduct(sku=p.sku, category=p.category, width_mm=p.spec("width_mm"),
                                    height_mm=p.spec("height_mm"), label=_label(ctx, p), solid=solid, **extra))
        only_tv = all(p.category == "tv" for p, _ in pl)
        def dist_text(lang: str, only_tv: bool = only_tv) -> str | None:
            return (ctx.t("tryon_distance", lang, ft=ctx.reg.num(dist, "card:seat_distance"))
                    if dist and only_tv else None)

        req = TryonRequest(photo=raw.data, read=read, products=tps, wall_width_ft=P.number(s.home, "wall_width_ft"),
                           footer_text=ctx.t("tryon_footer", "en"),
                           niche_width_cm=P.number(s.home, "niche_width_cm") or P.number(s.home, "space_width"),
                           distance_text=dist_text("en"),
                           existing_tv_size_in=old_tv)
        res = await run(req, editor=None, harmonise=False, timeout=12)  # composite only: < 5 s end to end
        if res.blurred_photo and not drawn:
            await repo.media_put(s.id, "room_photo", res.blurred_photo, "image/jpeg", ctx.now)
        if res.needs_wall_width:
            rows = [ListRow(id=ctx.sid("wallw", k), title=WALL_TITLES[k]) for k in WALL_WIDTHS]
            for r in rows:
                ctx.reg.static(r.title)
            diag.mark("tryon_last", {"mode": req_spec.get("mode"), "needs_wall_width": True, "read_ms": t_read})
            return ctx.reply(OutMessage(to=ctx.actor_wa_id, kind="list", body=ctx.t("wall_width_ask"),
                                        list_button=ctx.t("list_choose"), sections=[ListSection(rows=rows)],
                                        allowed_numbers=ctx.reg.allowed()))
        if not res.image:
            diag.mark("tryon_last", {"mode": req_spec.get("mode"), "error": (res.error or "no image")[:160]})
            continue
        await repo.media_put(s.id, "tryon", res.image, "image/jpeg", ctx.now)
        if res.hero and res.hero_sku:  # the clean picture for the Family Card
            await repo.media_put(s.id, "tryon_hero", res.hero, "image/jpeg", ctx.now, days=2)
            s.extra["tryon_hero_sku"] = res.hero_sku
        drawn.append([p.sku for p, _ in pl])
        msgs.append(_tryon_message(ctx, pl, res.image))
    ms = int((time.perf_counter() - t0) * 1000)
    from chanakya.app import memory

    diag.mark("tryon_last", {"mode": req_spec.get("mode"), "images": len(msgs), "read_ms": t_read, "total_ms": ms,
                             "mem_mb": memory.used_mb()})
    if not msgs:
        btns = [ctx.b("b_other_wall", ctx.sid("photo_yes")), ctx.b("b_go", ctx.sid("go"))]
        return ctx.reply(info_reply(ctx, ctx.t("tryon_failed"), btns))
    s.tryon_done = True
    s.extra["tryon_skus"] = drawn[0]
    ctx.ev("tryon_sent", skus=[x for d in drawn for x in d], verified=False, ms=ms, mode=req_spec.get("mode"))
    ctx.ev("latency", latency_ms=ms, phase="tryon")
    return ctx.reply(*msgs)


def _tryon_message(ctx: SkillContext, pl: list[tuple], image: bytes) -> OutMessage:
    prods = [p for p, _ in pl]
    sizes = [ctx.reg.num(p.spec("size_in"), f"spec:{p.sku}:size_in") for p in prods if p.spec("size_in")]
    if len(prods) == 2 and len(sizes) == 2 and prods[0].category == prods[1].category:
        say = ctx.t("tryon_bolo", ctx.clang, a=sizes[0], b=sizes[1])
        btns = [Button(id=ctx.sid("tpick", p.sku), title=ctx.t("tryon_like", size=z)[:20]) for p, z in zip(prods, sizes, strict=True)]
    elif len(prods) >= 2:
        say = ctx.t("tryon_bolo_combo", ctx.clang)
        btns = [ctx.b("b_like_both", ctx.sid("tpick", prods[0].sku))]
    else:
        p = prods[0]
        say = (ctx.t("tryon_bolo_one", ctx.clang, a=sizes[0]) if sizes
               else ctx.t("tryon_bolo_item", ctx.clang, product=C.product_name(ctx, p)))
        btns = [ctx.b("b_like_this", ctx.sid("tpick", p.sku))]
    btns = (btns + [ctx.b("b_show_more", ctx.sid("tmenu")), ctx.b("b_go", ctx.sid("go"))])[:3]
    cites = C.known_cites(ctx, ["seat_distance", "room_size", "niche_width_cm"])
    if not cites:
        m = info_reply(ctx, f'{ctx.t("label_bolo")}: "{say}"', btns)
    else:
        m = coach_reply(ctx, say, ctx.t("kyun_tryon"), cites, btns)
    m.image_bytes = image
    m.kind = "buttons"
    return m


async def set_old_tv(ctx: SkillContext, size: str) -> Reply:
    s = ctx.session
    if size.isdigit():
        P.set_slot(s.home, None, "existing_tv_in", "num", float(size), "tap", ctx.now)
    return ctx.reply(background=["tryon"])


async def set_wall_width(ctx: SkillContext, key: str) -> Reply:
    ft = WALL_WIDTHS.get(key)
    if ft:
        P.set_slot(ctx.session.home, None, "wall_width_ft", "num", float(ft), "tap", ctx.now)
    return ctx.reply(background=["tryon"])


async def customer_photo(ctx: SkillContext) -> Reply:
    s = ctx.session
    data, _ = await ctx.svc.channel.download_media(ctx.inbound.media_id)
    await repo.media_put(s.id, "room_photo_raw", _to_jpeg(data), "image/jpeg", ctx.now, days=1)
    s.extra["room_photo_name"] = ctx.svc.media_name(ctx.inbound.media_id)
    alang = (await repo.staff_by_wa(s.associate_wa_id)).lang
    s.extra["tryon_req"] = {"mode": "auto"}
    return ctx.reply(
        OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_photo_thanks", CUSTOMER_PHONE_LANG)),
        OutMessage(to=s.associate_wa_id, kind="text", body=truncate(ctx.t("cust_photo_assoc", alang), 200)),
        background=["tryon"],
    )


async def pick(ctx: SkillContext, sku: str) -> Reply:
    """'55 pasand' after the try-on: record the preference; continue to options / cost."""
    s = ctx.session
    s.extra["tryon_pick"] = sku
    add_interest(ctx, sku)
    if s.option_labels and sku in s.option_labels.values():
        from chanakya.core import state
        from chanakya.skills import complete

        if state.price_gate_open(s, ctx.pack):
            return await complete.choose_sku(ctx, sku)
    p = ctx.facts.product_sync(sku)
    size = p.spec("size_in")
    if size:
        s.home.notes.append(f"tryon_pick:{size}")
    return ctx.reply(C.next_question_or_options(ctx))
