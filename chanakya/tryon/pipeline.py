"""Ghar Mein Dekho (spec 17): blur people → read room → scale → place → (harmonise → verify) → annotate.

Pure image work, no DB and no number registry: the caller passes pre-formatted label strings.
Never raises: failures come back in TryonResult.error with the best image available."""

from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass, field

from PIL import Image, ImageOps

from chanakya.ai.schemas import ImageRead
from chanakya.tryon import annotate, harmonise, place, privacy, read_room, scale, verify

MAX_WIDTH = 1080
JPEG_QUALITY = 88
DEFAULT_FOOTER = "Approx. preview · to scale · TEST DATA"


@dataclass
class TryonProduct:
    sku: str
    category: str  # tv | ac | wm
    width_mm: float
    height_mm: float
    label: str  # e.g. '55" · Meru QLED Pro' (already formatted by caller)
    solid: bool  # True = primary (solid render), False = dashed saffron outline


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
    px_per_cm: float | None = None
    ms: int = 0
    error: str | None = None
    planned: list[tuple[str, tuple[int, int, int, int]]] = field(default_factory=list)  # (sku, px box) for tests


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def _load(photo: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(photo))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
    return img


def _has_real_wall(read: ImageRead | None) -> bool:
    return bool(read and read.kind == "room" and any(b.label == "wall" for b in read.boxes))


def _prepare(req: TryonRequest, res: TryonResult):
    img = _load(req.photo)
    boxes = read_room.to_pixels(req.read, img.width, img.height)
    img = privacy.blur_people(img, boxes.get("person"))
    res.blurred_photo = _jpeg(img)
    ppc, conf = scale.estimate(boxes, req.wall_width_ft)
    res.px_per_cm, res.scale_confidence = ppc, conf
    if ppc is None or conf < scale.MIN_CONFIDENCE:
        res.needs_wall_width = True
        return None
    category = (req.products[0].category if req.products else "tv") or "tv"
    wall = read_room.main_wall(boxes)
    sofa = read_room.first(boxes, "sofa")
    ceiling = read_room.first(boxes, "ceiling_line")
    if sofa is not None:
        floor_y = float(sofa[3])
    elif _has_real_wall(req.read):
        floor_y = float(wall[3])
    else:
        floor_y = None
    ceiling_y = (ceiling[1] + ceiling[3]) / 2 if ceiling else None
    placed = place.plan(category, req.products, wall, ppc, sofa, floor_y, ceiling_y)
    res.planned = [(p.sku, p.box) for p in placed]
    composite = place.compose(img, category, placed, ppc, req.niche_width_cm, req.products)
    return category, placed, composite


async def _run(req: TryonRequest, editor, do_harmonise: bool, res: TryonResult) -> None:
    prepared = await asyncio.to_thread(_prepare, req, res)
    if prepared is None:
        return
    category, placed, composite = prepared
    # keep a usable image from here on, even if a later step fails or times out
    res.image = await asyncio.to_thread(
        lambda: _jpeg(annotate.labels(composite, placed, req.distance_text, req.footer_text))
    )
    final = composite
    if do_harmonise and editor is not None and category == "tv":
        edited = await harmonise.run(editor, composite)
        primary = next((p for p in placed if p.solid), placed[0] if placed else None)
        if edited is not None and primary is not None:
            ok = await asyncio.to_thread(verify.check, composite, edited, primary.box)
            if ok:
                final = edited.resize(composite.size) if edited.size != composite.size else edited
                res.harmonised = res.verified = True
    if final is not composite:
        res.image = await asyncio.to_thread(
            lambda: _jpeg(annotate.labels(final, placed, req.distance_text, req.footer_text))
        )


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
    return res
