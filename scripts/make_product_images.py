"""Studio-style product PNGs per SKU (spec 16.4) → chanakya/render/static/img/products/<sku>.png.

Every SKU in data/seed/products.yaml (or the built-in list when the seed is missing) gets a transparent PNG,
longest side ~1000 px, tight crop, soft floor/contact shadow:

* tv        front view, true aspect from dims, ultra-thin bezel, glossy screen with a vivid wallpaper, feet/pedestal
* ac        split indoor unit, curved white front, outlet vanes + louvre, LED temperature, brand text
* wm        front-load (chrome door ring, glass porthole, drum, dial + display) or top-load (glass lid, console)
* laptop    open, 3/4 view (real perspective), wallpaper on screen, keyboard deck, trackpad
* soundbar  slim bar with grille texture (+ subwoofer when channels is x.1)

Colours come from ``info.colours`` when given. Each product is an SVG (gradients, filters, blurred shadows)
rasterised by headless Chromium (Playwright, transparent background, 2× supersampled). Without Chromium a
simpler Pillow drawing is used so the Docker build never fails.

    python scripts/make_product_images.py [--out DIR] [--only SKU,SKU] [--seed products.yaml] [--pillow]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import math
import re
import sys
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from make_screen_image import wallpaper_svg  # noqa: E402

ROOT = HERE.parent
OUT = ROOT / "chanakya" / "render" / "static" / "img" / "products"
SEED = ROOT / "data" / "seed" / "products.yaml"
TARGET = 1000  # longest side of the final PNG (px)
SS = 2  # supersampling factor for the Chromium render
FONT = "'Helvetica Neue', Helvetica, Arial, 'Noto Sans', 'DejaVu Sans', sans-serif"

BUILTIN = [
    *[
        {"sku": s, "category": "tv", "brand": b, "model": m, "dims": {"width_mm": w, "height_mm": h}}
        for s, b, m, w, h in [
            ("TV-ARK-43-4K60", "Arka", "Arka 43 4K", 963, 560),
            ("TV-ARK-55-4K60", "Arka", "Arka 55 4K", 1240, 720),
            ("TV-MER-55-QL120", "Meru", "Meru 55 QLED Pro", 1230, 708),
            ("TV-NOV-55-OL120", "Novex", "Novex 55 OLED", 1228, 708),
            ("TV-MER-65-QL120", "Meru", "Meru 65 QLED Pro", 1450, 835),
        ]
    ],
    {
        "sku": "SB-MER-21",
        "category": "soundbar",
        "brand": "Meru",
        "model": "Meru Soundbar 2.1",
        "specs": {"channels": "2.1"},
        "dims": {"width_mm": 860, "height_mm": 65},
    },
    *[
        {"sku": s, "category": "ac", "brand": b, "model": m, "dims": {"width_mm": w, "height_mm": h}}
        for s, b, m, w, h in [
            ("AC-ARK-10-3S", "Arka", "Arka 1.0T 3-star", 850, 290),
            ("AC-MER-15-5S", "Meru", "Meru 1.5T 5-star", 970, 300),
            ("AC-NOV-15-5S", "Novex", "Novex 1.5T 5-star", 990, 310),
        ]
    ],
    *[
        {
            "sku": s,
            "category": "wm",
            "brand": b,
            "model": m,
            "specs": {"load": ld},
            "dims": {"width_mm": w, "height_mm": h},
        }
        for s, b, m, ld, w, h in [
            ("WM-ARK-65-TL", "Arka", "Arka 6.5 kg Top", "top", 540, 950),
            ("WM-MER-8-FL", "Meru", "Meru 8 kg Front", "front", 600, 850),
        ]
    ],
    *[
        {"sku": s, "category": "laptop", "brand": b, "model": m, "specs": sp}
        for s, b, m, sp in [
            ("LP-ARK-E-8", "Arka", "Arka Book 14", {"screen_in": 14, "gpu": "none"}),
            ("LP-NOV-H-16G", "Novex", "Novex Pro 16", {"screen_in": 16, "gpu": "dedicated"}),
        ]
    ],
]

# ---------- colour helpers ----------
COLOUR_WORDS: list[tuple[tuple[str, ...], str]] = [
    (("midnight blue", "deep blue", "navy", "ocean blue", "sapphire"), "#26324d"),
    (("black steel", "dark steel", "black stainless"), "#3c3f46"),
    (
        (
            "space grey",
            "space gray",
            "titan grey",
            "titan gray",
            "graphite",
            "charcoal",
            "gunmetal",
            "dark grey",
            "dark gray",
            "titanium black",
            "slate",
        ),
        "#4a4d55",
    ),
    (("black", "midnight", "onyx", "jet", "carbon", "obsidian", "noir", "ebony"), "#1f2126"),
    (("silver", "platinum", "aluminium", "aluminum", "steel", "inox", "metallic", "chrome", "titanium"), "#c6cad1"),
    (("grey", "gray", "ash", "stone", "smoke"), "#a9aeb6"),
    (("white", "pearl", "ivory", "snow", "frost", "cream", "porcelain"), "#f3f4f5"),
    (("blue", "sky", "azure", "cobalt", "teal"), "#3f6fa6"),
    (("red", "wine", "maroon", "burgundy", "crimson", "ruby", "cherry"), "#7c2433"),
    (("gold", "champagne", "sand", "beige", "bronze", "copper"), "#c7ad86"),
    (("rose", "pink", "blush"), "#d9aaa4"),
    (("green", "sage", "olive", "mint", "emerald"), "#5f7d69"),
]
BRAND_ACCENT = {"arka": "#E8833A", "meru": "#3D8BFD", "novex": "#9B5CF6"}
BRAND_WALL = {"arka": "sunrise", "meru": "ocean", "novex": "aurora"}


def _rgb(hx: str) -> tuple[int, int, int]:
    hx = hx.lstrip("#")
    if len(hx) == 3:
        hx = "".join(c * 2 for c in hx)
    return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)


def _hex(c) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(v))) for v in c))


def mix(a: str, b: str, t: float) -> str:
    ca, cb = _rgb(a), _rgb(b)
    return _hex(tuple(ca[i] + (cb[i] - ca[i]) * t for i in range(3)))


def lighten(c: str, t: float) -> str:
    return mix(c, "#ffffff", t)


def darken(c: str, t: float) -> str:
    return mix(c, "#000000", t)


def luminance(c: str) -> float:
    r, g, b = _rgb(c)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def colour_of(p: dict, default: str) -> str:
    """First usable colour from info.colours (name words or #hex), else the category default."""
    cols = ((p.get("info") or {}).get("colours")) or p.get("colours") or []
    if isinstance(cols, (str, dict)):
        cols = [cols]
    for c in cols:
        if isinstance(c, dict):
            if isinstance(c.get("hex"), str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", c["hex"].strip()):
                return "#" + c["hex"].strip().lstrip("#")
            c = c.get("en") or c.get("name") or next((v for v in c.values() if isinstance(v, str)), "")
        name = str(c).strip().lower()
        if re.fullmatch(r"#?[0-9a-f]{6}", name):
            return "#" + name.lstrip("#")
        best = None
        for words, hx in COLOUR_WORDS:
            for w in words:
                if w in name and (best is None or len(w) > best[0]):
                    best = (len(w), hx)
        if best:
            return best[1]
    return default


def colour_names(p: dict) -> list[str]:
    cols = ((p.get("info") or {}).get("colours")) or p.get("colours") or []
    if isinstance(cols, (str, dict)):
        cols = [cols]
    out = []
    for c in cols:
        if isinstance(c, dict):
            c = c.get("en") or c.get("name") or c.get("hex") or ""
        out.append(str(c).strip().lower())
    return [c for c in out if c]


METAL_WORDS = ("silver", "grey", "gray", "steel", "titan", "graphite", "platinum", "chrome", "inox", "metallic")


def is_metallic(p: dict) -> bool:
    names = colour_names(p)
    return bool(names) and any(w in names[0] for w in METAL_WORDS)


def metal_sheen(gid: str = "metal") -> str:
    """Brushed-metal highlight bands (horizontal gradient) laid over a body."""
    return lin(
        gid,
        [
            (0, "#fff", 0),
            (0.16, "#fff", 0.30),
            (0.3, "#fff", 0.05),
            (0.62, "#000", 0.05),
            (0.84, "#fff", 0.14),
            (1, "#fff", 0),
        ],
        0,
        0,
        1,
        0,
    )


def _seed(p: dict) -> int:
    return zlib.crc32(str(p.get("sku") or p.get("model") or "x").encode())


def _brand(p: dict) -> str:
    return str(p.get("brand") or "").strip()


def _wall_palette(p: dict) -> str:
    return BRAND_WALL.get(_brand(p).lower(), ("bright", "lagoon", "aurora", "ocean", "sunrise")[_seed(p) % 5])


def _accent(p: dict) -> str:
    return BRAND_ACCENT.get(_brand(p).lower(), "#3D8BFD")


# Pre-rendered wallpapers (palette, variant) → JPEG data URI, filled by render_all(); the SVG is used otherwise.
WALLS: dict[tuple[str, int], str] = {}
WALL_VARIANTS = 4


def wall_key(p: dict, salt: int = 0) -> tuple[str, int]:
    return _wall_palette(p), (_seed(p) + salt) % WALL_VARIANTS


def wall_seed(variant: int) -> int:
    return variant * 17 + 3


def screen_fill(p: dict, salt: int, x: float, y: float, w: float, h: float, prefix: str) -> str:
    """Wallpaper filling a screen box: cached bitmap when available, else the live SVG."""
    key = wall_key(p, salt)
    uri = WALLS.get(key)
    if uri:
        return (
            f'<image href="{uri}" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'preserveAspectRatio="xMidYMid slice"/>'
        )
    svg = wallpaper_svg(1600, 900, key[0], wall_seed(key[1]), prefix=prefix, standalone=False)
    return svg.replace("<svg ", f'<svg x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" ', 1)


def _dims(p: dict) -> dict:
    d = p.get("dims") or {}
    return d if isinstance(d, dict) else {}


def _num(v, default: float) -> float:
    try:
        f = float(v)
        return f if f > 0 else default
    except (TypeError, ValueError):
        return default


# ---------- SVG helpers ----------
def lin(gid: str, stops, x1=0.0, y1=0.0, x2=0.0, y2=1.0, user: bool = False) -> str:
    units = ' gradientUnits="userSpaceOnUse"' if user else ""
    s = "".join(
        f'<stop offset="{o}" stop-color="{c}"' + (f' stop-opacity="{a}"' if a is not None else "") + "/>"
        for o, c, *rest in stops
        for a in [rest[0] if rest else None]
    )
    return f'<linearGradient id="{gid}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"{units}>{s}</linearGradient>'


def rad(gid: str, stops, cx=0.5, cy=0.5, r=0.5, fx=None, fy=None, user: bool = False) -> str:
    units = ' gradientUnits="userSpaceOnUse"' if user else ""
    f = (f' fx="{fx}"' if fx is not None else "") + (f' fy="{fy}"' if fy is not None else "")
    s = "".join(
        f'<stop offset="{o}" stop-color="{c}"' + (f' stop-opacity="{a}"' if a is not None else "") + "/>"
        for o, c, *rest in stops
        for a in [rest[0] if rest else None]
    )
    return f'<radialGradient id="{gid}" cx="{cx}" cy="{cy}" r="{r}"{f}{units}>{s}</radialGradient>'


def blur(fid: str, sd: float, pad: str = "-50%") -> str:
    return (
        f'<filter id="{fid}" x="{pad}" y="{pad}" width="200%" height="200%">'
        f'<feGaussianBlur stdDeviation="{sd:.2f}"/></filter>'
    )


def glow(fid: str, sd: float) -> str:
    return (
        f'<filter id="{fid}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="{sd:.2f}" '
        f'result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
        f"</filter>"
    )


def pts(points) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def poly_path(points) -> str:
    return "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in points) + " Z"


def svg_doc(w: float, h: float, defs: list[str], body: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}">'
        f"<defs>{''.join(defs)}</defs>{''.join(body)}</svg>"
    )


def text(
    x,
    y,
    s: str,
    size: float,
    fill: str,
    weight: int = 600,
    anchor: str = "middle",
    spacing: float = 0,
    opacity: float = 1.0,
    extra: str = "",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size:.1f}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}" letter-spacing="{spacing}" opacity="{opacity}" {extra}>'
        f"{escape(s)}</text>"
    )


