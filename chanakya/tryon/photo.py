"""The real product in the try-on: cut it out of its catalogue photo (a plain studio background, white or any
single colour), crop a TV to its panel when it hangs on the wall, and check the cut-out is clean and has the
product's true shape. Anything doubtful returns None, and the drawn product is used instead.

Catalogue photos come from the product data (the Jio Commerce media URL, or the local image in sample mode)."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter, ImageOps

MAX_PX = 900  # working size: enough detail for a product that fills ~half the room photo
BG_LO, BG_HI = 10.0, 30.0  # colour distance from the background: ≤ LO is background, ≥ HI is product


@dataclass
class Cutout:
    rgba: Image.Image  # the product only, cropped (a TV: its panel, plus the stand when it stands on a unit)
    panel: tuple[int, int, int, int]  # the box inside `rgba` that maps to the product's true width × height
    screen: tuple[int, int, int, int] | None  # a TV's blank (black) screen inside `rgba`: lit with content
    edge_rgb: np.ndarray  # mean colour of the product's left/right edges (for its drawn side faces)


def _border_background(a: np.ndarray) -> np.ndarray | None:
    """The studio background colour, or None if the border is not one plain colour (a lifestyle photo)."""
    b = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    med = np.median(b, axis=0)
    if (np.abs(b - med).max(axis=1) < 14).mean() < 0.8:
        return None
    return med


def _alpha(rgb: Image.Image) -> np.ndarray | None:
    a = np.asarray(rgb, dtype=np.float32)
    bg = _border_background(a)
    if bg is None:
        return None
    d = np.abs(a - bg).max(axis=2)
    del a
    # background = background-coloured pixels connected to the border (white details inside the product stay)
    import cv2

    n, labels = cv2.connectedComponents((d <= BG_HI).astype(np.uint8), connectivity=4)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    edge = edge[edge > 0]
    region = np.isin(labels, edge)
    del labels
    alpha = np.ones(d.shape, np.float32)
    alpha[region] = np.clip((d[region] - BG_LO) / (BG_HI - BG_LO), 0.0, 1.0)
    return alpha


def _span(cover: np.ndarray, frac: float) -> tuple[int, int] | None:
    """First/last index where coverage ≥ frac × its maximum (trims faint shadows and specks)."""
    if cover.max() <= 0:
        return None
    idx = np.nonzero(cover >= frac * cover.max())[0]
    return int(idx[0]), int(idx[-1]) + 1


def _panel_rows(width: np.ndarray) -> tuple[int, int] | None:
    """A TV's panel: the contiguous run of rows at (nearly) full width around the widest row; the stand is narrow."""
    top = int(np.argmax(width))
    full = width >= 0.85 * width[top]
    r0 = top
    while r0 > 0 and full[r0 - 1]:
        r0 -= 1
    r1 = top
    while r1 < len(width) - 1 and full[r1 + 1]:
        r1 += 1
    return r0, r1 + 1


