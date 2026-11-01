"""Ghar Mein Dekho glue (Section 17): photo in → instant ack → background try-on → image to the associate."""

from __future__ import annotations

import io

from PIL import Image

from chanakya.ai.schemas import ImageRead
from chanakya.brain import packs as P
from chanakya.brain.fit import fit, tv_size_range
from chanakya.brain.options import candidate_set
from chanakya.channel.limits import truncate
from chanakya.core import repo
from chanakya.core.coach import coach_reply, info_reply
from chanakya.core.contracts import ListRow, ListSection, OutMessage, Reply
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


async def on_room_photo(ctx: SkillContext, data: bytes, filename: str | None) -> Reply:
    """Instant reply (< 2 s): ack + next question; the try-on runs in the background."""
    s = ctx.session
    s.awaiting = None
    jpeg = _to_jpeg(data)
    await repo.media_put(s.id, "room_photo_raw", jpeg, "image/jpeg", ctx.now, days=1)
    s.extra["room_photo_name"] = filename
    nxt = C.next_question_or_options(ctx, footer=ctx.t("footer_photo_got"))
    return ctx.reply(nxt, background=["tryon"])


def _to_jpeg(data: bytes) -> bytes:
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return data


def pick_products(ctx: SkillContext) -> list:
    """Two sizes to show: options B and C if shown, else the best fit at the two ends of the size band."""
    s = ctx.session
    pack = ctx.pack
    f = ctx.facts
    if s.option_labels.get("B"):
        out = [f.product_sync(s.option_labels["B"])]
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


async def job(ctx: SkillContext) -> Reply:
    """Background job body (runs under the associate's lock with a fresh ctx)."""
    s = ctx.session
    raw = await repo.media_latest(s.id, "room_photo_raw")
    if raw is None:
        return ctx.reply()
    products = [p for p in pick_products(ctx) if p]
    if not products:
        return ctx.reply(info_reply(ctx, ctx.t("tryon_failed")))
    read = None
    cached = s.extra.get("room_read") or {}
    if cached.get("media_id") == raw.id:  # same photo: don't read it twice (e.g. after the old-TV answer)
        read = ImageRead.model_validate(cached["read"]) if cached.get("read") else None
    else:
        try:
            read = await ctx.ai.read_image(raw.data, s.extra.get("room_photo_name"), hint="room")
            if read.kind != "room":
                read = None
        except Exception:
            log.exception("room_read_failed")
        s.extra["room_read"] = {"media_id": raw.id, "read": read.model_dump() if read else None}
        s.extra.pop("oldtv_asked", None)
    # A TV already on that wall is the best size reference: ask its size once (one tap), then draw.
    old_tv = P.number(s.home, "existing_tv_in")
    has_old_tv = bool(read and any(b.label == "existing_tv" for b in read.boxes))
    if has_old_tv and old_tv is None and not s.extra.get("oldtv_asked"):
        s.extra["oldtv_asked"] = True
        rows = [ListRow(id=ctx.sid("oldtv", str(n)), title=f'{n}"') for n in OLD_TV_SIZES]
        rows.append(ListRow(id=ctx.sid("oldtv", "none"), title=ctx.t("oldtv_none")))
        for r in rows:
            ctx.reg.static(r.title)
        return ctx.reply(
            OutMessage(
                to=ctx.actor_wa_id,
                kind="list",
                body=ctx.t("oldtv_ask"),
                list_button=ctx.t("list_choose"),
                sections=[ListSection(rows=rows)],
                allowed_numbers=ctx.reg.allowed(),
            )
        )
    mount = "stand" if P.value(s.home, "mounting") == "stand" else "wall"
    tps = []
    for i, p in enumerate(products[:2]):
        size = p.spec("size_in")
        if size:
            name = " ".join(w for w in p.model.split() if w != str(int(size)))
            label = f'{ctx.reg.num(size, f"spec:{p.sku}:size_in")}" · {name}'
        else:
            label = p.model
        extra = {}
        if "dims_text" in TRYON_FIELDS:
            extra["dims_text"] = PV.dims_text(ctx, p)
        if "mount" in TRYON_FIELDS:
            extra["mount"] = mount
        tps.append(
            TryonProduct(
                sku=p.sku,
                category=p.category,
                width_mm=p.spec("width_mm"),
                height_mm=p.spec("height_mm"),
                label=label,
                solid=(i == 0),
                **extra,
            )
        )
    dist = P.number(s.home, "seat_distance", ctx.pack)
    dist_text = (ctx.t("tryon_distance", ctx.clang, ft=ctx.reg.num(dist, "card:seat_distance"))
                 if dist and ctx.pack.category == "tv" else None)
    ww = P.number(s.home, "wall_width_ft")
    req = TryonRequest(
        photo=raw.data,
        read=read,
        products=tps,
        wall_width_ft=ww,
        niche_width_cm=P.number(s.home, "niche_width_cm"),
        distance_text=dist_text,
        existing_tv_size_in=old_tv,
    )
    res = await run(req, editor=ctx.ai, harmonise=bool(ctx.svc and ctx.svc.settings.harmonise_tryon))
    if res.blurred_photo:
        await repo.media_put(s.id, "room_photo", res.blurred_photo, "image/jpeg", ctx.now)
    if res.needs_wall_width:
        rows = [ListRow(id=ctx.sid("wallw", k), title=WALL_TITLES[k]) for k in WALL_WIDTHS]
        for r in rows:
            ctx.reg.static(r.title)
        return ctx.reply(
            OutMessage(
                to=ctx.actor_wa_id,
                kind="list",
                body=ctx.t("wall_width_ask"),
                list_button=ctx.t("list_choose"),
                sections=[ListSection(rows=rows)],
                allowed_numbers=ctx.reg.allowed(),
            )
        )
    if not res.image:
        return ctx.reply(info_reply(ctx, ctx.t("tryon_failed")))
    await repo.media_put(s.id, "tryon", res.image, "image/jpeg", ctx.now)
    s.tryon_done = True
    s.extra["tryon_skus"] = [p.sku for p in products[:2]]
    ctx.ev("tryon_sent", skus=[p.sku for p in products[:2]], verified=res.verified, ms=res.ms)
    ctx.ev("latency", latency_ms=res.ms, phase="tryon")
    sizes = [ctx.reg.num(p.spec("size_in"), f"spec:{p.sku}:size_in") for p in products[:2] if p.spec("size_in")]
    if len(sizes) == 2:
        say = ctx.t("tryon_bolo", ctx.clang, a=sizes[0], b=sizes[1])
    else:
        say = ctx.t("tryon_bolo_one", ctx.clang, a=sizes[0] if sizes else "")
    cites = C.known_cites(ctx, ["seat_distance", "room_size", "niche_width_cm"])
    from chanakya.skills.recommend import tryon_buttons

    btns = tryon_buttons(ctx, [p.sku for p in products[:2]])
    if not cites:
        m = info_reply(ctx, f'{ctx.t("label_bolo")}: "{say}"', btns)
    else:
        m = coach_reply(ctx, say, ctx.t("kyun_tryon"), cites, btns)
    m.image_bytes = res.image
    m.kind = "buttons"
    return ctx.reply(m)


