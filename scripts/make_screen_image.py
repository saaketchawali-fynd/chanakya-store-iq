"""Vivid abstract wallpaper (smooth colour waves) → chanakya/render/static/img/screen_bright.jpg (1600×900).

The try-on pastes it into the TV screen, and ``make_product_images.py`` reuses :func:`wallpaper_svg` for the
TV / laptop screens. The SVG is rasterised with Playwright/Chromium; a numpy/Pillow drawing is the fallback
when Chromium is unavailable, so the script never fails a build.

    python scripts/make_screen_image.py [--out PATH] [--palette bright] [--pillow]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "chanakya" / "render" / "static" / "img" / "screen_bright.jpg"
W, H = 1600, 900

# Each palette: diagonal background (3 stops), soft glows, and silk ribbons (start colour → end colour).
PALETTES: dict[str, dict] = {
    "bright": {  # try-on default: luminous blue → violet → pink, warm and cool ribbons
        "bg": ("#1748ff", "#6a2bff", "#ff3d8b"),
        "glows": ("#35e0ff", "#ff6ad5", "#ffc95e"),
        "ribbons": (
            ("#00d8ff", "#3d5afe"),
            ("#7c4dff", "#e040fb"),
            ("#ff4f8b", "#ffb340"),
            ("#ffd54f", "#ff7043"),
            ("#64ffda", "#40c4ff"),
        ),
    },
    "sunrise": {  # Arka: warm dawn
        "bg": ("#2a0b4a", "#a1124f", "#ff7a2f"),
        "glows": ("#ff9a3c", "#ff3d6e", "#ffd86e"),
        "ribbons": (
            ("#ff6a3d", "#ffb13b"),
            ("#ff2e63", "#ff8a5c"),
            ("#ffd166", "#ff7b54"),
            ("#c13584", "#ff5e62"),
            ("#ffe29a", "#ffa94d"),
        ),
    },
    "ocean": {  # Meru: deep sea to teal
        "bg": ("#061a40", "#0353a4", "#00a6c8"),
        "glows": ("#23d5ff", "#3a86ff", "#7cf5d4"),
        "ribbons": (
            ("#00c6ff", "#0072ff"),
            ("#43e97b", "#38f9d7"),
            ("#4facfe", "#00f2fe"),
            ("#5f72ff", "#9a5cff"),
            ("#a8ffec", "#3ad7ff"),
        ),
    },
    "aurora": {  # Novex: violet and magenta
        "bg": ("#14072e", "#4a148c", "#c2185b"),
        "glows": ("#d36bff", "#ff5fa2", "#6f7cff"),
        "ribbons": (
            ("#b06ab3", "#4568dc"),
            ("#ff5f9e", "#ff9472"),
            ("#8e2de2", "#e040fb"),
            ("#f8a5ff", "#7f7fd5"),
            ("#ffc3a0", "#ff6f91"),
        ),
    },
    "lagoon": {  # spare: emerald and blue
        "bg": ("#04293a", "#064663", "#0f9b8e"),
        "glows": ("#2ef2b0", "#3fa7ff", "#d2ff72"),
        "ribbons": (
            ("#11998e", "#38ef7d"),
            ("#36d1dc", "#5b86e5"),
            ("#c6ff4f", "#2ed8a3"),
            ("#00b4db", "#0083b0"),
            ("#a6fff2", "#4fc3f7"),
        ),
    },
}


def _smooth_path(points: list[tuple[float, float]], start_cmd: str = "M") -> str:
    """Catmull-Rom spline through points → SVG cubic Bézier commands."""
    if len(points) < 2:
        return ""
    out = [f"{start_cmd}{points[0][0]:.1f},{points[0][1]:.1f}"]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        out.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return " ".join(out)


def _edges(w: float, h: float, rnd: random.Random, yc: float, amp: float, thick: float, tilt: float, twist: bool):
    """Top and bottom edge polylines of a flowing ribbon; with ``twist`` the edges cross (satin twist)."""
    f1, f2 = rnd.uniform(0.6, 1.1), rnd.uniform(1.3, 2.0)
    p1, p2, p3 = rnd.uniform(0, math.tau), rnd.uniform(0, math.tau), rnd.uniform(0, math.tau)
    top, bot = [], []
    n = 16
    for i in range(n + 1):
        t = i / n
        x = -0.15 * w + t * 1.3 * w
        c = yc + tilt * (t - 0.5) * h
        c += amp * h * (0.75 * math.sin(math.tau * f1 * t + p1) + 0.25 * math.sin(math.tau * f2 * t + p2))
        if twist:
            th = thick * h * math.sin(math.tau * 0.75 * t + p3)  # changes sign → edges cross
        else:
            th = thick * h * (0.6 + 0.4 * math.sin(math.tau * 0.8 * t + p3))
        top.append((x, c - th / 2))
        bot.append((x, c + th / 2))
    return top, bot


def wallpaper_svg(
    w: int = W, h: int = H, palette: str = "bright", seed: int = 7, prefix: str = "wp", standalone: bool = True
) -> str:
    """Smooth colour-wave wallpaper as SVG. ``standalone=False`` returns a nested <svg> (viewBox w×h) that
    fills its parent box (preserveAspectRatio slice) — used for TV and laptop screens."""
    pal = PALETTES.get(palette) or PALETTES["bright"]
    rnd = random.Random(seed)
    p = prefix
    bg0, bg1, bg2 = pal["bg"]
    defs = [
        f'<linearGradient id="{p}bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{bg0}"/><stop offset="0.55" stop-color="{bg1}"/>'
        f'<stop offset="1" stop-color="{bg2}"/></linearGradient>',
        f'<filter id="{p}b60" x="-50%" y="-50%" width="200%" height="200%">'
        f'<feGaussianBlur stdDeviation="{0.07 * h:.1f}"/></filter>',
        f'<filter id="{p}b16" x="-20%" y="-60%" width="140%" height="220%">'
        f'<feGaussianBlur stdDeviation="{0.018 * h:.1f}"/></filter>',
        f'<filter id="{p}glow" x="-20%" y="-60%" width="140%" height="220%">'
        f'<feGaussianBlur stdDeviation="{0.006 * h:.1f}" result="b"/>'
        f'<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>',
        f'<radialGradient id="{p}vig" cx="0.5" cy="0.45" r="0.78">'
        f'<stop offset="0.62" stop-color="#000" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="#000" stop-opacity="0.30"/></radialGradient>',
    ]
    body = [f'<rect width="{w}" height="{h}" fill="url(#{p}bg)"/>']
    for i, col in enumerate(pal["glows"]):  # soft colour glows
        cx, cy = rnd.uniform(0.1, 0.9) * w, rnd.uniform(0.15, 0.85) * h
        r = rnd.uniform(0.28, 0.42) * h
        body.append(
            f'<ellipse cx="{cx:.0f}" cy="{cy:.0f}" rx="{r * 1.7:.0f}" ry="{r:.0f}" fill="{col}" '
            f'opacity="{0.6 if i == 0 else 0.45}" filter="url(#{p}b60)"/>'
        )
    ribbons = list(pal["ribbons"])
    rnd.shuffle(ribbons)
    # (centre y, amplitude, thickness, tilt, kind): soft = wide blurred band; satin = fine strands that twist
    specs = [
        (0.24, 0.10, 0.36, 0.30, "soft"),
        (0.76, 0.08, 0.34, -0.25, "soft"),
        (0.44, 0.09, 0.26, 0.45, "satin"),
        (0.62, 0.07, 0.20, -0.40, "satin"),
        (0.34, 0.05, 0.10, 0.10, "satin"),
    ]
    for i, ((c0, c1), (yc, amp, th, tilt, kind)) in enumerate(zip(ribbons, specs, strict=False)):
        yc += rnd.uniform(-0.04, 0.04)
        gid = f"{p}r{i}"
        defs.append(
            f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="{w}" y2="{0.25 * h:.0f}">'
            f'<stop offset="0" stop-color="{c0}"/><stop offset="1" stop-color="{c1}"/></linearGradient>'
        )
        top, bot = _edges(w, h, rnd, yc * h, amp, th, tilt, twist=(kind == "satin"))
        if kind == "soft":
            d = _smooth_path(top) + " " + _smooth_path(bot[::-1], "L") + " Z"
            body.append(f'<path d="{d}" fill="url(#{gid})" opacity="0.62" filter="url(#{p}b16)"/>')
            continue
        # satin: base fill, then bands between strands lightened/darkened across the width → silk shading
        d = _smooth_path(top) + " " + _smooth_path(bot[::-1], "L") + " Z"
        body.append(f'<path d="{d}" fill="url(#{gid})" opacity="0.55" filter="url(#{p}b16)"/>')
        k_n = 36
        lines = [
            [(a[0], a[1] + (b[1] - a[1]) * (k / k_n)) for a, b in zip(top, bot, strict=True)] for k in range(k_n + 1)
        ]
        bands = [f'<path d="{d}" fill="url(#{gid})"/>']
        for k in range(k_n):
            u = (k + 0.5) / k_n
            shade = 0.50 * math.exp(-(((u - 0.24) / 0.14) ** 2)) - 0.30 * math.exp(-(((u - 0.86) / 0.16) ** 2))
            if abs(shade) < 0.03:
                continue
            band = _smooth_path(lines[k]) + " " + _smooth_path(lines[k + 1][::-1], "L") + " Z"
            col = "#fff" if shade > 0 else "#000"
            bands.append(f'<path d="{band}" fill="{col}" fill-opacity="{abs(shade):.2f}"/>')
        bands.append(
            f'<path d="{_smooth_path(lines[k_n * 2 // 9])}" fill="none" stroke="#fff" stroke-opacity="0.55" '
            f'stroke-width="{max(1.0, 0.002 * h):.1f}"/>'
        )
        body.append(f'<g opacity="0.92">{"".join(bands)}</g>')
    body.append(f'<rect width="{w}" height="{h}" fill="url(#{p}vig)"/>')
    inner = f"<defs>{''.join(defs)}</defs>{''.join(body)}"
    if standalone:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{inner}</svg>'
    return f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice">{inner}</svg>'


# ---------- rasterise ----------
async def rasterise_svg(svg: str, w: int, h: int, scale: float = 1.0, transparent: bool = False) -> bytes:
    """Render an SVG string to PNG bytes with headless Chromium (raises if Chromium is unavailable)."""
    from playwright.async_api import async_playwright

    html = (
        "<!doctype html><html><head><style>html,body{margin:0;padding:0;background:transparent}"
        f"svg{{display:block}}</style></head><body>{svg}</body></html>"
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = await browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=scale)
            await page.set_content(html, wait_until="load")
            return await page.screenshot(
                type="png", omit_background=transparent, clip={"x": 0, "y": 0, "width": w, "height": h}
            )
        finally:
            await browser.close()


def pillow_wallpaper(w: int = W, h: int = H, palette: str = "bright", seed: int = 7):
    """Fallback without Chromium: smooth sinusoidal colour field (numpy) or a plain gradient."""
    from PIL import Image, ImageFilter

    pal = PALETTES.get(palette) or PALETTES["bright"]

    def rgb(hx: str) -> tuple[int, int, int]:
        hx = hx.lstrip("#")
        return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)

    stops = [rgb(c) for c in (*pal["bg"], pal["ribbons"][0][0], pal["ribbons"][2][1], pal["glows"][0])]
    try:
        import numpy as np

        rnd = random.Random(seed)
        sw, sh = w // 4, h // 4
        y, x = np.mgrid[0:sh, 0:sw]
        u, v = x / sw, y / sh
        field = 0.45 * (u + v)
        for _ in range(3):
            fx, fy, ph = rnd.uniform(1.0, 2.2), rnd.uniform(0.6, 1.6), rnd.uniform(0, math.tau)
            field = field + 0.18 * np.sin(math.tau * (fx * u + 0.3 * np.sin(math.tau * fy * v + ph)) + ph)
        field = (field - field.min()) / max(1e-6, float(field.max() - field.min()))
        pos = field * (len(stops) - 1)
        i0 = np.clip(np.floor(pos).astype(int), 0, len(stops) - 2)
        t = (pos - i0)[..., None]
        arr = np.array(stops, dtype=float)
        img = arr[i0] * (1 - t) + arr[i0 + 1] * t
        im = Image.fromarray(img.clip(0, 255).astype("uint8"), "RGB").resize((w, h), Image.BICUBIC)
        return im.filter(ImageFilter.GaussianBlur(2))
    except Exception:
        im = Image.new("RGB", (w, h))
        px = im.load()
        a, b = stops[0], stops[2]
        for yy in range(h):
            for xx in range(0, w, 4):
                t = (xx / w + yy / h) / 2
                c = tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3))
                for dx in range(4):
                    if xx + dx < w:
                        px[xx + dx, yy] = c
        return im


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--palette", default="bright", choices=sorted(PALETTES))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--pillow", action="store_true", help="skip Chromium, use the Pillow fallback")
    args = ap.parse_args(argv)
    from PIL import Image

    img = None
    how = "pillow"
    if not args.pillow:
        try:
            png = asyncio.run(rasterise_svg(wallpaper_svg(W, H, args.palette, args.seed), W, H))
            img = Image.open(io.BytesIO(png)).convert("RGB")
            how = "chromium"
        except Exception as e:  # Chromium missing / failed → fallback
            print(f"warning: Chromium render failed ({e.__class__.__name__}: {e}); using Pillow", file=sys.stderr)
    if img is None:
        img = pillow_wallpaper(W, H, args.palette, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.out, format="JPEG", quality=92, optimize=True, progressive=True)
    print(f"wrote {args.out} ({how}, {W}x{H})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