SEGMENTS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgedc",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
    "-": "g",
    "C": "afed",
    "E": "afged",
    "H": "fbgec",
}


def seven_seg(s: str, x: float, y: float, h: float, fill: str) -> tuple[str, float]:
    """7-segment LED text (top-left at x, y; height h) → (svg, width)."""
    w, t = 0.5 * h, 0.12 * h
    gap, skew = 0.02 * h, 0.07
    out, cx = [], x

    def sk(px, py):
        return px + (y + h - py) * skew, py

    def hseg(x0, x1, yc):
        return [
            (x0, yc),
            (x0 + t / 2, yc - t / 2),
            (x1 - t / 2, yc - t / 2),
            (x1, yc),
            (x1 - t / 2, yc + t / 2),
            (x0 + t / 2, yc + t / 2),
        ]

    def vseg(xc, y0, y1):
        return [
            (xc, y0),
            (xc + t / 2, y0 + t / 2),
            (xc + t / 2, y1 - t / 2),
            (xc, y1),
            (xc - t / 2, y1 - t / 2),
            (xc - t / 2, y0 + t / 2),
        ]

    for ch in s:
        if ch == ":":
            for yy in (y + 0.32 * h, y + 0.68 * h):
                out.append(
                    f'<polygon points="{pts([sk(cx + t * 0.2, yy - t / 2), sk(cx + t * 1.2, yy - t / 2), sk(cx + t * 1.2, yy + t / 2), sk(cx + t * 0.2, yy + t / 2)])}"/>'
                )
            cx += t * 1.9
            continue
        if ch == "°":
            ox, oy = sk(cx + 0.18 * h, y + 0.16 * h)
            out.append(
                f'<circle cx="{ox:.1f}" cy="{oy:.1f}" r="{0.1 * h:.1f}" fill="none" stroke="{fill}" '
                f'stroke-width="{0.055 * h:.1f}"/>'
            )
            cx += 0.38 * h
            continue
        if ch == " ":
            cx += w * 0.6
            continue
        segs = SEGMENTS.get(ch, "")
        x0, x1 = cx + t / 2, cx + w - t / 2
        ym = y + h / 2
        geo = {
            "a": hseg(x0 + gap, x1 - gap, y + t / 2),
            "g": hseg(x0 + gap, x1 - gap, ym),
            "d": hseg(x0 + gap, x1 - gap, y + h - t / 2),
            "f": vseg(x0, y + t / 2 + gap, ym - gap),
            "b": vseg(x1, y + t / 2 + gap, ym - gap),
            "e": vseg(x0, ym + gap, y + h - t / 2 - gap),
            "c": vseg(x1, ym + gap, y + h - t / 2 - gap),
        }
        for sg in segs:
            out.append(f'<polygon points="{pts([sk(*q) for q in geo[sg]])}"/>')
        cx += w + 0.22 * h
    return f'<g fill="{fill}">{"".join(out)}</g>', cx - x


def floor_shadow(cx: float, cy: float, rx: float, ry: float, op: float, fid: str) -> str:
    return f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="#000" opacity="{op}" filter="url(#{fid})"/>'