async def set_old_tv(ctx: SkillContext, size: str) -> Reply:
    s = ctx.session
    if size.isdigit():
        P.set_slot(s.home, None, "existing_tv_in", "num", float(size), "tap", ctx.now)
    return ctx.reply(info_reply(ctx, ctx.t("room_photo_ack")), background=["tryon"])


async def set_wall_width(ctx: SkillContext, key: str) -> Reply:
    ft = WALL_WIDTHS.get(key)
    if ft:
        P.set_slot(ctx.session.home, None, "wall_width_ft", "num", float(ft), "tap", ctx.now)
    return ctx.reply(info_reply(ctx, ctx.t("room_photo_ack")), background=["tryon"])


async def tryon_request(ctx: SkillContext) -> Reply:
    s = ctx.session
    raw = await repo.media_latest(s.id, "room_photo_raw")
    if raw is None:
        s.awaiting = "room_photo"
        return ctx.reply(info_reply(ctx, ctx.t("room_photo_send")))
    return ctx.reply(info_reply(ctx, ctx.t("room_photo_ack")), background=["tryon"])


async def customer_photo(ctx: SkillContext) -> Reply:
    s = ctx.session
    data, _ = await ctx.svc.channel.download_media(ctx.inbound.media_id)
    await repo.media_put(s.id, "room_photo_raw", _to_jpeg(data), "image/jpeg", ctx.now, days=1)
    s.extra["room_photo_name"] = ctx.svc.media_name(ctx.inbound.media_id)
    alang = (await repo.staff_by_wa(s.associate_wa_id)).lang
    return ctx.reply(
        OutMessage(to=ctx.actor_wa_id, kind="text", body=ctx.t("cust_photo_thanks", s.home.customer_lang)),
        OutMessage(to=s.associate_wa_id, kind="text", body=truncate(ctx.t("cust_photo_assoc", alang), 200)),
        background=["tryon"],
    )


async def pick(ctx: SkillContext, sku: str) -> Reply:
    """'55 pasand' after the try-on: record the preference; continue to options / cost."""
    s = ctx.session
    s.extra["tryon_pick"] = sku
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
