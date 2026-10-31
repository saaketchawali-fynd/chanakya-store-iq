"""Place products to scale on the room photo (spec 17.1.4). Size and position come from geometry only."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from chanakya.tryon.read_room import PxBox

SCREEN_PATH = Path(__file__).resolve().parent.parent / "render" / "static" / "img" / "screen_bright.jpg"
SAFFRON = (232, 131, 58)
BEZEL = (17, 17, 17)
TV_CENTRE_ABOVE_FLOOR_CM = 105
AC_BELOW_CEILING_CM = 30
AC_ABOVE_FLOOR_CM = 210


@dataclass
class Placed:
    sku: str
    box: PxBox
    solid: bool
    label: str


@lru_cache
def _screen_source() -> Image.Image:
    if SCREEN_PATH.exists():
        try:
            im = Image.open(SCREEN_PATH)
            im.load()
            return im.convert("RGB")
        except Exception:
            pass
    # fallback: bright sky-to-field gradient
    w, h = 320, 180
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            if y < h * 0.62:
                t = y / (h * 0.62)
                px[x, y] = (int(90 + 100 * t), int(160 + 60 * t), int(240 - 10 * t))
            else:
                t = (y - h * 0.62) / (h * 0.38)
                px[x, y] = (int(80 - 30 * t), int(180 - 50 * t), int(70 - 20 * t))
    d = ImageDraw.Draw(im)
    d.ellipse((w * 0.72, h * 0.1, w * 0.86, h * 0.35), fill=(255, 236, 150))
    return im


def _centered(cx: float, cy: float, w: float, h: float) -> PxBox:
    return (round(cx - w / 2), round(cy - h / 2), round(cx + w / 2), round(cy + h / 2))


def _clamp_centre(cx: float, cy: float, w: float, h: float, wall: PxBox) -> tuple[float, float]:
    wx0, wy0, wx1, wy1 = wall
    if w <= wx1 - wx0:
        cx = min(max(cx, wx0 + w / 2), wx1 - w / 2)
    else:
        cx = (wx0 + wx1) / 2
    if h <= wy1 - wy0:
        cy = min(max(cy, wy0 + h / 2), wy1 - h / 2)
    else:
        cy = (wy0 + wy1) / 2
    return cx, cy


def _dashed_rect(d: ImageDraw.ImageDraw, box: PxBox, color, width: int, dash: int, gap: int) -> None:
    x0, y0, x1, y1 = box

    def seg_line(a, b, horizontal: bool, fixed: int):
        pos = a
        while pos < b:
            end = min(pos + dash, b)
            if horizontal:
                d.line([(pos, fixed), (end, fixed)], fill=color, width=width)
            else:
                d.line([(fixed, pos), (fixed, end)], fill=color, width=width)
            pos = end + gap

    seg_line(x0, x1, True, y0)
    seg_line(x0, x1, True, y1)
    seg_line(y0, y1, False, x0)
    seg_line(y0, y1, False, x1)


def _shadow(img: Image.Image, box: PxBox, radius: int = 18, offset=(0, 8), alpha: float = 0.35) -> Image.Image:
    pad = radius * 3
    x0, y0, x1, y1 = box
    layer = Image.new("L", img.size, 0)
    ImageDraw.Draw(layer).rectangle(
        (x0 + offset[0], y0 + offset[1], x1 + offset[0], y1 + offset[1]), fill=int(255 * alpha)
    )
    crop = (max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad + offset[1]))
    region = layer.crop(crop).filter(ImageFilter.GaussianBlur(radius))
    layer = Image.new("L", img.size, 0)
    layer.paste(region, crop[:2])
    black = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(black, img, layer)


def _draw_tv(img: Image.Image, box: PxBox, px_per_cm: float) -> Image.Image:
    img = _shadow(img, box)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=BEZEL)
    bezel = max(4, round(1.2 * px_per_cm))
    sx0, sy0, sx1, sy1 = x0 + bezel, y0 + bezel, x1 - bezel, y1 - bezel
    if sx1 - sx0 > 4 and sy1 - sy0 > 4:
        screen = _screen_source().resize((sx1 - sx0, sy1 - sy0))
        img.paste(screen, (sx0, sy0))
        d = ImageDraw.Draw(img)
        # 3 px highlight along the top edge of the glass
        d.line([(sx0, sy0 + 1), (sx1 - 1, sy0 + 1)], fill=(255, 255, 255), width=3)
    return img


def _draw_ac(img: Image.Image, box: PxBox) -> Image.Image:
    img = _shadow(img, box, radius=12, offset=(0, 6), alpha=0.25)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    r = max(4, (y1 - y0) // 5)
    d.rounded_rectangle(box, radius=r, fill=(248, 248, 248), outline=(170, 170, 170), width=2)
    vy = y0 + round((y1 - y0) * 0.72)
    d.line([(x0 + r, vy), (x1 - r, vy)], fill=(140, 140, 140), width=max(2, (y1 - y0) // 25))
    return img


def _draw_wm(img: Image.Image, box: PxBox, front: bool = True) -> Image.Image:
    img = _shadow(img, box, radius=12, offset=(0, 4), alpha=0.25)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    d.rounded_rectangle(box, radius=max(3, w // 20), fill=(246, 246, 246), outline=(160, 160, 160), width=2)
    d.line([(x0, y0 + h * 0.15), (x1, y0 + h * 0.15)], fill=(180, 180, 180), width=2)
    if front:
        rr = min(w, h) * 0.32
        cx, cy = x0 + w / 2, y0 + h * 0.57
        d.ellipse(
            (cx - rr, cy - rr, cx + rr, cy + rr), outline=(120, 120, 120), width=max(3, w // 40), fill=(205, 220, 232)
        )
    return img


def _fit_marker(img: Image.Image, box: PxBox, fits: bool) -> None:
    d = ImageDraw.Draw(img)
    r = max(14, (box[2] - box[0]) // 8)
    cx, cy = box[2] - r // 2, box[1] - r // 2
    color = (46, 139, 87) if fits else (200, 40, 40)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color, outline=(255, 255, 255), width=3)
    lw = max(3, r // 4)
    if fits:
        d.line(
            [(cx - r * 0.5, cy), (cx - r * 0.1, cy + r * 0.4), (cx + r * 0.55, cy - r * 0.45)], fill="white", width=lw
        )
    else:
        d.line([(cx - r * 0.45, cy - r * 0.45), (cx + r * 0.45, cy + r * 0.45)], fill="white", width=lw)
        d.line([(cx - r * 0.45, cy + r * 0.45), (cx + r * 0.45, cy - r * 0.45)], fill="white", width=lw)


def plan(
    category: str,
    products: list,
    wall: PxBox,
    px_per_cm: float,
    sofa: PxBox | None,
    floor_y: float | None,
    ceiling_y: float | None,
) -> list[Placed]:
    """Compute pixel boxes for every product. The primary (solid) sets the anchor; outlines share its centre."""
    if not products:
        return []
    primary = next((p for p in products if p.solid), products[0])
    wx0, wy0, wx1, wy1 = wall
    pw = primary.width_mm / 10 * px_per_cm
    ph = primary.height_mm / 10 * px_per_cm

    if category == "ac":
        cx = (wx0 + wx1) / 2
        if ceiling_y is not None:
            top = ceiling_y + AC_BELOW_CEILING_CM * px_per_cm
        elif floor_y is not None:
            top = floor_y - AC_ABOVE_FLOOR_CM * px_per_cm
        else:
            top = wy0 + (wy1 - wy0) * 0.1
        cy = top + ph / 2
        cx, cy = _clamp_centre(cx, cy, pw, ph, wall)
    elif category == "wm":
        cx = (wx0 + wx1) / 2
        bottom = floor_y if floor_y is not None else wy1
        cy = bottom - ph / 2
        cx = min(max(cx, wx0 + pw / 2), wx1 - pw / 2) if pw <= wx1 - wx0 else (wx0 + wx1) / 2
    else:  # tv
        sofa_under = False
        if sofa is not None:
            scx = (sofa[0] + sofa[2]) / 2
            sofa_under = wx0 <= scx <= wx1 and (sofa[1] + sofa[3]) / 2 > (wy0 + wy1) / 2
        cx = (sofa[0] + sofa[2]) / 2 if sofa_under else (wx0 + wx1) / 2
        if floor_y is not None:
            cy = floor_y - TV_CENTRE_ABOVE_FLOOR_CM * px_per_cm
        else:
            cy = wy0 + (wy1 - wy0) * 0.45
        cx, cy = _clamp_centre(cx, cy, pw, ph, wall)

    out: list[Placed] = []
    for p in products:
        w = p.width_mm / 10 * px_per_cm
        h = p.height_mm / 10 * px_per_cm
        pcy = cy
        if category == "wm":  # all washing machines stand on the floor line
            pcy = cy + ph / 2 - h / 2
        out.append(Placed(sku=p.sku, box=_centered(cx, pcy, w, h), solid=p is primary, label=p.label))
    return out


def compose(
    img: Image.Image,
    category: str,
    placed: list[Placed],
    px_per_cm: float,
    niche_width_cm: float | None = None,
    products: list | None = None,
) -> Image.Image:
    out = img.convert("RGB").copy()
    by_sku = {p.sku: p for p in (products or [])}
    for pl in placed:
        if not pl.solid:
            continue
        if category == "ac":
            out = _draw_ac(out, pl.box)
        elif category == "wm":
            out = _draw_wm(out, pl.box)
        else:
            out = _draw_tv(out, pl.box, px_per_cm)
    d = ImageDraw.Draw(out)
    lw = max(3, round(out.width / 300))
    for pl in placed:
        if pl.solid:
            continue
        _dashed_rect(d, pl.box, SAFFRON, width=lw, dash=lw * 6, gap=lw * 4)
    if category == "wm" and niche_width_cm:
        for pl in placed:
            prod = by_sku.get(pl.sku)
            if prod is not None:
                _fit_marker(out, pl.box, prod.width_mm / 10 <= niche_width_cm)
    return out