# ---------- TV ----------
def svg_tv(p: dict) -> tuple[str, float, float]:
    d = _dims(p)
    wmm = _num(d.get("width_mm"), 1230)
    hmm = _num(d.get("height_mm"), wmm * 0.576)
    ar = min(0.75, max(0.45, hmm / wmm))
    brand = _brand(p)
    specs = p.get("specs") or {}
    panel = str(specs.get("panel") or "").lower()
    body_col = colour_of(p, "#16171b")
    # the front bezel stays near-black (tinted by the finish); the finish itself shows on the stand
    frame = mix(body_col, "#141519", 0.6) if luminance(body_col) <= 0.55 else "#1b1c20"
    stand_col = mix(body_col, "#3a3c42", 0.35)
    pw, ph = 1000.0, round(1000.0 * ar)
    m, top = 40.0, 20.0
    x0, y0 = m, top
    b = 6.5
    chin = b * 1.7
    style = "feet" if brand.lower() in ("arka", "") or _seed(p) % 7 == 0 else "pedestal"
    fh = 0.060 * pw if style == "feet" else 0.046 * pw
    yb = y0 + ph
    floor = yb + fh
    w_total, h_total = pw + 2 * m, floor + 34
    sx, sy, sw, sh = x0 + b, y0 + b, pw - 2 * b, ph - b - chin
    defs = [
        lin("bez", [(0, mix(frame, "#3b3d44", 0.35)), (0.5, frame), (1, darken(frame, 0.35))]),
        lin("gloss", [(0, "#fff", 0.20), (0.38, "#fff", 0.04), (0.55, "#fff", 0), (1, "#fff", 0)], 0, 0, 1, 1),
        lin("stand", [(0, lighten(stand_col, 0.25)), (0.5, stand_col), (1, darken(stand_col, 0.45))], 0, 0, 1, 0),
        lin("plate", [(0, lighten(stand_col, 0.35)), (0.45, stand_col), (1, darken(stand_col, 0.55))]),
        blur("sh9", 9),
        blur("sh3", 3),
        blur("sh1", 1.2),
        f'<clipPath id="scr"><rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" rx="2"/></clipPath>',
    ]
    body = []
    # floor + contact shadows
    if style == "feet":
        body.append(floor_shadow(x0 + pw / 2, floor + 4, pw * 0.48, 10, 0.20, "sh9"))
    else:
        body.append(floor_shadow(x0 + pw / 2, floor + 4, pw * 0.26, 9, 0.28, "sh9"))
    # stand (behind the panel)
    if style == "feet":
        for fx, sgn in ((x0 + pw * 0.115, -1), (x0 + pw * 0.885, 1)):
            spread = fh * 0.62
            blade = [(fx - 5, yb - 6), (fx + 5, yb - 6), (fx + 5 + sgn * spread, floor), (fx - 5 + sgn * spread, floor)]
            body.append(floor_shadow(fx + sgn * spread, floor + 1, 30, 4, 0.45, "sh3"))
            body.append(f'<polygon points="{pts(blade)}" fill="url(#stand)"/>')
            body.append(
                f'<rect x="{fx + sgn * spread - 22:.1f}" y="{floor - 3.5:.1f}" width="44" height="4.5" rx="2.2" '
                f'fill="{darken(stand_col, 0.25)}"/>'
            )
    else:
        neck_w = pw * (0.05 if brand.lower() != "novex" else 0.036)
        plate_w = pw * (0.34 if brand.lower() != "novex" else 0.42)
        cx = x0 + pw / 2
        body.append(floor_shadow(cx, floor + 1, plate_w * 0.52, 6, 0.45, "sh3"))
        neck = [
            (cx - neck_w * 0.36, yb - 20),
            (cx + neck_w * 0.36, yb - 20),
            (cx + neck_w / 2, floor - 8),
            (cx - neck_w / 2, floor - 8),
        ]
        body.append(f'<polygon points="{pts(neck)}" fill="url(#stand)"/>')
        body.append(
            f'<rect x="{cx - plate_w / 2:.1f}" y="{floor - 10:.1f}" width="{plate_w:.1f}" height="10" rx="5" '
            f'fill="url(#plate)"/>'
        )
        body.append(
            f'<rect x="{cx - plate_w / 2 + 6:.1f}" y="{floor - 9.5:.1f}" width="{plate_w - 12:.1f}" height="1.4" '
            f'rx="0.7" fill="#fff" opacity="0.35"/>'
        )
    # panel
    body.append(f'<rect x="{x0}" y="{y0}" width="{pw}" height="{ph}" rx="7" fill="url(#bez)"/>')
    body.append(
        f'<rect x="{x0 + 0.6}" y="{y0 + 0.6}" width="{pw - 1.2}" height="{ph - 1.2}" rx="6.5" fill="none" '
        f'stroke="#ffffff" stroke-opacity="0.16" stroke-width="1.2"/>'
    )
    body.append(f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" rx="2" fill="#020203"/>')
    body.append(f'<g clip-path="url(#scr)">{screen_fill(p, 0, sx + 2, sy + 2, sw - 4, sh - 4, "tvw")}')
    if "oled" in panel:  # deeper blacks: subtle contrast boost at the edges
        body.append(
            f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" fill="none" stroke="#000" stroke-opacity="0.5" '
            f'stroke-width="6" filter="url(#sh3)"/>'
        )
    body.append(f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" fill="url(#gloss)"/>')
    body.append(
        f'<polygon points="{pts([(sx, sy), (sx + sw * 0.46, sy), (sx + sw * 0.16, sy + sh), (sx, sy + sh)])}" '
        f'fill="#fff" opacity="0.045"/>'
    )
    body.append("</g>")
    body.append(text(x0 + pw / 2, yb - chin / 2 + 2.6, brand.upper(), 7.5, "#a7a9b0", 700, spacing=1.6))
    return svg_doc(w_total, h_total, defs, body), w_total, h_total


# ---------- AC ----------
def svg_ac(p: dict) -> tuple[str, float, float]:
    d = _dims(p)
    wmm = _num(d.get("width_mm"), 970)
    hmm = _num(d.get("height_mm"), 300)
    ar = min(0.40, max(0.24, hmm / wmm))
    base = colour_of(p, "#f3f4f6")
    dark = luminance(base) < 0.45
    bw, bh = 1000.0, round(1000.0 * ar)
    m = 30.0
    x0, y0 = m, 18.0
    x1, y1 = x0 + bw, y0 + bh
    rt, rb = 18.0, 44.0
    w_total, h_total = bw + 2 * m, y1 + 64
    outline = (
        f"M{x0 + rt},{y0} H{x1 - rt} A{rt},{rt} 0 0 1 {x1},{y0 + rt} V{y1 - rb} A{rb},{rb} 0 0 1 {x1 - rb},{y1} "
        f"H{x0 + rb} A{rb},{rb} 0 0 1 {x0},{y1 - rb} V{y0 + rt} A{rt},{rt} 0 0 1 {x0 + rt},{y0} Z"
    )
    ink = "#8c929b" if not dark else "#c9ccd2"
    accent = "#78d6ff" if not dark else "#9fe3ff"
    oy0, oy1 = y0 + 0.735 * bh, y1 - 0.07 * bh
    ox0, ox1 = x0 + 0.055 * bw, x1 - 0.055 * bw
    defs = [
        lin("acb", [(0, lighten(base, 0.7)), (0.45, base), (0.8, darken(base, 0.05)), (1, darken(base, 0.16))]),
        lin("acs", [(0, "#000", 0.07), (0.1, "#000", 0), (0.9, "#000", 0), (1, "#000", 0.07)], 0, 0, 1, 0),
        lin("under", [(0, "#000", 0), (1, "#000", 0.07)]),
        lin("outl", [(0, "#16191d"), (0.5, "#2f343b"), (1, "#1c1f24")]),
        lin("flap", [(0, lighten(base, 0.6)), (0.6, darken(base, 0.04)), (1, darken(base, 0.2))]),
        blur("sh12", 12),
        blur("sh5", 5),
        glow("led", 2.2),
        f'<clipPath id="outc"><rect x="{ox0}" y="{oy0}" width="{ox1 - ox0}" height="{oy1 - oy0}" rx="12"/></clipPath>',
        f'<clipPath id="bodyc"><path d="{outline}"/></clipPath>',
    ]
    body = [
        floor_shadow(x0 + bw / 2, y1 + 26, bw * 0.44, 10, 0.16, "sh12"),
        f'<path d="{outline}" fill="#000" opacity="0.20" transform="translate(0 12)" filter="url(#sh12)"/>',
        f'<path d="{outline}" fill="url(#acb)"/>',
        f'<path d="{outline}" fill="url(#acs)"/>',
        '<g clip-path="url(#bodyc)">',
        # top edge highlight + groove
        f'<rect x="{x0}" y="{y0 + 0.10 * bh:.1f}" width="{bw}" height="1.6" fill="{darken(base, 0.12)}" opacity="0.7"/>',
        f'<rect x="{x0}" y="{y0 + 0.10 * bh + 1.6:.1f}" width="{bw}" height="1.4" fill="#fff" opacity="0.8"/>',
        f'<ellipse cx="{x0 + bw * 0.42:.1f}" cy="{y0 + 0.26 * bh:.1f}" rx="{bw * 0.42:.1f}" ry="{0.11 * bh:.1f}" '
        f'fill="#fff" opacity="{0.75 if not dark else 0.14}" filter="url(#sh12)"/>',
        # seam above the outlet
        f'<rect x="{x0}" y="{y0 + 0.655 * bh:.1f}" width="{bw}" height="2" fill="{darken(base, 0.2)}" opacity="0.8"/>',
        f'<rect x="{x0}" y="{y0 + 0.655 * bh + 2:.1f}" width="{bw}" height="1.5" fill="#fff" opacity="0.7"/>',
        f'<rect x="{x0}" y="{y0 + 0.66 * bh:.1f}" width="{bw}" height="{0.34 * bh:.1f}" fill="url(#under)"/>',
        "</g>",
        # outlet with vertical vanes and the louvre flap
        f'<rect x="{ox0}" y="{oy0}" width="{ox1 - ox0}" height="{oy1 - oy0}" rx="12" fill="url(#outl)"/>',
        '<g clip-path="url(#outc)">',
    ]
    vx = ox0 + 10
    while vx < ox1:
        body.append(f'<rect x="{vx:.1f}" y="{oy0}" width="3" height="{oy1 - oy0}" fill="#3a4048"/>')
        body.append(f'<rect x="{vx + 3:.1f}" y="{oy0}" width="1" height="{oy1 - oy0}" fill="#58606a" opacity="0.4"/>')
        vx += 17
    fh = (oy1 - oy0) * 0.62
    fy = oy0 + (oy1 - oy0) * 0.30
    body.append(
        f'<path d="M{ox0},{fy + 6} Q{(ox0 + ox1) / 2},{fy - 6} {ox1},{fy + 6} V{fy + fh} '
        f'Q{(ox0 + ox1) / 2},{fy + fh - 10} {ox0},{fy + fh} Z" fill="url(#flap)"/>'
    )
    body.append(
        f'<path d="M{ox0},{fy + fh} Q{(ox0 + ox1) / 2},{fy + fh - 10} {ox1},{fy + fh}" stroke="#000" '
        f'stroke-opacity="0.35" stroke-width="2" fill="none"/>'
    )
    body.append("</g>")
    body.append(
        f'<rect x="{ox0}" y="{oy0}" width="{ox1 - ox0}" height="{oy1 - oy0}" rx="12" fill="none" '
        f'stroke="#000" stroke-opacity="0.25" stroke-width="1.5"/>'
    )
    names = colour_names(p)
    if len(names) > 1:  # second finish as a slim accent strip above the seam
        trim = colour_of({"info": {"colours": [names[1]]}}, "#c7ad86")
        body.append(
            f'<rect x="{x0 + 0.04 * bw:.1f}" y="{y0 + 0.615 * bh:.1f}" width="{bw * 0.92:.1f}" height="{0.022 * bh:.1f}" '
            f'rx="{0.011 * bh:.1f}" fill="{trim}"/>'
        )
        body.append(
            f'<rect x="{x0 + 0.04 * bw:.1f}" y="{y0 + 0.615 * bh:.1f}" width="{bw * 0.92:.1f}" height="{0.008 * bh:.1f}" '
            f'fill="#fff" opacity="0.5"/>'
        )
    # LED temperature + mode icon, brand
    led, lw = seven_seg("24°", 0, 0, 0.12 * bh, accent)
    lx, ly = x1 - 0.15 * bw, y0 + 0.36 * bh
    if _brand(p).lower() == "novex":  # smoked display window
        body.append(
            f'<rect x="{lx - lw / 2 - 52:.1f}" y="{ly - 0.05 * bh:.1f}" width="{lw + 82:.1f}" height="{0.22 * bh:.1f}" '
            f'rx="{0.11 * bh:.1f}" fill="#1b1f25" opacity="0.88"/>'
        )
    body.append(f'<g filter="url(#led)" transform="translate({lx - lw / 2:.1f} {ly:.1f})">{led}</g>')
    sfx, sfy, r = lx - lw / 2 - 26, ly + 0.06 * bh, 0.035 * bh
    flake = "".join(
        f'<line x1="{sfx:.1f}" y1="{sfy:.1f}" x2="{sfx + r * math.cos(a):.1f}" y2="{sfy + r * math.sin(a):.1f}"/>'
        for a in (i * math.pi / 3 for i in range(6))
    )
    body.append(f'<g stroke="{accent}" stroke-width="2" stroke-linecap="round" filter="url(#led)">{flake}</g>')
    body.append(text(x0 + bw / 2, y0 + 0.52 * bh, _brand(p), 0.085 * bh, ink, 600, spacing=1.5))
    return svg_doc(w_total, h_total, defs, body), w_total, h_total


# ---------- washing machines ----------
def _wm_load(p: dict) -> str:
    sp = p.get("specs") or {}
    for v in (
        sp.get("load"),
        sp.get("type"),
        sp.get("loading"),
        p.get("model"),
        (p.get("title") or {}).get("en") if isinstance(p.get("title"), dict) else p.get("title"),
    ):
        s = str(v or "").lower()
        if "top" in s:
            return "top"
        if "front" in s:
            return "front"
    return "front"


def svg_wm_front(p: dict) -> tuple[str, float, float]:
    d = _dims(p)
    wmm = _num(d.get("width_mm"), 600)
    hmm = _num(d.get("height_mm"), 850)
    ar = min(1.65, max(1.2, hmm / wmm))
    base = colour_of(p, "#f2f3f5")
    dark = luminance(base) < 0.45
    bh = 900.0
    bw = round(bh / ar)
    m = 34.0
    x0, y0 = m, 16.0
    x1, y1 = x0 + bw, y0 + bh
    cp = 0.15 * bh
    w_total, h_total = bw + 2 * m, y1 + 40
    ink = "#8d939b" if not dark else "#c4c8ce"
    cx, cy = x0 + bw / 2, y0 + cp + (bh - cp) * 0.45
    R = 0.385 * bw
    defs = [
        lin("wmb", [(0, lighten(base, 0.6)), (0.12, base), (0.85, darken(base, 0.04)), (1, darken(base, 0.12))]),
        lin("wms", [(0, "#000", 0.08), (0.08, "#000", 0), (0.92, "#000", 0), (1, "#000", 0.10)], 0, 0, 1, 0),
        lin("panel", [(0, lighten(base, 0.5)), (1, darken(base, 0.06))]),
        lin(
            "ring", [(0, "#fbfbfc"), (0.3, "#aeb4bc"), (0.52, "#f1f3f5"), (0.8, "#7f8690"), (1, "#d7dade")], 0, 0, 1, 1
        ),
        lin("ring2", [(0, "#6d737c"), (0.5, "#e9ecef"), (1, "#5d636b")], 0, 0, 1, 1),
        metal_sheen(),
        rad("glass", [(0, "#4f6479"), (0.7, "#26313d"), (1, "#151b22")], fx=0.4, fy=0.35),
        rad("drum", [(0, "#d9dde2"), (0.7, "#9aa1aa"), (1, "#6c737c")]),
        rad("knob", [(0, "#ffffff"), (0.7, "#e3e6ea"), (1, "#b8bdc4")], fx=0.35, fy=0.3),
        lin("disp", [(0, "#0d1014"), (1, "#20262d")]),
        '<pattern id="holes" width="11" height="11" patternUnits="userSpaceOnUse" patternTransform="rotate(30)">'
        '<circle cx="3" cy="3" r="1.9" fill="#3b4148" opacity="0.55"/><circle cx="8.5" cy="8.5" r="1.9" fill="#3b4148" opacity="0.55"/></pattern>',
        blur("sh10", 10),
        blur("sh4", 4),
        blur("sh2", 2),
        glow("led", 2),
        f'<clipPath id="drumc"><circle cx="{cx}" cy="{cy}" r="{R * 0.70:.1f}"/></clipPath>',
        f'<clipPath id="glassc"><circle cx="{cx}" cy="{cy}" r="{R * 0.80:.1f}"/></clipPath>',
    ]
    body = [
        floor_shadow(cx, y1 + 6, bw * 0.56, 13, 0.24, "sh10"),
        f'<rect x="{x0 + 18}" y="{y1 - 6}" width="34" height="14" rx="3" fill="#2a2d32"/>',
        f'<rect x="{x1 - 52}" y="{y1 - 6}" width="34" height="14" rx="3" fill="#2a2d32"/>',
        floor_shadow(cx, y1 + 4, bw * 0.47, 5, 0.40, "sh2"),
        f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}" rx="22" fill="url(#wmb)"/>',
        f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}" rx="22" fill="url(#wms)"/>',
        (f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}" rx="22" fill="url(#metal)"/>' if is_metallic(p) else ""),
        f'<rect x="{x0 + 3}" y="{y0 + 1.5}" width="{bw - 6}" height="2" rx="1" fill="#fff" opacity="0.9"/>',
        # control panel
        f'<rect x="{x0 + 2}" y="{y0 + 2}" width="{bw - 4}" height="{cp}" rx="20" fill="url(#panel)"/>',
        f'<rect x="{x0}" y="{y0 + cp}" width="{bw}" height="2.2" fill="{darken(base, 0.22)}" opacity="0.8"/>',
        f'<rect x="{x0}" y="{y0 + cp + 2.2}" width="{bw}" height="1.6" fill="#fff" opacity="0.7"/>',
    ]
    # detergent drawer
    dx, dy, dw, dh = x0 + 0.05 * bw, y0 + 0.024 * bh, 0.30 * bw, 0.098 * bh
    body += [
        f'<rect x="{dx:.1f}" y="{dy:.1f}" width="{dw:.1f}" height="{dh:.1f}" rx="7" fill="{lighten(base, 0.35)}" '
        f'stroke="{darken(base, 0.18)}" stroke-width="1.4"/>',
        f'<rect x="{dx + dw * 0.12:.1f}" y="{dy + dh - 15:.1f}" width="{dw * 0.76:.1f}" height="8" rx="4" '
        f'fill="{darken(base, 0.22)}"/>',
    ]
    # program dial
    kx, ky, kr = x0 + 0.53 * bw, y0 + cp * 0.5, cp * 0.34
    ticks = "".join(
        f'<circle cx="{kx + kr * 1.32 * math.cos(a):.1f}" cy="{ky + kr * 1.32 * math.sin(a):.1f}" r="1.7"/>'
        for a in (math.radians(-225 + i * 270 / 11) for i in range(12))
    )
    body += [
        f'<g fill="{ink}" opacity="0.8">{ticks}</g>',
        f'<circle cx="{kx:.1f}" cy="{ky + 3:.1f}" r="{kr:.1f}" fill="#000" opacity="0.25" filter="url(#sh4)"/>',
        f'<circle cx="{kx:.1f}" cy="{ky:.1f}" r="{kr:.1f}" fill="url(#ring2)"/>',
        f'<circle cx="{kx:.1f}" cy="{ky:.1f}" r="{kr * 0.8:.1f}" fill="url(#knob)"/>',
        f'<line x1="{kx:.1f}" y1="{ky:.1f}" x2="{kx - kr * 0.45:.1f}" y2="{ky - kr * 0.45:.1f}" stroke="#59606a" '
        f'stroke-width="3.5" stroke-linecap="round"/>',
    ]
    # display + buttons + power
    ex, ey, ew, eh = x0 + 0.68 * bw, y0 + cp * 0.20, 0.215 * bw, cp * 0.38
    seg, sw_ = seven_seg("1:25", 0, 0, eh * 0.52, "#5df2c6")
    body += [
        f'<rect x="{ex:.1f}" y="{ey:.1f}" width="{ew:.1f}" height="{eh:.1f}" rx="6" fill="url(#disp)"/>',
        f'<g filter="url(#led)" transform="translate({ex + ew / 2 - sw_ / 2:.1f} {ey + eh * 0.24:.1f})">{seg}</g>',
    ]
    for i in range(4):
        bx = ex + i * (ew / 4) + 6
        body.append(
            f'<rect x="{bx:.1f}" y="{ey + eh + 12:.1f}" width="{ew / 4 - 12:.1f}" height="7" rx="3.5" '
            f'fill="{darken(base, 0.16)}"/>'
        )
    px, py, pr = x1 - 0.055 * bw, y0 + cp * 0.5, cp * 0.10
    body += [
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{pr:.1f}" fill="{lighten(base, 0.3)}" stroke="{darken(base, 0.2)}" stroke-width="1.4"/>',
        f'<path d="M{px - pr * 0.42:.1f},{py - pr * 0.3:.1f} A{pr * 0.52:.1f},{pr * 0.52:.1f} 0 1 0 {px + pr * 0.42:.1f},{py - pr * 0.3:.1f}" '
        f'fill="none" stroke="{ink}" stroke-width="1.8" stroke-linecap="round"/>',
        f'<line x1="{px:.1f}" y1="{py - pr * 0.6:.1f}" x2="{px:.1f}" y2="{py - pr * 0.05:.1f}" stroke="{ink}" stroke-width="1.8" stroke-linecap="round"/>',
    ]
    body.append(text(x0 + 0.06 * bw, y0 + cp + 0.05 * bh, _brand(p), 0.03 * bh, ink, 600, anchor="start", spacing=1))
    # door
    lifters = "".join(
        f'<rect x="{-R * 0.05:.1f}" y="{-R * 0.69:.1f}" width="{R * 0.10:.1f}" height="{R * 0.16:.1f}" rx="{R * 0.04:.1f}" '
        f'fill="#eef0f2" stroke="#8d949c" stroke-width="1" transform="rotate({ang})"/>'
        for ang in (20, 140, 260)
    )
    body += [
        f'<circle cx="{cx:.1f}" cy="{cy + 8:.1f}" r="{R + 4:.1f}" fill="#000" opacity="0.22" filter="url(#sh10)"/>',
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R:.1f}" fill="url(#ring)"/>',
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R - 1:.1f}" fill="none" stroke="#fff" stroke-opacity="0.6" stroke-width="1.5"/>',
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R * 0.885:.1f}" fill="#2b3036"/>',
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R * 0.80:.1f}" fill="url(#glass)"/>',
        '<g clip-path="url(#drumc)">',
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R * 0.70:.1f}" fill="url(#drum)"/>',
        f'<rect x="{cx - R}" y="{cy - R}" width="{2 * R}" height="{2 * R}" fill="url(#holes)"/>',
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R * 0.30:.1f}" fill="#7d848d" opacity="0.5"/>',
        f'<g transform="translate({cx:.1f} {cy:.1f})">{lifters}</g>',
        "</g>",
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R * 0.80:.1f}" fill="#1c2a38" opacity="0.38"/>',
        '<g clip-path="url(#glassc)">',
        f'<path d="M{cx - R * 0.78:.1f},{cy - R * 0.05:.1f} A{R * 0.8:.1f},{R * 0.8:.1f} 0 0 1 {cx + R * 0.05:.1f},{cy - R * 0.79:.1f} '
        f'A{R * 0.95:.1f},{R * 0.95:.1f} 0 0 0 {cx - R * 0.78:.1f},{cy - R * 0.05:.1f} Z" fill="#fff" opacity="0.42"/>',
        f'<ellipse cx="{cx + R * 0.38:.1f}" cy="{cy + R * 0.42:.1f}" rx="{R * 0.12:.1f}" ry="{R * 0.05:.1f}" fill="#fff" '
        f'opacity="0.25" transform="rotate(-40 {cx + R * 0.38:.1f} {cy + R * 0.42:.1f})"/>',
        "</g>",
        f'<rect x="{cx + R * 0.86:.1f}" y="{cy - R * 0.16:.1f}" width="{R * 0.07:.1f}" height="{R * 0.32:.1f}" rx="{R * 0.035:.1f}" '
        f'fill="#5b616a" opacity="0.8"/>',
        # kick plate + service hatch
        f'<rect x="{x0}" y="{y1 - 0.075 * bh:.1f}" width="{bw}" height="1.8" fill="{darken(base, 0.18)}" opacity="0.7"/>',
        f'<rect x="{x0 + 0.06 * bw:.1f}" y="{y1 - 0.06 * bh:.1f}" width="{0.13 * bw:.1f}" height="{0.038 * bh:.1f}" rx="5" '
        f'fill="none" stroke="{darken(base, 0.2)}" stroke-width="1.4"/>',
    ]
    return svg_doc(w_total, h_total, defs, body), w_total, h_total


