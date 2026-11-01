"""Labels on the try-on (spec 17.1.7): product pills, a 1 m scale bar on the wall plane, the distance line and
the footer. Text is pre-formatted by the caller. A small layout pass guarantees that nothing overlaps."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from chanakya.tryon import render
from chanakya.tryon.fonts import font
from chanakya.tryon.geometry import Wall
from chanakya.tryon.place import SAFFRON, Placed
from chanakya.tryon.read_room import Room

INDIGO = (27, 42, 74)
SAFFRON_TEXT = (196, 96, 22)
SS = 2
Box = tuple[int, int, int, int]
SCALE_BAR_TEXT = "1 m"


@dataclass
class _Pill:
    img: Image.Image  # RGBA incl. shadow padding
    off: tuple[int, int]  # pill rect top-left inside img
    w: int
    h: int


def _pill(lines: list[str], size: int, fill, fg, fg2=None, outline=None, shadow: bool = True) -> _Pill:
    """Rounded label (one or two lines) rendered at 2× and downsampled for smooth edges."""
    lines = [s for s in lines if s]
    f1 = font(size * SS, bold=True)
    f2 = font(max(10, round(size * 0.8)) * SS)
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    metrics = []
    for i, text in enumerate(lines):
        f = f1 if i == 0 else f2
        bl, bt, br, bb = probe.textbbox((0, 0), text, font=f)
        asc, desc = f.getmetrics() if hasattr(f, "getmetrics") else (bb, 0)
        metrics.append((text, f, bl, br - bl, asc + desc, asc))
    padx, pady, lgap = round(0.6 * size * SS), round(0.3 * size * SS), round(0.06 * size * SS)
    tw = max(m[3] for m in metrics)
    th = sum(m[4] for m in metrics) + lgap * (len(metrics) - 1)
    pw, ph = tw + 2 * padx, th + 2 * pady
    sp = round(0.35 * size * SS) if shadow else 0
    im = Image.new("RGBA", (pw + 2 * sp, ph + 2 * sp), (0, 0, 0, 0))
    radius = min(ph // 2, round(0.75 * size * SS))
    if shadow:
        sh = Image.new("L", im.size, 0)
        ImageDraw.Draw(sh).rounded_rectangle((sp, sp + SS * 2, sp + pw, sp + ph + SS * 2), radius=radius, fill=80)
        sh = sh.filter(ImageFilter.GaussianBlur(sp / 2.5))
        im.putalpha(sh)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle(
        (sp, sp, sp + pw - 1, sp + ph - 1),
        radius=radius,
        fill=tuple(fill) + (246,),
        outline=(tuple(outline) + (255,)) if outline else None,
        width=round(1.6 * SS) if outline else 0,
    )
    y = sp + pady
    for i, (text, f, bl, _w, lh, _asc) in enumerate(metrics):
        col = fg if i == 0 or fg2 is None else fg2
        d.text((sp + padx - bl, y), text, font=f, fill=tuple(col) + (255,))
        y += lh + lgap
    out = im.resize((max(1, im.width // SS), max(1, im.height // SS)), Image.Resampling.LANCZOS)
    return _Pill(out, (sp // SS, sp // SS), pw // SS, ph // SS)


def _inter(a: Box, b: Box) -> int:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _grow(b: Box, g: int) -> Box:
    return (b[0] - g, b[1] - g, b[2] + g, b[3] + g)


class _Layout:
    def __init__(self, w: int, h: int, margin: int, gap: int):
        self.w, self.h, self.margin, self.gap = w, h, margin, gap
        self.taken: list[tuple[str, Box]] = []

    def clamp(self, x: float, y: float, w: int, h: int) -> Box:
        m = self.margin
        x = min(max(m, x), self.w - m - w)
        y = min(max(m, y), self.h - m - h)
        return (round(x), round(y), round(x) + w, round(y) + h)

    def free(self, b: Box) -> bool:
        if b[0] < 0 or b[1] < 0 or b[2] > self.w or b[3] > self.h:
            return False
        return not any(_inter(_grow(b, self.gap // 2), t) for _, t in self.taken)

    def best(self, cands, w: int, h: int, soft: list[tuple[Box, float]]) -> Box:
        scored = []
        for i, (x, y) in enumerate(cands):
            b = self.clamp(x, y, w, h)
            if not self.free(b):
                continue
            moved = abs(b[0] - x) + abs(b[1] - y)
            cost = sum(_inter(b, s) * wt for s, wt in soft) / max(1, w * h) + 0.04 * i + 0.002 * moved
            scored.append((cost, b))
        if scored:
            return min(scored)[1]
        # nothing free among the candidates: scan the photo for the nearest free spot
        x0, y0 = cands[0] if cands else (self.margin, self.margin)
        step = max(4, h // 3)
        spots = []
        for yy in range(self.margin, max(self.margin + 1, self.h - self.margin - h), step):
            for xx in range(self.margin, max(self.margin + 1, self.w - self.margin - w), step):
                b = (xx, yy, xx + w, yy + h)
                if self.free(b):
                    cost = sum(_inter(b, s) * wt for s, wt in soft) / max(1, w * h)
                    spots.append((cost + 0.002 * (abs(xx - x0) + abs(yy - y0)), b))
        if spots:
            return min(spots)[1]
        return self.clamp(x0, y0, w, h)  # photo too small to avoid overlap: keep it on the photo

    def add(self, name: str, b: Box) -> None:
        self.taken.append((name, b))


def _over(base: Image.Image, layer: Image.Image, xy: tuple[int, int]) -> None:
    """Alpha-composite an RGBA layer onto the RGB base at xy (clipped to the base)."""
    x, y = xy
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(base.width, x + layer.width), min(base.height, y + layer.height)
    if x1 <= x0 or y1 <= y0:
        return
    crop = base.crop((x0, y0, x1, y1)).convert("RGBA")
    crop.alpha_composite(layer.crop((x0 - x, y0 - y, x1 - x, y1 - y)))
    base.paste(crop.convert("RGB"), (x0, y0))


def _paste(base: Image.Image, p: _Pill, b: Box) -> None:
    _over(base, p.img, (b[0] - p.off[0], b[1] - p.off[1]))


def _union(boxes: list[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _scale_bar(
    img: Image.Image, wall: Wall, placed: list[Placed], lay: _Layout, soft, size: int, furniture: list[Box]
) -> Box | None:
    """|—— 1 m ——| drawn on the wall plane (follows its perspective) on free wall beside the product."""
    rects = [p.rect for p in placed]
    u0 = min(r[0] - r[2] / 2 for r in rects)
    u1 = max(r[0] + r[2] / 2 for r in rects)
    z0 = min(r[1] - r[3] / 2 for r in rects)
    z1 = max(r[1] + r[3] / 2 for r in rects)
    zm = (z0 + z1) / 2
    tick = 5.0
    cands = [
        (u0, z0 - 12),  # just below the product, left-aligned
        (u1 - 100, z0 - 12),
        (u1 + 15, zm),  # beside it, at mid height
        (u0 - 115, zm),
        (u1 + 15, z0 + 8),
        (u0 - 115, z0 + 8),
        (u1 + 15, z1 - 8),
        (u0 - 115, z1 - 8),
    ]
    top = (wall.ceiling_z or wall.top_z) - tick - 4
    z = z1 + 14
    while z <= top:  # sweep up the free wall above the product and its labels
        cands += [(u0, z), (u1 - 100, z), ((u0 + u1) / 2 - 50, z), (u1 + 15, z), (u0 - 115, z)]
        z += 18
    cands = [(ua, zz) for ua, zz in cands if tick + 2 <= zz <= max(top, tick + 2)]  # on the wall, not the floor
    pill = _pill([SCALE_BAR_TEXT], max(12, round(size * 0.62)), INDIGO, (255, 255, 255), shadow=False)
    solid_boxes = [p.box for p in placed if p.solid]
    all_boxes = [p.box for p in placed]
    ub = _union(all_boxes)
    pcx, pcy = (ub[0] + ub[2]) / 2, (ub[1] + ub[3]) / 2
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    best = None
    for strict in (True, False):  # first: clear of every product and of the furniture; then: of the solid one
        for i, (ua, z) in enumerate(cands):
            pts = wall.to_img(
                [(ua, z), (ua + 100, z), (ua, z + tick), (ua, z - tick), (ua + 100, z + tick), (ua + 100, z - tick)]
            )
            if not np.all(np.isfinite(pts)):
                continue
            mid = (pts[0] + pts[1]) / 2
            tx, ty = mid[0] - pill.w / 2, mid[1] - pill.h / 2
            bx = (
                int(min(pts[:, 0].min(), tx)) - 3,
                int(min(pts[:, 1].min(), ty)) - 3,
                int(max(pts[:, 0].max(), tx + pill.w)) + 3,
                int(max(pts[:, 1].max(), ty + pill.h)) + 3,
            )
            length = float(np.linalg.norm(pts[1] - pts[0]))
            if length < pill.w + 30 or not lay.free(bx):
                continue
            if any(_inter(bx, b) for b in (all_boxes if strict else solid_boxes)):
                continue
            if strict and any(_inter(bx, f) for f in furniture):
                continue
            near = float(np.hypot((bx[0] + bx[2]) / 2 - pcx, (bx[1] + bx[3]) / 2 - pcy)) / img.width
            area = max(1, (bx[2] - bx[0]) * (bx[3] - bx[1]))
            cost = sum(_inter(bx, s) * wt for s, wt in soft) / area + 2.0 * near + 0.005 * i
            if best is None or cost < best[0]:
                best = (cost, pts, (round(tx), round(ty)), bx)
        if best is not None:
            break
    if best is None:
        return None
    _, pts, txy, bx = best
    x0, y0 = bx[0], bx[1]
    under = gray[max(0, bx[1]) : max(1, bx[3]), max(0, bx[0]) : max(1, bx[2])]
    light_wall = float(under.mean()) > 130 if under.size else True
    line_col = (INDIGO + (255,)) if light_wall else (255, 255, 255, 255)
    halo = (255, 255, 255, 170) if light_wall else (20, 20, 20, 150)
    ov = Image.new("RGBA", ((bx[2] - bx[0]) * SS, (bx[3] - bx[1]) * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    q = (pts - [x0, y0]) * SS
    a, b = q[0], q[1]
    L = float(np.linalg.norm(b - a))
    u = (b - a) / L
    gap = (pill.w / 2 + 5) * SS
    m = (a + b) / 2
    segs = [(a, m - u * gap), (m + u * gap, b), (q[2], q[3]), (q[4], q[5])]
    lw = max(2, round(img.width / 420)) * SS
    for width, col in ((lw + 2 * SS, halo), (lw, line_col)):
        for p0, p1 in segs:
            d.line([tuple(p0), tuple(p1)], fill=col, width=width)
    ov = ov.resize((bx[2] - bx[0], bx[3] - bx[1]), Image.Resampling.LANCZOS)
    _over(img, ov, (x0, y0))
    _over(img, pill.img, (txy[0] - pill.off[0], txy[1] - pill.off[1]))
    return bx


def annotate(
    img: Image.Image,
    placed: list[Placed],
    distance_text: str | None,
    footer_text: str | None,
    wall: Wall | None = None,
    room: Room | None = None,
) -> tuple[Image.Image, list[tuple[str, Box]]]:
    """Returns the labelled image and every label's box (for tests: they never overlap)."""
    out = img.convert("RGB").copy()
    W, H = out.size
    size = max(12, round(W / 42))
    small = max(10, round(size * 0.72))
    lay = _Layout(W, H, margin=max(8, round(W / 110)), gap=max(6, round(size * 0.3)))
    solids = [p for p in placed if p.solid]
    outlines = [p for p in placed if not p.solid]
    soft: list[tuple[Box, float]] = [(p.box, 3.0) for p in solids] + [(p.box, 0.5) for p in outlines]
    if room is not None:
        for label in ("door", "window", "tv_unit", "sofa", "person"):
            soft += [(b, 0.25) for b in room.all(label)]

    if footer_text:
        fp = _pill([footer_text], small, (253, 236, 236), (150, 30, 30))
        b = lay.clamp(W - lay.margin - fp.w, H - lay.margin - fp.h, fp.w, fp.h)
        _paste(out, fp, b)
        lay.add("footer", b)
    if distance_text:
        dp = _pill([distance_text], size, (255, 255, 255), INDIGO, outline=INDIGO)
        foot = next((t for n, t in lay.taken if n == "footer"), None)
        cands = [(lay.margin, H - lay.margin - dp.h)]
        if foot is not None:
            cands.append((lay.margin, foot[1] - lay.gap - dp.h))
        b = lay.best(cands, dp.w, dp.h, [])
        _paste(out, dp, b)
        lay.add("distance", b)

    for p in placed:  # WM fit tick/cross against the niche width: drawn first, labels keep clear of it
        if p.fits is not None:
            out, mb = render.fit_marker(out, p.quad, p.fits)
            lay.add(f"fit:{p.sku}", mb)
    if placed:
        union = _union([p.box for p in placed])
        g = lay.gap
        for p in solids:
            pp = _pill([p.label, p.dims_text or ""], size, INDIGO, (255, 255, 255), fg2=(206, 214, 232))
            s = p.box
            cands = [
                (s[0], union[1] - g - pp.h),
                (s[2] - pp.w, union[1] - g - pp.h),
                ((s[0] + s[2]) / 2 - pp.w / 2, union[1] - g - pp.h),
            ]
            for t_name, t in lay.taken:  # stack above whatever blocks the spot right above the product
                if t_name.startswith("fit:"):
                    cands += [(s[0], t[1] - g - pp.h), (s[2] - pp.w, t[1] - g - pp.h)]
            cands += [
                (s[0], union[3] + g),
                (s[2] - pp.w, union[3] + g),
                (union[0] - g - pp.w, (s[1] + s[3]) / 2 - pp.h / 2),
                (union[2] + g, (s[1] + s[3]) / 2 - pp.h / 2),
                (s[0] + g, s[1] + g),
            ]
            b = lay.best(cands, pp.w, pp.h, soft)
            _paste(out, pp, b)
            lay.add(f"label:{p.sku}", b)
        for p in outlines:
            op = _pill([p.label, p.dims_text or ""], size, (255, 255, 255), SAFFRON_TEXT, outline=SAFFRON)
            o = p.box
            cands = [(o[2] - op.w, o[1] - g - op.h), (o[0], o[1] - g - op.h)]
            for n, t in lay.taken:
                if n.startswith("label:"):
                    cands += [(t[2] + g, t[1]), (t[0], t[1] - g - op.h), (t[0] - g - op.w, t[1])]
                    cands += [(o[2] - op.w, t[1] - g - op.h)]
            cands += [
                (o[2] - op.w, o[3] + g),
                (o[0], o[3] + g),
                (o[2] + g, o[1]),
                (o[0] - g - op.w, o[1]),
                (o[2] - op.w - g, o[1] + g),
            ]
            b = lay.best(cands, op.w, op.h, soft)
            _paste(out, op, b)
            lay.add(f"label:{p.sku}", b)
        if wall is not None:
            furniture = []
            if room is not None:
                for label in ("door", "window", "tv_unit", "sofa", "existing_tv", "person"):
                    furniture += room.all(label)
            bar_soft = soft + [(p.box, 6.0) for p in placed] + [(f, 2.0) for f in furniture]
            bx = _scale_bar(out, wall, placed, lay, bar_soft, size, furniture)
            if bx is not None:
                lay.add("scale_bar", bx)
    return out, list(lay.taken)


def labels(
    img: Image.Image,
    placed: list[Placed],
    distance_text: str | None,
    footer_text: str | None,
    wall: Wall | None = None,
    room: Room | None = None,
) -> Image.Image:
    return annotate(img, placed, distance_text, footer_text, wall, room)[0]
