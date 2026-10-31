"""Privacy: blur people before anything is stored or composed."""

from __future__ import annotations

from PIL import Image, ImageFilter

from chanakya.tryon.read_room import PxBox

BLUR_RADIUS = 25


def blur_people(img: Image.Image, boxes: list[PxBox] | None) -> Image.Image:
    """Gaussian-blur every person box (radius 25). Returns a new image."""
    out = img.copy()
    for x0, y0, x1, y1 in boxes or []:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(out.width, x1), min(out.height, y1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        region = out.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
        out.paste(region, (x0, y0))
    return out