def svg_wm_top(p: dict) -> tuple[str, float, float]:
    d = _dims(p)
    wmm = _num(d.get("width_mm"), 560)
    hmm = _num(d.get("height_mm"), 950)
    ar = min(1.9, max(1.3, hmm / wmm))
    base = colour_of(p, "#f2f3f5")
    dark = luminance(base) < 0.45
    ink = "#8d939b" if not dark else "#c4c8ce"
    bw = 540.0
    td = 0.40 * bw  # projected depth of the top surface (camera slightly above)
    hc = 0.30 * td  # console height
    bhf = round(bw * ar) - td * 0.55  # front face (part of the height reads as the top surface)
    inset = 0.045 * bw
    m = 34.0
    x0, x1 = m, m + bw
    y0f = 18.0 + hc + td
    y1 = y0f + bhf
    w_total, h_total = bw + 2 * m, y1 + 40

    def tp(u, v, lift=0.0):  # point on the top surface: u 0..1 (left→right), v 0 (front) .. 1 (back)
        xl, xr = x0 + inset * v, x1 - inset * v
        return xl + (xr - xl) * u, y0f - v * td - lift

    defs = [
        lin("wmb", [(0, lighten(base, 0.35)), (0.08, base), (0.85, darken(base, 0.04)), (1, darken(base, 0.12))]),
        lin("wms", [(0, "#000", 0.08), (0.08, "#000", 0), (0.92, "#000", 0), (1, "#000", 0.10)], 0, 0, 1, 0),
        lin("refl", [(0, "#fff", 0), (0.45, "#fff", 0.22), (0.55, "#fff", 0.22), (1, "#fff", 0)], 0, 0, 1, 0.35),
        lin("topf", [(0, lighten(base, 0.75)), (1, darken(base, 0.04))]),
        metal_sheen(),
        lin("lid", [(0, "#4b5a69"), (1, "#151b22")]),
        lin("cons", [(0, "#151a20"), (1, "#2c343d")]),
        lin("consT", [(0, lighten(base, 0.55)), (1, darken(base, 0.06))]),
        rad("tub", [(0, "#cfd4da"), (0.55, "#8c949d"), (0.85, "#4a5159"), (1, "#2a2f35")]),
        rad("puls", [(0, "#ffffff"), (1, "#aab1b9")], fx=0.4, fy=0.35),
        blur("sh10", 10),
        blur("sh2", 2),
        blur("sh4", 4),
        glow("led", 2),
    ]
    lid = [tp(0.07, 0.06), tp(0.93, 0.06), tp(0.91, 0.68), tp(0.09, 0.68)]
    tc = tp(0.5, 0.38)
    trx, try_ = bw * 0.33, td * 0.25
    defs.append(f'<clipPath id="lidc"><path d="{poly_path(lid)}"/></clipPath>')
    body = [
        floor_shadow(x0 + bw / 2, y1 + 6, bw * 0.56, 13, 0.24, "sh10"),
        f'<rect x="{x0 + 16}" y="{y1 - 6}" width="32" height="13" rx="3" fill="#2a2d32"/>',
        f'<rect x="{x1 - 48}" y="{y1 - 6}" width="32" height="13" rx="3" fill="#2a2d32"/>',
        floor_shadow(x0 + bw / 2, y1 + 4, bw * 0.47, 5, 0.40, "sh2"),
        # front face with a soft diagonal reflection
        f'<rect x="{x0}" y="{y0f - 12}" width="{bw}" height="{bhf + 12}" rx="18" fill="url(#wmb)"/>',
        f'<rect x="{x0}" y="{y0f - 12}" width="{bw}" height="{bhf + 12}" rx="18" fill="url(#wms)"/>',
        f'<rect x="{x0}" y="{y0f - 12}" width="{bw}" height="{bhf + 12}" rx="18" fill="url(#refl)" opacity="0.6"/>',
        (
            f'<rect x="{x0}" y="{y0f - 12}" width="{bw}" height="{bhf + 12}" rx="18" fill="url(#metal)"/>'
            if is_metallic(p)
            else ""
        ),
        # top surface
        f'<path d="{poly_path([tp(0, 0), tp(1, 0), tp(1, 1), tp(0, 1)])}" fill="url(#topf)" '
        f'stroke="{darken(base, 0.10)}" stroke-width="1" stroke-linejoin="round"/>',
        f'<line x1="{x0 + 10}" y1="{y0f + 1}" x2="{x1 - 10}" y2="{y0f + 1}" stroke="#fff" stroke-width="2.2" opacity="0.9"/>',
        # glass lid: tub + pulsator seen through tinted glass, frame, reflections, handle
        f'<path d="{poly_path(lid)}" fill="{darken(base, 0.25)}" transform="translate(0 3)" opacity="0.55" filter="url(#sh2)"/>',
        f'<path d="{poly_path(lid)}" fill="url(#lid)"/>',
        '<g clip-path="url(#lidc)">',
        f'<ellipse cx="{tc[0]:.1f}" cy="{tc[1]:.1f}" rx="{trx:.1f}" ry="{try_:.1f}" fill="#e9ecef" opacity="0.85"/>',
        f'<ellipse cx="{tc[0]:.1f}" cy="{tc[1] + 2:.1f}" rx="{trx * 0.9:.1f}" ry="{try_ * 0.86:.1f}" fill="url(#tub)"/>',
        f'<ellipse cx="{tc[0]:.1f}" cy="{tc[1] + try_ * 0.18:.1f}" rx="{trx * 0.34:.1f}" ry="{try_ * 0.30:.1f}" fill="url(#puls)"/>',
        f'<path d="{poly_path(lid)}" fill="#1e2a35" opacity="0.42"/>',
        f'<polygon points="{pts([tp(0.10, 0.70), tp(0.36, 0.70), tp(0.24, 0.0), tp(0.0, 0.0)])}" fill="#fff" opacity="0.20"/>',
        f'<polygon points="{pts([tp(0.42, 0.70), tp(0.48, 0.70), tp(0.36, 0.0), tp(0.30, 0.0)])}" fill="#fff" opacity="0.12"/>',
        "</g>",
        f'<path d="{poly_path(lid)}" fill="none" stroke="{lighten(base, 0.4)}" stroke-width="4" stroke-linejoin="round"/>',
        f'<path d="{poly_path(lid)}" fill="none" stroke="{darken(base, 0.25)}" stroke-width="1" stroke-linejoin="round"/>',
        f'<rect x="{tp(0.5, 0.06)[0] - 34:.1f}" y="{tp(0.5, 0.06)[1] - 3:.1f}" width="68" height="8" rx="4" '
        f'fill="{darken(base, 0.28)}"/>',
    ]
    # console (raised backsplash): dark glass front face, light top face
    fl, fr = tp(0.015, 0.76), tp(0.985, 0.76)
    face = [fl, fr, tp(0.985, 0.82, hc), tp(0.015, 0.82, hc)]
    topf = [tp(0.015, 0.82, hc), tp(0.985, 0.82, hc), tp(0.985, 1.0, hc * 0.9), tp(0.015, 1.0, hc * 0.9)]
    body += [
        f'<path d="{poly_path(face)}" fill="#000" opacity="0.25" transform="translate(0 4)" filter="url(#sh4)"/>',
        f'<path d="{poly_path(topf)}" fill="url(#consT)" stroke="{darken(base, 0.12)}" stroke-width="1" stroke-linejoin="round"/>',
        f'<path d="{poly_path(face)}" fill="url(#cons)" stroke="{darken(base, 0.3)}" stroke-width="1" stroke-linejoin="round"/>',
    ]
    fh = fl[1] - face[3][1]
    ftop = face[3][1]
    seg, sw_ = seven_seg("0:45", 0, 0, fh * 0.40, "#5df2c6")
    mid = (fl[0] + fr[0]) / 2
    body.append(f'<g filter="url(#led)" transform="translate({mid - sw_ / 2:.1f} {ftop + fh * 0.30:.1f})">{seg}</g>')
    for i, u in enumerate((0.13, 0.21, 0.29, 0.37)):
        x = fl[0] + (fr[0] - fl[0]) * u
        body.append(
            f'<circle cx="{x:.1f}" cy="{ftop + fh * 0.52:.1f}" r="{fh * 0.075:.1f}" fill="#9aa3ad" '
            f'opacity="{0.95 - i * 0.15:.2f}"/>'
        )
    kx, ky = fl[0] + (fr[0] - fl[0]) * 0.82, ftop + fh * 0.5
    body += [
        f'<ellipse cx="{kx:.1f}" cy="{ky:.1f}" rx="{fh * 0.40:.1f}" ry="{fh * 0.36:.1f}" fill="#d7dbe0" stroke="#6b727b" stroke-width="1.5"/>',
        f'<line x1="{kx:.1f}" y1="{ky:.1f}" x2="{kx - fh * 0.18:.1f}" y2="{ky - fh * 0.2:.1f}" stroke="#4a5058" '
        f'stroke-width="2.5" stroke-linecap="round"/>',
        # front details: top cover edge, brand, kick plate
        f'<rect x="{x0}" y="{y0f + 0.03 * bhf:.1f}" width="{bw}" height="1.6" fill="{darken(base, 0.16)}" opacity="0.7"/>',
        f'<rect x="{x0}" y="{y0f + 0.03 * bhf + 1.6:.1f}" width="{bw}" height="1.2" fill="#fff" opacity="0.7"/>',
        text(x0 + bw / 2, y0f + 0.11 * bhf, _brand(p), 0.055 * bw, ink, 600, spacing=1.5),
        f'<rect x="{x0}" y="{y1 - 0.06 * bhf:.1f}" width="{bw}" height="1.8" fill="{darken(base, 0.18)}" opacity="0.7"/>',
    ]
    return svg_doc(w_total, h_total, defs, body), w_total, h_total


