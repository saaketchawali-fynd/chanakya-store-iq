"""Draw the bright scene used as TV screen content in try-on: chanakya/render/static/img/screen_bright.jpg."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "chanakya" / "render" / "static" / "img" / "screen_bright.jpg"
W, H = 1600, 900


def main() -> int:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    horizon = int(H * 0.62)
    for y in range(horizon):  # sky gradient
        t = y / horizon
        d.line([(0, y), (W, y)], fill=(int(70 + 110 * t), int(150 + 80 * t), int(235 + 15 * t)))
    for y in range(horizon, H):  # field gradient
        t = (y - horizon) / (H - horizon)
        d.line([(0, y), (W, y)], fill=(int(90 - 40 * t), int(185 - 60 * t), int(70 - 30 * t)))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    sx, sy = int(W * 0.76), int(H * 0.22)
    gd.ellipse([sx - 190, sy - 190, sx + 190, sy + 190], fill=(255, 240, 180, 110))
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    img.paste(glow, (0, 0), glow)
    d = ImageDraw.Draw(img)
    d.ellipse([sx - 85, sy - 85, sx + 85, sy + 85], fill=(255, 226, 110))
    for cx, cy, s in ((260, 170, 1.0), (620, 110, 0.7), (1100, 240, 0.8)):  # clouds
        for dx, dy, r in ((0, 0, 60), (55, -20, 70), (115, 0, 58), (60, 20, 60)):
            rr = int(r * s)
            d.ellipse([cx + dx * s - rr, cy + dy * s - rr, cx + dx * s + rr, cy + dy * s + rr], fill=(250, 252, 255))
    d.polygon([(0, horizon), (380, horizon - 120), (760, horizon)], fill=(76, 150, 92))  # hills
    d.polygon([(600, horizon), (1050, horizon - 160), (1600, horizon)], fill=(66, 140, 84))
    d.ellipse([W // 2 - 360, int(H * 0.72), W // 2 + 360, int(H * 1.15)], outline=(240, 240, 230), width=6)  # pitch arc
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, quality=90)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
