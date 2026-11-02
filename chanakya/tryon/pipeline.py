"""Ghar Mein Dekho (spec 17): blur people → read room → true scale → place → render → (harmonise → verify)
→ annotate.

Pure image work, no DB and no number registry: the caller passes pre-formatted label strings.
Never raises: failures come back in TryonResult.error with the best image available."""

from __future__ import annotations

import asyncio
import io
import threading
import time
from dataclasses import dataclass, field

from PIL import Image, ImageOps

from chanakya.ai.schemas import ImageRead
from chanakya.tryon import annotate, harmonise, place, privacy, read_room, render, scale, verify

MAX_WIDTH = 1080
JPEG_QUALITY = 88
DEFAULT_FOOTER = "Approx. preview · to scale · TEST DATA"
# One drawing at a time: each needs ~120 MB at its peak, and a timed-out thread keeps running, so two at once
# could take the instance over its memory limit.
_HEAVY = threading.Lock()


@dataclass
class TryonProduct:
    sku: str
    category: str  # tv | ac | wm | fridge
    width_mm: float
    height_mm: float
    label: str  # e.g. '55" · Meru QLED Pro' (already formatted by caller)
    solid: bool  # True = primary (solid render), False = dashed saffron outline
    dims_text: str | None = None  # e.g. '123 × 71 cm' (pre-formatted), second line of the label
    mount: str = "wall"  # TV: wall | stand
    door: str | None = None  # fridge: single | double | bottom_freezer | side_by_side
    photo: bytes | None = field(default=None, repr=False)  # catalogue photo: the real product is drawn from it


@dataclass
class TryonRequest:
    photo: bytes  # JPEG/PNG from WhatsApp
    read: ImageRead | None  # kind "room", boxes normalised 0-1000 [ymin,xmin,ymax,xmax]; None → default geometry
    products: list[TryonProduct]  # 1–2
    wall_width_ft: float | None = None
    niche_width_cm: float | None = None
    existing_tv_size_in: float | None = None  # size of the TV already on that wall, if the associate stated it
    distance_text: str | None = None  # e.g. 'Sofa se 10 ft' (pre-formatted)
    footer_text: str = DEFAULT_FOOTER


@dataclass
class TryonResult:
    image: bytes | None = None
    blurred_photo: bytes | None = None
    verified: bool = False
    harmonised: bool = False
    needs_wall_width: bool = False
    scale_confidence: float = 0.0
    px_per_cm: float | None = None  # at the primary product (scale varies across an oblique wall)
    ms: int = 0
    error: str | None = None
    planned: list[tuple[str, tuple[int, int, int, int]]] = field(default_factory=list)  # (sku, px box) for tests
    scale_source: str | None = None  # existing_tv | wall_width | frame_width | door | ceiling | floor_tile | …
    perspective: bool = False  # True when the wall corners gave a homography
    quads: list[tuple[str, tuple[tuple[float, float], ...]]] = field(default_factory=list)  # (sku, TL TR BR BL)
    label_boxes: list[tuple[str, tuple[int, int, int, int]]] = field(default_factory=list)


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def _load(photo: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(photo))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.Resampling.LANCZOS)
    return img


@dataclass
class _Prepared:
    category: str
    placed: list[place.Placed]
    composite: Image.Image
    sc: scale.Scale
    room: read_room.Room


def _prepare(req: TryonRequest, res: TryonResult) -> _Prepared | None:
    img = _load(req.photo)
    room = read_room.parse(req.read, img.width, img.height)
    img = privacy.blur_people(img, room.boxes.get("person"))
    res.blurred_photo = _jpeg(img)
    sc = scale.estimate(room, req.wall_width_ft, req.existing_tv_size_in)
    res.scale_confidence, res.scale_source = sc.confidence, sc.source
    res.px_per_cm = sc.px_per_cm
    if sc.wall is None or sc.confidence < scale.MIN_CONFIDENCE:
        res.needs_wall_width = True
        return None
    res.perspective = sc.wall.perspective
    cats = list(dict.fromkeys((p.category or "tv") for p in req.products)) or ["tv"]
    category = cats[0]
    placed, composite = [], img
    for cat in cats:  # e.g. a TV and an AC on the same wall: each type is planned and drawn in turn
        group = place.plan(cat, [p for p in req.products if (p.category or "tv") == cat], sc.wall, room,
                           req.niche_width_cm)
        if group:
            composite = render.compose(composite, cat, group, sc.wall, room, req.niche_width_cm)
            placed += group
    if not placed:
        raise ValueError("no product with usable dimensions")
    primary = next((p for p in placed if p.solid), placed[0])
    res.px_per_cm = round(sc.wall.scale_at(primary.rect[0], primary.rect[1])[0], 4)
    res.planned = [(p.sku, p.box) for p in placed]
    res.quads = [(p.sku, p.quad) for p in placed]
    return _Prepared(category, placed, composite, sc, room)


def _heavy(fn, *args):
    with _HEAVY:
        return fn(*args)


def _annotated(req: TryonRequest, prep: _Prepared, img: Image.Image, res: TryonResult) -> bytes:
    out, boxes = annotate.annotate(img, prep.placed, req.distance_text, req.footer_text, prep.sc.wall, prep.room)
    res.label_boxes = boxes
    return _jpeg(out)


async def _run(req: TryonRequest, editor, do_harmonise: bool, res: TryonResult) -> None:
    prep = await asyncio.to_thread(_heavy, _prepare, req, res)
    if prep is None:
        return
    # keep a usable image from here on, even if a later step fails or times out
    res.image = await asyncio.to_thread(_heavy, _annotated, req, prep, prep.composite, res)
    final = prep.composite
    if do_harmonise and editor is not None and prep.category == "tv":
        edited = await harmonise.run(editor, prep.composite)
        primary = next((p for p in prep.placed if p.solid), prep.placed[0] if prep.placed else None)
        if edited is not None and primary is not None:
            ok = await asyncio.to_thread(_heavy, verify.check, prep.composite, edited, primary.box, primary.quad)
            if ok:
                final = edited.resize(prep.composite.size) if edited.size != prep.composite.size else edited
                res.harmonised = res.verified = True
    if final is not prep.composite:
        res.image = await asyncio.to_thread(_heavy, _annotated, req, prep, final, res)


async def run(req: TryonRequest, editor=None, harmonise: bool = False, timeout: float = 30) -> TryonResult:
    t0 = time.perf_counter()
    res = TryonResult()
    try:
        if not req.products:
            raise ValueError("no products to place")
        await asyncio.wait_for(_run(req, editor, harmonise, res), timeout)
    except TimeoutError:
        res.error = "timeout"
    except Exception as e:  # never block the conversation
        res.error = f"{type(e).__name__}: {e}"
    res.ms = round((time.perf_counter() - t0) * 1000)
    from chanakya.app import memory

    memory.trim()
    return res