# ---------- laptop (real perspective) ----------
class Cam:
    """Tiny pinhole camera: yaw about Y, then pitch about X; camera on -Z looking at the origin."""

    def __init__(self, yaw: float, pitch: float, dist: float, centre=(0.0, 0.0, 0.0)):
        self.cy, self.sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        self.cp, self.sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
        self.dist = dist
        self.c = centre
        self.k, self.ox, self.oy = 1.0, 0.0, 0.0

    def cam(self, x, y, z):
        x, y, z = x - self.c[0], y - self.c[1], z - self.c[2]
        x, z = x * self.cy + z * self.sy, -x * self.sy + z * self.cy
        y, z = y * self.cp + z * self.sp, -y * self.sp + z * self.cp
        return x, y, z

    def raw(self, x, y, z):
        cx, cy, cz = self.cam(x, y, z)
        s = self.dist / (cz + self.dist)
        return cx * s, -cy * s

    def __call__(self, x, y, z):
        u, v = self.raw(x, y, z)
        return u * self.k + self.ox, v * self.k + self.oy


def _rrect3(origin, ex, ey, w, h, r, n=5):
    """Rounded rectangle in a 3D plane (origin + u·ex + v·ey, u∈[0,w], v∈[0,h]) → list of 3D points."""
    r = min(r, w / 2, h / 2)
    out = []
    corners = [(w - r, r, -90), (w - r, h - r, 0), (r, h - r, 90), (r, r, 180)]
    for cu, cv, a0 in corners:
        for i in range(n + 1):
            a = math.radians(a0 + 90 * i / n)
            u, v = cu + r * math.cos(a), cv + r * math.sin(a)
            out.append(tuple(origin[k] + u * ex[k] + v * ey[k] for k in range(3)))
    return out


