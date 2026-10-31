"""Labels on the try-on: product pills, the distance line and the footer. Text is pre-formatted by the caller."""

from __future__ import annotations

from PIL import Image, ImageDraw

from chanakya.tryon.fonts import font
from chanakya.tryon.place import SAFFRON, Placed

INDIGO = (27, 42, 74)


def _pill(
    d: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    size: int,
    fill,
    fg,
    outline=None,
    anchor_right: bool = False,
    bounds: tuple[int, int] | None = None,
) -> tuple[int, int, int, int]:
    f = font(size)
    bl, bt, br, bb = d.textbbox((0, 0), text, font=f)
    tw, th = br - bl, bb - bt
    padx, pady = size * 0.6, size * 0.35
    w, h = tw + 2 * padx, th + 2 * pady
    x, y = xy
    if anchor_right:
        x -= w
    if bounds:
        x = min(max(4, x), bounds[0] - w - 4)
        y = min(max(4, y), bounds[1] - h - 4)
    box = (round(x), round(y), round(x + w), round(y + h))
    d.rounded_rectangle(box, radius=round(h / 2), fill=fill, outline=outline, width=3 if outline else 0)
    d.text((x + padx - bl, y + pady - bt), text, font=f, fill=fg)
    return box


def labels(img: Image.Image, placed: list[Placed], distance_text: str | None, footer_text: str | None) -> Image.Image:
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    W, H = out.size
    size = max(16, round(W / 42))
    bounds = (W, H)
    outlines = [p for p in placed if not p.solid]
    solids = [p for p in placed if p.solid]
    # outline label: above its box, left aligned; solid label: above the outline (right aligned) or its own box
    for p in outlines:
        # stack above the solid label's row so the two pills never overlap
        y = p.box[1] - size * (4.2 if solids else 2.0)
        if y < 4:
            y = p.box[3] + size * (2.6 if solids else 0.4)
        _pill(d, (p.box[0], y), p.label, size, fill=(255, 255, 255), fg=SAFFRON, outline=SAFFRON, bounds=bounds)
    for p in solids:
        if outlines:
            ref = min(o.box[1] for o in outlines)
            y = ref - size * 2.0
            if y < 4:
                y = max(o.box[3] for o in outlines) + size * 0.4
            right = max(o.box[2] for o in outlines)
            _pill(d, (right, y), p.label, size, fill=INDIGO, fg=(255, 255, 255), anchor_right=True, bounds=bounds)
        else:
            y = p.box[1] - size * 2.0 if p.box[1] - size * 2.0 > 4 else p.box[3] + size * 0.4
            _pill(d, (p.box[0], y), p.label, size, fill=INDIGO, fg=(255, 255, 255), bounds=bounds)
    small = max(14, round(size * 0.75))
    if footer_text:
        fb = _pill(
            d,
            (W - 12, H - small * 2.4),
            footer_text,
            small,
            fill=(253, 236, 236),
            fg=(150, 30, 30),
            anchor_right=True,
            bounds=bounds,
        )
    else:
        fb = (0, H, 0, H)
    if distance_text:
        _pill(
            d,
            (12, fb[1] - size * 2.2),
            distance_text,
            size,
            fill=(255, 255, 255),
            fg=INDIGO,
            outline=INDIGO,
            bounds=bounds,
        )
    return out
