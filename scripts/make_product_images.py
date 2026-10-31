"""Draw placeholder product PNGs per SKU (spec 16.4) into chanakya/render/static/img/products/<sku>.png.

Reads data/seed/products.yaml when present; otherwise uses the built-in SKU list from spec 9.2.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "chanakya" / "render" / "static" / "img" / "products"
SEED = ROOT / "data" / "seed" / "products.yaml"

BUILTIN = [
    *[
        {"sku": s, "category": "tv", "brand": b, "model": m, "dims": {"width_mm": w, "height_mm": h}}
        for s, b, m, w, h in [
            ("TV-ARK-43-4K60", "Arka", "Arka 43 4K", 963, 560),
            ("TV-ARK-50-4K60", "Arka", "Arka 50 4K", 1117, 646),
            ("TV-ARK-55-4K60", "Arka", "Arka 55 4K", 1240, 720),
            ("TV-MER-55-QL60", "Meru", "Meru 55 QLED", 1230, 710),
            ("TV-MER-55-QL120", "Meru", "Meru 55 QLED Pro", 1230, 708),
            ("TV-NOV-55-OL120", "Novex", "Novex 55 OLED", 1228, 708),
            ("TV-ARK-65-4K60", "Arka", "Arka 65 4K", 1460, 845),
            ("TV-MER-65-QL120", "Meru", "Meru 65 QLED Pro", 1450, 835),
            ("TV-NOV-65-ML120", "Novex", "Novex 65 Mini LED", 1447, 832),
            ("TV-MER-75-QL120", "Meru", "Meru 75 QLED Pro", 1672, 960),
        ]
    ],
    {
        "sku": "SB-MER-21",
        "category": "soundbar",
        "brand": "Meru",
        "model": "Meru Soundbar 2.1",
        "dims": {"width_mm": 900, "height_mm": 70},
    },
    *[
        {"sku": s, "category": "ac", "brand": b, "model": m, "dims": {"width_mm": w, "height_mm": h}}
        for s, b, m, w, h in [
            ("AC-ARK-10-3S", "Arka", "Arka 1.0T 3-star", 850, 290),
            ("AC-MER-15-3S", "Meru", "Meru 1.5T 3-star", 970, 300),
            ("AC-MER-15-5S", "Meru", "Meru 1.5T 5-star", 970, 300),
            ("AC-NOV-15-5S", "Novex", "Novex 1.5T 5-star", 990, 310),
            ("AC-ARK-20-3S", "Arka", "Arka 2.0T 3-star", 1080, 330),
            ("AC-MER-20-5S", "Meru", "Meru 2.0T 5-star", 1080, 330),
        ]
    ],
    *[
        {
            "sku": s,
            "category": "wm",
            "brand": b,
            "model": m,
            "specs": {"load": ld},
            "dims": {"width_mm": w, "depth_mm": d},
        }
        for s, b, m, ld, w, d in [
            ("WM-ARK-65-TL", "Arka", "Arka 6.5 kg Top", "top", 540, 560),
            ("WM-MER-7-TL", "Meru", "Meru 7 kg Top", "top", 560, 580),
            ("WM-ARK-7-FL", "Arka", "Arka 7 kg Front", "front", 595, 550),
            ("WM-MER-8-FL", "Meru", "Meru 8 kg Front", "front", 600, 565),
            ("WM-MER-10-TL", "Meru", "Meru 10 kg Top", "top", 600, 620),
            ("WM-NOV-9-FL", "Novex", "Novex 9 kg Front", "front", 600, 600),
        ]
    ],
    *[
        {"sku": s, "category": "laptop", "brand": b, "model": m}
        for s, b, m in [
            ("LP-ARK-E-8", "Arka", "Arka Book 14"),
            ("LP-ARK-M-8", "Arka", "Arka Book 15"),
            ("LP-MER-M-16", "Meru", "Meru Air 14"),
            ("LP-MER-H-16U", "Meru", "Meru Air Ultra"),
            ("LP-NOV-H-16G", "Novex", "Novex Pro 16"),
            ("LP-NOV-H-32G", "Novex", "Novex Pro Max"),
        ]
    ],
]

BRAND_COL = {"Arka": (232, 131, 58), "Meru": (64, 140, 220), "Novex": (150, 90, 200)}


def font(size: int):
    for p in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _canvas(w: int, h: int) -> Image.Image:
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def _shadow(img: Image.Image, box, radius: int) -> None:
    sh = _canvas(*img.size)
    ImageDraw.Draw(sh).rounded_rectangle(box, radius=radius, fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    img.alpha_composite(sh, (0, 10))


def draw_tv(p: dict) -> Image.Image:
    dims = p.get("dims") or {}
    wmm, hmm = float(dims.get("width_mm") or 1230), float(dims.get("height_mm") or 708)
    W = 1000
    H = int(W * hmm / wmm)
    pad = 30
    img = _canvas(W + 2 * pad, H + 2 * pad + 20)
    box = [pad, pad, pad + W, pad + H]
    _shadow(img, box, 16)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=16, fill=(17, 17, 17, 255))
    bz = 14
    sx0, sy0, sx1, sy1 = box[0] + bz, box[1] + bz, box[2] - bz, box[3] - bz - 10
    grad = Image.new("RGBA", (sx1 - sx0, sy1 - sy0))
    gd = ImageDraw.Draw(grad)
    col = BRAND_COL.get(p.get("brand"), (80, 160, 220))
    for y in range(grad.height):
        t = y / max(1, grad.height - 1)
        r = int(40 + (col[0] - 40) * t * 0.9 + 120 * (1 - t))
        g = int(80 + (col[1] - 80) * t * 0.9 + 110 * (1 - t))
        b = int(160 + (col[2] - 160) * t * 0.9 + 80 * (1 - t))
        gd.line([(0, y), (grad.width, y)], fill=(min(r, 255), min(g, 255), min(b, 255), 255))
    img.alpha_composite(grad, (sx0, sy0))
    d.line([(sx0 + 4, sy0 + 4), (sx1 - 4, sy0 + 4)], fill=(255, 255, 255, 120), width=3)
    f = font(max(28, H // 9))
    brand = p.get("brand") or ""
    tw = d.textlength(brand, font=f)
    d.text(((sx0 + sx1 - tw) / 2, (sy0 + sy1) / 2 - f.size / 2), brand, font=f, fill=(255, 255, 255, 235))
    f2 = font(18)
    d.text(
        (box[0] + W / 2 - d.textlength(brand.upper(), font=f2) / 2, box[3] - bz - 6),
        brand.upper(),
        font=f2,
        fill=(170, 170, 170, 255),
    )
    return img


def draw_soundbar(p: dict) -> Image.Image:
    img = _canvas(1000, 220)
    box = [40, 70, 960, 150]
    _shadow(img, box, 30)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=30, fill=(30, 30, 34, 255))
    for x in range(80, 920, 14):
        for y in (90, 104, 118, 132):
            d.ellipse([x, y, x + 5, y + 5], fill=(70, 70, 78, 255))
    f = font(26)
    d.text((470, 160), p.get("brand") or "", font=f, fill=(90, 90, 90, 255))
    return img


def draw_ac(p: dict) -> Image.Image:
    dims = p.get("dims") or {}
    wmm, hmm = float(dims.get("width_mm") or 970), float(dims.get("height_mm") or 300)
    W = 1000
    H = max(220, int(W * hmm / wmm))
    img = _canvas(W + 60, H + 80)
    box = [30, 30, 30 + W, 30 + H]
    _shadow(img, box, 40)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=40, fill=(248, 248, 248, 255), outline=(210, 210, 214, 255), width=3)
    vy = box[3] - 55
    d.rounded_rectangle([box[0] + 60, vy, box[2] - 60, vy + 22], radius=11, fill=(200, 203, 210, 255))
    d.line([(box[0] + 80, vy + 11), (box[2] - 80, vy + 11)], fill=(160, 165, 175, 255), width=2)
    col = BRAND_COL.get(p.get("brand"), (64, 140, 220))
    d.ellipse([box[2] - 110, box[1] + 40, box[2] - 94, box[1] + 56], fill=col + (255,))
    f = font(34)
    d.text((box[0] + 60, box[1] + 36), p.get("brand") or "", font=f, fill=(120, 120, 128, 255))
    return img


def draw_wm(p: dict) -> Image.Image:
    load = ((p.get("specs") or {}).get("load") or "front").lower()
    W, H = 640, 760
    img = _canvas(W + 60, H + 60)
    box = [30, 30, 30 + W, 30 + H]
    _shadow(img, box, 30)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=30, fill=(250, 250, 250, 255), outline=(205, 205, 210, 255), width=3)
    d.line([(box[0], box[1] + 120), (box[2], box[1] + 120)], fill=(215, 215, 220, 255), width=3)
    col = BRAND_COL.get(p.get("brand"), (64, 140, 220))
    d.rounded_rectangle([box[2] - 240, box[1] + 40, box[2] - 50, box[1] + 80], radius=10, fill=(40, 44, 52, 255))
    d.ellipse([box[0] + 50, box[1] + 42, box[0] + 86, box[1] + 78], fill=col + (255,))
    cx, cy = (box[0] + box[2]) // 2, box[1] + 120 + (H - 120) // 2
    if load == "top":
        d.rounded_rectangle(
            [box[0] + 40, box[1] + 10, box[2] - 40, box[1] + 110],
            radius=20,
            fill=(235, 238, 242, 255),
            outline=(190, 195, 205, 255),
            width=3,
        )
        d.text((cx - 90, cy - 20), p.get("brand") or "", font=font(44), fill=(170, 170, 178, 255))
    else:
        r = 210
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(200, 205, 214, 255))
        r2 = 160
        d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], fill=(70, 110, 150, 255))
        d.ellipse([cx - r2 + 30, cy - r2 + 30, cx - r2 + 90, cy - r2 + 90], fill=(255, 255, 255, 90))
    return img


def draw_laptop(p: dict) -> Image.Image:
    img = _canvas(1000, 720)
    d = ImageDraw.Draw(img)
    scr = [170, 60, 830, 480]
    d.rounded_rectangle(scr, radius=24, fill=(40, 42, 48, 255))
    col = BRAND_COL.get(p.get("brand"), (64, 140, 220))
    inner = [scr[0] + 20, scr[1] + 20, scr[2] - 20, scr[3] - 20]
    grad = Image.new("RGBA", (inner[2] - inner[0], inner[3] - inner[1]))
    gd = ImageDraw.Draw(grad)
    for y in range(grad.height):
        t = y / max(1, grad.height - 1)
        gd.line(
            [(0, y), (grad.width, y)],
            fill=(int(col[0] * (0.6 + 0.4 * t)), int(col[1] * (0.6 + 0.4 * t)), int(col[2] * (0.6 + 0.4 * t)), 255),
        )
    img.alpha_composite(grad, (inner[0], inner[1]))
    d.polygon([(110, 500), (890, 500), (960, 600), (40, 600)], fill=(205, 208, 214, 255), outline=(170, 172, 178, 255))
    d.rounded_rectangle([40, 596, 960, 620], radius=10, fill=(180, 183, 190, 255))
    d.rounded_rectangle([420, 560, 580, 590], radius=6, fill=(190, 193, 200, 255))
    f = font(40)
    b = p.get("brand") or ""
    d.text((500 - d.textlength(b, font=f) / 2, 250), b, font=f, fill=(255, 255, 255, 230))
    return img


DRAW = {"tv": draw_tv, "soundbar": draw_soundbar, "ac": draw_ac, "wm": draw_wm, "laptop": draw_laptop}


def load_products() -> list[dict]:
    if SEED.exists():
        try:
            data = yaml.safe_load(SEED.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("products") or []
            if isinstance(data, list) and data:
                return data
        except Exception as e:  # malformed seed → built-in list
            print(f"warning: could not read {SEED}: {e}", file=sys.stderr)
    return BUILTIN


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in load_products():
        sku, cat = p.get("sku"), (p.get("category") or "").lower()
        if not sku or cat not in DRAW:
            continue
        DRAW[cat](p).save(OUT / f"{sku}.png", optimize=True)
        n += 1
    print(f"wrote {n} product images to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