def _hull(points):
    pts_ = sorted(set((round(x, 2), round(y, 2)) for x, y in points))
    if len(pts_) <= 2:
        return pts_

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for q in pts_:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(pts_):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def svg_laptop(p: dict) -> tuple[str, float, float]:
    sp = p.get("specs") or {}
    gaming = (
        str(sp.get("gpu") or "").lower() in ("dedicated", "rtx", "gaming")
        or "gaming" in str(p.get("model") or "").lower()
    )
    body_c = colour_of(p, "#33363d" if gaming else "#c4c8cf")
    dark = luminance(body_c) < 0.4
    key_c = "#1d1f23" if not dark else "#111214"
    accent = _accent(p)
    W, D, T = 330.0, 228.0, 13.0
    L, LT = 214.0, 5.5
    alpha = math.radians(108)
    ey_l = (0.0, math.sin(alpha), -math.cos(alpha))
    n_front = (0.0, -math.cos(alpha), -math.sin(alpha))  # lid face normal, towards the viewer (−z)
    cam = Cam(yaw=-30, pitch=24, dist=1250, centre=(0, L * 0.38, D * 0.42))
    # geometry (3D)
    base_top = _rrect3((-W / 2, T, D), (1, 0, 0), (0, 0, -1), W, D, 14, 6)
    base_bot = _rrect3((-W / 2 + 3, 0, D - 3), (1, 0, 0), (0, 0, -1), W - 6, D - 6, 12, 6)
    hinge = (-W / 2, T, D - 6)
    lid_o = (hinge[0], hinge[1] + 1, hinge[2])
    lid_front = _rrect3(lid_o, (1, 0, 0), ey_l, W, L, 10, 6)
    back = tuple(-LT * c for c in n_front)
    lid_back = [(x + back[0], y + back[1], z + back[2]) for x, y, z in lid_front]
    # fit to canvas: longest side ~ 1000 px
    allp = [cam.raw(*q) for q in base_top + base_bot + lid_front + lid_back]
    minx, maxx = min(a for a, _ in allp), max(a for a, _ in allp)
    miny, maxy = min(b for _, b in allp), max(b for _, b in allp)
    k = 960 / (maxx - minx)
    m = 30.0
    cam.k, cam.ox, cam.oy = k, m - minx * k, m - miny * k
    w_total, h_total = (maxx - minx) * k + 2 * m, (maxy - miny) * k + 2 * m + 24

    def P(q):
        return cam(*q)

    def deck(u, v, y=T + 0.2):  # u: -W/2..W/2, v: 0 (front) .. D (hinge)
        return P((u, y, v))

    defs = [
        lin("deck", [(0, lighten(body_c, 0.35)), (0.6, body_c), (1, darken(body_c, 0.12))], 0, 1, 1, 0),
        lin("side", [(0, darken(body_c, 0.18)), (1, darken(body_c, 0.42))], 0, 0, 1, 0),
        lin("lidb", [(0, "#16171a"), (1, "#0a0a0c")]),
        lin("gl", [(0, "#fff", 0.22), (0.45, "#fff", 0.03), (0.6, "#fff", 0)], 0, 0, 1, 1),
        blur("sh14", 14),
        blur("sh4", 4),
        glow("kg", 1.5),
    ]
    body = []
    # shadows on the floor
    floor_pts = [P(q) for q in _rrect3((-W / 2 - 6, 0, D + 8), (1, 0, 0), (0, 0, -1), W + 12, D + 14, 18, 4)]
    body.append(
        f'<path d="{poly_path(floor_pts)}" fill="#000" opacity="0.30" filter="url(#sh14)" transform="translate(0 10)"/>'
    )
    body.append(
        f'<path d="{poly_path([P(q) for q in base_bot])}" fill="#000" opacity="0.45" filter="url(#sh4)" transform="translate(0 3)"/>'
    )
    # base slab
    hull = _hull([P(q) for q in base_top + base_bot])
    body.append(f'<path d="{poly_path(hull)}" fill="url(#side)"/>')
    body.append(f'<path d="{poly_path([P(q) for q in base_top])}" fill="url(#deck)"/>')
    # front lip highlight
    (lx0, ly0), (lx1, ly1) = P((-W / 2 + 14, T, 0.4)), P((W / 2 - 14, T, 0.4))
    body.append(
        f'<line x1="{lx0:.1f}" y1="{ly0:.1f}" x2="{lx1:.1f}" y2="{ly1:.1f}" stroke="#fff" stroke-opacity="0.55" '
        f'stroke-width="1.6"/>'
    )
    # keyboard well + keys
    kx0, kx1, kz0, kz1 = -W * 0.43, W * 0.43, D * 0.40, D * 0.93
    well = _rrect3((kx0 - 3, T + 0.1, kz1 + 3), (1, 0, 0), (0, 0, -1), (kx1 - kx0) + 6, (kz1 - kz0) + 6, 4, 3)
    body.append(f'<path d="{poly_path([P(q) for q in well])}" fill="{darken(body_c, 0.10)}"/>')
    rows = [
        ([1] * 14, 0.62),
        ([1] * 13 + [2], 1),
        ([1.5] + [1] * 12 + [1.5], 1),
        ([1.8] + [1] * 11 + [2.2], 1),
        ([2.3] + [1] * 10 + [2.7], 1),
        ([1.1, 1, 1, 1.3, 5.2, 1.3, 1, 1, 1, 1.1], 1),
    ]
    total_h = sum(hh for _, hh in rows)
    gap = 1.6
    unit_h = ((kz1 - kz0) - gap * (len(rows) - 1)) / total_h
    zc = kz1
    keys = []
    for widths, hh in rows:
        hgt = unit_h * hh
        unit_w = ((kx1 - kx0) - gap * (len(widths) - 1)) / sum(widths)
        xc = kx0
        for wu in widths:
            kw = unit_w * wu
            q = _rrect3((xc, T + 0.5, zc), (1, 0, 0), (0, 0, -1), kw, hgt, 1.2, 1)
            keys.append(f'<path d="{poly_path([P(t) for t in q])}"/>')
            xc += kw + gap
        zc -= hgt + gap
    kfill = key_c
    body.append(f'<g fill="{kfill}">{"".join(keys)}</g>')
    if gaming:  # backlight glow under the keys
        body.append(
            f'<g fill="none" stroke="{accent}" stroke-opacity="0.55" stroke-width="0.8" filter="url(#kg)">'
            f"{''.join(keys)}</g>"
        )
    # trackpad
    tp = _rrect3((-W * 0.19, T + 0.15, D * 0.33), (1, 0, 0), (0, 0, -1), W * 0.38, D * 0.25, 7, 4)
    body.append(
        f'<path d="{poly_path([P(q) for q in tp])}" fill="{lighten(body_c, 0.06) if not dark else lighten(body_c, 0.04)}" '
        f'stroke="{darken(body_c, 0.22)}" stroke-width="1"/>'
    )
    # hinge gap
    hg = [
        P((-W / 2 + 22, T + 0.3, D - 3)),
        P((W / 2 - 22, T + 0.3, D - 3)),
        P((W / 2 - 22, T + 3.5, D - 1)),
        P((-W / 2 + 22, T + 3.5, D - 1)),
    ]
    body.append(f'<polygon points="{pts(hg)}" fill="{darken(body_c, 0.55)}"/>')
    # lid slab: edge (hull), front bezel, screen with wallpaper mapped by an affine transform, gloss
    lh = _hull([P(q) for q in lid_front + lid_back])
    body.append(f'<path d="{poly_path(lh)}" fill="{darken(body_c, 0.15)}"/>')
    body.append(f'<path d="{poly_path([P(q) for q in lid_front])}" fill="url(#lidb)"/>')

    def lidp(u, v):  # u: 0..W, v: 0..L on the lid's front face
        return P(tuple(lid_o[i] + u * (1, 0, 0)[i] + v * ey_l[i] + 0.3 * n_front[i] for i in range(3)))

    bz, chin = 7.0, 14.0
    scr3 = _rrect3(
        tuple(lid_o[i] + bz * (1, 0, 0)[i] + chin * ey_l[i] + 0.3 * n_front[i] for i in range(3)),
        (1, 0, 0),
        ey_l,
        W - 2 * bz,
        L - chin - bz,
        2.5,
        2,
    )
    scr = [P(q) for q in scr3]
    tl, tr, bl = lidp(bz, L - bz), lidp(W - bz, L - bz), lidp(bz, chin)
    a, b_ = (tr[0] - tl[0]) / 1600, (tr[1] - tl[1]) / 1600
    c, d_ = (bl[0] - tl[0]) / 1000, (bl[1] - tl[1]) / 1000
    defs.append(f'<clipPath id="lscr"><path d="{poly_path(scr)}"/></clipPath>')
    wp = screen_fill(p, 11, 0, 0, 1600, 1000, "lpw")
    body.append(f'<path d="{poly_path(scr)}" fill="#000"/>')
    body.append(
        f'<g clip-path="url(#lscr)"><g transform="matrix({a:.5f} {b_:.5f} {c:.5f} {d_:.5f} {tl[0]:.2f} {tl[1]:.2f})">{wp}</g>'
        f'<path d="{poly_path(scr)}" fill="url(#gl)"/></g>'
    )
    cam_dot = lidp(W / 2, L - bz / 2)
    body.append(f'<circle cx="{cam_dot[0]:.1f}" cy="{cam_dot[1]:.1f}" r="1.6" fill="#2b2e33"/>')
    lg0, lg1, lg2 = lidp(W / 2 - 30, chin * 0.30), lidp(W / 2 + 30, chin * 0.30), lidp(W / 2 - 30, chin * 0.30 + 10)
    ma, mb = (lg1[0] - lg0[0]) / 60, (lg1[1] - lg0[1]) / 60
    mc, md = (lg2[0] - lg0[0]) / 10, (lg2[1] - lg0[1]) / 10
    body.append(
        f'<g transform="matrix({ma:.5f} {mb:.5f} {-mc:.5f} {-md:.5f} {lg0[0]:.2f} {lg0[1]:.2f})">'
        f"{text(30, 0, _brand(p).upper(), 4.6, '#8b8f97', 700, spacing=1.2)}</g>"
    )
    return svg_doc(w_total, h_total, defs, body), w_total, h_total