def _screen_box(rgb: np.ndarray, alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    """A TV photographed with its screen off (black, maybe a glossy reflection): the screen area inside the panel,
    to be lit with a picture. None when the photo already shows a picture on the screen."""
    h, w = alpha.shape
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    core = lum[int(0.1 * h): int(0.9 * h), int(0.1 * w): int(0.9 * w)]
    if core.size == 0 or float(core.mean()) > 75 or float(np.percentile(core, 95)) > 140:
        return None  # a picture (or a bright screen) is already on it
    dark = (lum < 48) & (alpha > 0.9)
    rows, cols = _span(dark.sum(axis=1), 0.9), _span(dark.sum(axis=0), 0.9)
    if rows and cols and (rows[1] - rows[0]) * (cols[1] - cols[0]) >= 0.8 * w * h:
        return cols[0], rows[0], cols[1], rows[1]
    bs, bb = max(1, round(0.009 * w)), max(1, round(0.02 * w))  # thin bezel, a little more at the bottom (logo)
    return bs, bs, w - bs, h - bb


def cutout(data: bytes, category: str, aspect: float, keep_stand: bool = False) -> Cutout | None:
    """The product from its photo, or None. `aspect` = true width / height (of the panel, for a TV)."""
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        im.thumbnail((MAX_PX, MAX_PX))
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            alpha = np.asarray(rgba.getchannel("A"), np.float32) / 255.0
            rgb = rgba.convert("RGB")
        else:
            rgb = im.convert("RGB")
            alpha = _alpha(rgb)
            if alpha is None:
                return None
    except Exception:
        return None
    solid = alpha > 0.5
    rows, cols = _span(solid.sum(axis=1), 0.04), _span(solid.sum(axis=0), 0.04)
    if rows is None or cols is None:
        return None
    (y0, y1), (x0, x1) = rows, cols
    if (y1 - y0) < 40 or (x1 - x0) < 40:
        return None
    if category == "tv":
        pr = _panel_rows(solid[y0:y1, x0:x1].sum(axis=1))
        py0, py1 = y0 + pr[0], y0 + pr[1]
        pc = _span(solid[py0:py1].sum(axis=0), 0.5)
        if pc is None:
            return None
        px0, px1 = pc
        below = solid[py1:y1, px0:px1]
        is_stand = y1 - py1 >= 3 and float(below.mean(axis=1).max()) < 0.6  # feet/pedestal, not a floor surface
        if keep_stand and not is_stand:
            return None  # no usable stand in the photo: the drawn TV (with its pedestal) is used
        if keep_stand:  # panel + its own stand, when it stands on a TV unit
            cy0, cy1, cx0, cx1 = py0, y1, min(px0, x0), max(px1, x1)
        else:
            cy0, cy1, cx0, cx1 = py0, py1, px0, px1
        panel = (px0 - cx0, py0 - cy0, px1 - cx0, py1 - cy0)
        fill_box = (px0, py0, px1, py1)
    else:
        cy0, cy1, cx0, cx1 = y0, y1, x0, x1
        panel = (0, 0, x1 - x0, y1 - y0)
        fill_box = (x0, y0, x1, y1)
    fx0, fy0, fx1, fy1 = fill_box
    pw, ph = fx1 - fx0, fy1 - fy0
    if ph < 20 or pw < 20:
        return None
    # quality gates: the product is one solid shape, with (about) its true proportions
    fill = float(alpha[fy0:fy1, fx0:fx1].mean())
    if fill < (0.93 if category == "tv" else 0.8):
        return None
    if not (0.82 <= (pw / ph) / aspect <= 1.22):
        return None
    a = np.asarray(rgb, np.float32)
    crop_rgb = a[cy0:cy1, cx0:cx1]
    crop_a = alpha[cy0:cy1, cx0:cx1]
    edge = np.concatenate([crop_rgb[:, :3].reshape(-1, 3), crop_rgb[:, -3:].reshape(-1, 3)])
    edge_a = np.concatenate([crop_a[:, :3].reshape(-1), crop_a[:, -3:].reshape(-1)])
    edge_rgb = edge[edge_a > 0.9].mean(axis=0) if (edge_a > 0.9).any() else crop_rgb.reshape(-1, 3).mean(axis=0)
    screen = None
    if category == "tv":
        sx0, sy0, sx1, sy1 = panel
        sb = _screen_box(crop_rgb[sy0:sy1, sx0:sx1], crop_a[sy0:sy1, sx0:sx1])
        if sb is not None:
            screen = (sb[0] + sx0, sb[1] + sy0, sb[2] + sx0, sb[3] + sy0)
    a8 = Image.fromarray((np.clip(crop_a, 0, 1) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(0.6))
    out = Image.fromarray(np.clip(crop_rgb, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    out.putalpha(a8)
    return Cutout(out, panel, screen, edge_rgb.astype(np.float32))