# ---------- soundbar ----------
def svg_soundbar(p: dict) -> tuple[str, float, float]:
    d = _dims(p)
    sp = p.get("specs") or {}
    wmm = _num(d.get("width_mm"), 900)
    hmm = _num(d.get("height_mm"), 70)
    ch = str(sp.get("channels") or "")
    sub = bool(re.search(r"\.\s*1\b", ch)) or "sub" in str(p.get("model") or "").lower()
    base = colour_of(p, "#1f2126")
    bw = 900.0 if sub else 1000.0
    bh = max(52.0, min(110.0, bw * hmm / wmm))
    depth = 26.0
    m = 30.0
    sub_w, sub_h = (230.0, 400.0) if sub else (0.0, 0.0)
    floor = 30 + max(sub_h, bh + depth) + 10
    x0 = m
    y1 = floor
    y0 = y1 - bh
    w_total = x0 + bw + (sub_w * 0.78 if sub else 0) + m
    h_total = floor + 36
    defs = [
        lin("fab", [(0, lighten(base, 0.10)), (0.5, base), (1, darken(base, 0.30))]),
        lin("topb", [(0, darken(base, 0.1)), (1, lighten(base, 0.18))]),
        lin("shine", [(0, "#fff", 0.0), (0.45, "#fff", 0.10), (0.55, "#fff", 0.10), (1, "#fff", 0)], 0, 0, 1, 0),
        f'<pattern id="grille" width="6" height="6" patternUnits="userSpaceOnUse">'
        f'<circle cx="1.5" cy="1.5" r="1.15" fill="{darken(base, 0.55)}"/><circle cx="4.5" cy="4.5" r="1.15" fill="{darken(base, 0.55)}"/></pattern>',
        blur("sh10", 10),
        blur("sh3", 3),
        glow("led", 1.6),
    ]
    body = []
    if sub:
        sx0 = x0 + bw - sub_w * 0.22
        sy1 = floor - 18
        sy0 = sy1 - sub_h
        dd = 34.0
        top = [(sx0, sy0), (sx0 + sub_w, sy0), (sx0 + sub_w - 14, sy0 - dd), (sx0 + 14, sy0 - dd)]
        body += [
            floor_shadow(sx0 + sub_w / 2, sy1 + 4, sub_w * 0.62, 12, 0.30, "sh10"),
            floor_shadow(sx0 + sub_w / 2, sy1 + 2, sub_w * 0.52, 4, 0.40, "sh3"),
            f'<path d="{poly_path(top)}" fill="url(#topb)"/>',
            f'<rect x="{sx0}" y="{sy0}" width="{sub_w}" height="{sub_h}" rx="10" fill="url(#fab)"/>',
            f'<rect x="{sx0 + 8}" y="{sy0 + 8}" width="{sub_w - 16}" height="{sub_h - 16}" rx="7" fill="url(#grille)" opacity="0.9"/>',
            f'<rect x="{sx0}" y="{sy0}" width="{sub_w}" height="{sub_h}" rx="10" fill="url(#shine)"/>',
            f'<rect x="{sx0 + 1}" y="{sy0 + 1}" width="{sub_w - 2}" height="2" rx="1" fill="#fff" opacity="0.18"/>',
            text(sx0 + sub_w / 2, sy1 - 18, _brand(p).upper(), 11, "#9a9da4", 700, spacing=2),
        ]
    # bar: floor shadow, top face, front grille, end caps, logo, LED
    top = [
        (x0 + bh * 0.25, y0),
        (x0 + bw - bh * 0.25, y0),
        (x0 + bw - bh * 0.25 - 10, y0 - depth * 0.6),
        (x0 + bh * 0.25 + 10, y0 - depth * 0.6),
    ]
    body += [
        floor_shadow(x0 + bw / 2, y1 + 6, bw * 0.50, 9, 0.30, "sh10"),
        floor_shadow(x0 + bw / 2, y1 + 2, bw * 0.47, 3, 0.45, "sh3"),
        f'<path d="{poly_path(top)}" fill="url(#topb)" stroke="{darken(base, 0.3)}" stroke-width="0.8"/>',
        f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}" rx="{bh * 0.45:.1f}" fill="url(#fab)"/>',
        f'<rect x="{x0 + bh * 0.3:.1f}" y="{y0 + 5}" width="{bw - bh * 0.6:.1f}" height="{bh - 10}" rx="{bh * 0.3:.1f}" fill="url(#grille)"/>',
        f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}" rx="{bh * 0.45:.1f}" fill="url(#shine)"/>',
        f'<rect x="{x0 + bh * 0.3:.1f}" y="{y0 + 1.5}" width="{bw - bh * 0.6:.1f}" height="1.6" rx="0.8" fill="#fff" opacity="0.25"/>',
    ]
    for u in (0.45, 0.50, 0.55):
        cx_, cy_ = top[3][0] + (top[2][0] - top[3][0]) * u, (top[0][1] + top[3][1]) / 2
        body.append(
            f'<ellipse cx="{cx_:.1f}" cy="{cy_:.1f}" rx="7" ry="2.2" fill="{lighten(base, 0.28)}" opacity="0.8"/>'
        )
    body.append(f'<circle cx="{x0 + bw / 2:.1f}" cy="{y0 + bh - 9:.1f}" r="2.2" fill="#9ff3ff" filter="url(#led)"/>')
    body.append(
        text(x0 + bw - bh * 0.9, y0 + bh / 2 + 4, _brand(p).upper(), 12, "#a2a5ac", 700, anchor="end", spacing=2.4)
    )
    return svg_doc(w_total, h_total, defs, body), w_total, h_total


def svg_wm(p: dict):
    return svg_wm_top(p) if _wm_load(p) == "top" else svg_wm_front(p)


SVG_BUILDERS = {"tv": svg_tv, "ac": svg_ac, "wm": svg_wm, "laptop": svg_laptop, "soundbar": svg_soundbar}


# ---------- post-processing ----------
def finish_png(png: bytes, scale: float = SS):
    """Chromium screenshot (RGBA, supersampled) → tight crop + small margin, longest side TARGET px."""
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = im.getchannel("A").point(lambda a: 255 if a > 3 else 0)
    bbox = alpha.getbbox() or (0, 0, im.width, im.height)
    pad = int(8 * scale)
    bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(im.width, bbox[2] + pad), min(im.height, bbox[3] + pad))
    im = im.crop(bbox)
    k = TARGET / max(im.width, im.height)
    if k < 1:
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    return im


async def _prerender_walls(ctx, products: list[dict]) -> None:
    """Render each wallpaper used by TVs/laptops once (1600×900 JPEG) so product renders stay fast."""
    keys = set()
    for p in products:
        cat = str(p.get("category") or "").lower()
        if cat == "tv":
            keys.add(wall_key(p, 0))
        elif cat == "laptop":
            keys.add(wall_key(p, 11))
    if not keys:
        return
    import base64

    page = await ctx.new_page()
    try:
        await page.set_viewport_size({"width": 1600, "height": 900})
        for pal, var in sorted(keys):
            if (pal, var) in WALLS:
                continue
            svg = wallpaper_svg(1600, 900, pal, wall_seed(var), prefix="w")
            await page.set_content(f"<html><body style='margin:0'>{svg}</body></html>", wait_until="load")
            jpg = await page.screenshot(type="jpeg", quality=92, clip={"x": 0, "y": 0, "width": 1600, "height": 900})
            WALLS[(pal, var)] = "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
    finally:
        await page.close()


async def render_all(products: list[dict], out: Path, workers: int = 4) -> list[str]:
    """Rasterise every product SVG with Chromium (transparent background). Returns the SKUs written."""
    from playwright.async_api import async_playwright

    jobs: asyncio.Queue = asyncio.Queue()
    for p in products:
        jobs.put_nowait(p)
    done: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            wctx = await browser.new_context(device_scale_factor=1)
            await _prerender_walls(wctx, products)
            await wctx.close()
            ctx = await browser.new_context(device_scale_factor=SS, viewport={"width": 1200, "height": 1200})

            async def worker():
                page = await ctx.new_page()
                while not jobs.empty():
                    p = jobs.get_nowait()
                    cat = str(p.get("category") or "").lower()
                    svg, w, h = SVG_BUILDERS[cat](p)
                    w, h = math.ceil(w), math.ceil(h)
                    await page.set_viewport_size({"width": w, "height": h})
                    await page.set_content(
                        "<!doctype html><html><head><style>html,body{margin:0;padding:0;background:transparent}"
                        f"svg{{display:block}}</style></head><body>{svg}</body></html>",
                        wait_until="load",
                    )
                    png = await page.screenshot(
                        type="png", omit_background=True, clip={"x": 0, "y": 0, "width": w, "height": h}
                    )
                    finish_png(png).save(out / f"{p['sku']}.png", compress_level=7)
                    done.append(str(p["sku"]))
                await page.close()

            await asyncio.gather(*(worker() for _ in range(max(1, min(workers, len(products))))))
        finally:
            await browser.close()
    return done


# ---------- Pillow fallback (no Chromium) ----------
def _pil_font(size: int):
    from PIL import ImageFont

    for f in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _vgrad(w: int, h: int, top: str, bottom: str):
    from PIL import Image

    g = Image.linear_gradient("L").resize((w, h))
    return Image.composite(
        Image.new("RGBA", (w, h), _rgb(bottom) + (255,)), Image.new("RGBA", (w, h), _rgb(top) + (255,)), g
    )


def pillow_product(p: dict):
    """Plain but tidy studio drawing with Pillow (used only when Chromium is unavailable)."""
    from PIL import Image, ImageDraw, ImageFilter

    cat = str(p.get("category") or "").lower()
    d = _dims(p)
    brand = _brand(p)
    f = _pil_font(26)

    def canvas(w, h):
        return Image.new("RGBA", (w, h), (0, 0, 0, 0))

    def shadow(img, box, blur_r=14, alpha=80):
        sh = canvas(*img.size)
        ImageDraw.Draw(sh).ellipse(box, fill=(0, 0, 0, alpha))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur_r)))

    def paste_grad(img, box, top, bottom, radius):
        x0, y0, x1, y1 = (int(v) for v in box)
        g = _vgrad(x1 - x0, y1 - y0, top, bottom)
        mask = Image.new("L", g.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, g.width - 1, g.height - 1], radius=radius, fill=255)
        img.paste(g, (x0, y0), mask)

    if cat == "tv":
        wmm, hmm = _num(d.get("width_mm"), 1230), _num(d.get("height_mm"), 708)
        W, H = 1000, int(1000 * min(0.75, max(0.45, hmm / wmm)))
        img = canvas(W + 60, H + 110)
        shadow(img, [60, H + 70, W, H + 96])
        dr = ImageDraw.Draw(img)
        for fx, sg in ((30 + W * 0.12, -1), (30 + W * 0.88, 1)):
            dr.polygon(
                [(fx - 5, H + 20), (fx + 5, H + 20), (fx + 5 + sg * 40, H + 80), (fx - 5 + sg * 40, H + 80)],
                fill=(40, 42, 46, 255),
            )
        dr.rounded_rectangle([30, 20, 30 + W, 20 + H], radius=8, fill=(20, 21, 24, 255))
        pal = {"arka": ("#ff8a3d", "#a1124f"), "meru": ("#00b4ff", "#061a40"), "novex": ("#e040fb", "#14072e")}.get(
            brand.lower(), ("#3d8bfd", "#1b2a4a")
        )
        paste_grad(img, [37, 27, 23 + W, 9 + H], pal[0], pal[1], 2)
        gl = canvas(*img.size)
        ImageDraw.Draw(gl).polygon(
            [(37, 27), (37 + W * 0.45, 27), (37 + W * 0.15, 9 + H), (37, 9 + H)], fill=(255, 255, 255, 22)
        )
        img.alpha_composite(gl)
        return img
    if cat == "ac":
        W, H = 1000, 300
        img = canvas(W + 60, H + 90)
        shadow(img, [80, H + 40, W - 20, H + 70], alpha=60)
        paste_grad(img, [30, 20, 30 + W, 20 + H], "#ffffff", "#dcdfe4", 36)
        dr = ImageDraw.Draw(img)
        dr.rounded_rectangle([85, 20 + H * 0.70, W - 25, 20 + H * 0.92], radius=12, fill=(38, 42, 48, 255))
        dr.rounded_rectangle([85, 20 + H * 0.78, W - 25, 20 + H * 0.86], radius=8, fill=(225, 228, 232, 255))
        dr.text((30 + W / 2, 20 + H * 0.45), brand, font=f, fill=(140, 146, 155, 255), anchor="mm")
        dr.text((W - 120, 20 + H * 0.38), "24°", font=_pil_font(34), fill=(110, 205, 255, 255), anchor="mm")
        return img
    if cat == "wm":
        top = _wm_load(p) == "top"
        W, H = (520, 900) if top else (640, 900)
        img = canvas(W + 60, H + 70)
        shadow(img, [40, H + 10, W + 20, H + 50])
        paste_grad(img, [30, 20, 30 + W, 20 + H], "#ffffff", "#e3e5e9", 24)
        dr = ImageDraw.Draw(img)
        dr.line([(30, 20 + H * 0.15), (30 + W, 20 + H * 0.15)], fill=(200, 203, 208, 255), width=3)
        dr.text((60, 20 + H * 0.19), brand, font=f, fill=(140, 146, 155, 255))
        if top:
            dr.rounded_rectangle([70, 40, W - 10, 20 + H * 0.12], radius=12, fill=(48, 58, 68, 255))
        else:
            cx, cy, r = 30 + W / 2, 20 + H * 0.56, W * 0.37
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(196, 201, 208, 255))
            dr.ellipse([cx - r * 0.8, cy - r * 0.8, cx + r * 0.8, cy + r * 0.8], fill=(44, 58, 72, 255))
            dr.ellipse([cx - r * 0.66, cy - r * 0.66, cx + r * 0.66, cy + r * 0.66], fill=(120, 130, 140, 255))
            hl = canvas(*img.size)
            ImageDraw.Draw(hl).arc(
                [cx - r * 0.72, cy - r * 0.72, cx + r * 0.72, cy + r * 0.72],
                195,
                255,
                fill=(255, 255, 255, 120),
                width=10,
            )
            img.alpha_composite(hl.filter(ImageFilter.GaussianBlur(2)))
        return img
    if cat == "laptop":
        img = canvas(1000, 700)
        shadow(img, [80, 600, 920, 660])
        dr = ImageDraw.Draw(img)
        dr.polygon([(120, 480), (880, 480), (960, 600), (40, 600)], fill=(196, 200, 207, 255))
        dr.rounded_rectangle([40, 596, 960, 616], radius=8, fill=(160, 164, 172, 255))
        for r_ in range(5):
            y = 500 + r_ * 16
            dr.line([(170 - r_ * 10, y), (830 + r_ * 10, y)], fill=(40, 42, 46, 255), width=9)
        dr.rounded_rectangle([170, 40, 830, 480], radius=18, fill=(16, 17, 20, 255))
        paste_grad(img, [186, 56, 814, 456], "#3d8bfd", "#1b2a4a", 4)
        return img
    if cat == "soundbar":
        img = canvas(1000, 200)
        shadow(img, [60, 130, 940, 160])
        paste_grad(img, [30, 60, 970, 140], "#3a3c42", "#16171a", 38)
        dr = ImageDraw.Draw(img)
        for x in range(70, 930, 8):
            for y in (78, 90, 102, 114, 126):
                dr.ellipse([x, y, x + 3, y + 3], fill=(18, 18, 20, 255))
        dr.text((880, 100), brand.upper(), font=_pil_font(14), fill=(160, 162, 168, 255), anchor="rm")
        return img
    return None


def load_products(seed: Path = SEED) -> list[dict]:
    if seed.exists():
        try:
            data = yaml.safe_load(seed.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("products") or []
            if isinstance(data, list) and data:
                return [p for p in data if isinstance(p, dict)]
        except Exception as e:  # malformed seed → built-in list
            print(f"warning: could not read {seed}: {e}", file=sys.stderr)
    return BUILTIN


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Draw studio-style product PNGs per SKU.")
    ap.add_argument("--out", type=Path, default=OUT, help="output folder (default: render/static/img/products)")
    ap.add_argument("--seed", type=Path, default=SEED, help="products YAML (default: data/seed/products.yaml)")
    ap.add_argument("--only", default="", help="comma-separated SKUs to draw")
    ap.add_argument("--pillow", action="store_true", help="skip Chromium and use the Pillow drawing")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    products = []
    for p in load_products(args.seed):
        sku, cat = p.get("sku"), str(p.get("category") or "").lower()
        if not sku or (only and sku not in only):
            continue
        if cat not in SVG_BUILDERS:
            print(f"skip {sku}: no drawing for category {cat!r}", file=sys.stderr)
            continue
        products.append(p)
    args.out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    how = "pillow"
    if products and not args.pillow:
        try:
            written = asyncio.run(render_all(products, args.out, args.workers))
            how = "chromium"
        except Exception as e:  # Chromium missing / crashed → Pillow for whatever is left
            print(f"warning: Chromium render failed ({e.__class__.__name__}: {e}); using Pillow", file=sys.stderr)
    for p in products:
        if str(p["sku"]) in written:
            continue
        img = pillow_product(p)
        if img is not None:
            bbox = img.getchannel("A").getbbox()
            if bbox:
                img = img.crop(bbox)
            img.save(args.out / f"{p['sku']}.png", compress_level=7)
            written.append(str(p["sku"]))
    print(f"wrote {len(written)} product images to {args.out} ({how})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
