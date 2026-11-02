"""Product renders placed in the photo (spec 17.1.4–17.1.5). Geometry and light only.

Each product is drawn front-on in its own sprite (centimetre grid, supersampled) and warped onto the photo
with the homography of its front plane: true size, true perspective and true depth (a washer's front stands
56 cm proud of the wall). Visible side/top faces and shadows on the wall and floor come from the camera pose
recovered from the wall. Realism comes from the photo itself — the wall's colour and brightness light the
frame, tint the screen and set the shadows; the photo's blur and grain are matched so nothing looks pasted."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from chanakya.tryon import geometry
from chanakya.tryon.geometry import Pose, Wall, cross2
from chanakya.tryon.place import SAFFRON, STAND_GAP_CM, Placed
from chanakya.tryon.read_room import PxBox, Room

SCREEN_PATH = Path(__file__).resolve().parent.parent / "render" / "static" / "img" / "screen_bright.jpg"
FALLBACK_SCREEN = Path(__file__).resolve().parent / "assets" / "screen_fallback.jpg"
SS = 2  # supersampling factor for warps and vector overlays
# Largest supersampled sprite canvas (px). Building a sprite holds ~a dozen float layers of its size, so a big TV
# drawn at full 2× was ~120 MB on a 512 MB instance; at this cap it is still ~2× its on-screen size.
MAX_SPRITE_PX = 900_000
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


# ---------------------------------------------------------------- light


@dataclass
class Light:
    rgb: np.ndarray  # median wall colour around the product, 0-255
    lum: float  # its luminance, 0-255
    noise: float  # photo grain (sigma, grey levels)
    side: float  # -1 light from the left … +1 light from the right
    level: str = "moderate"  # sunny | moderate | dim (read hint, else from luminance)
    blur: float = 0.6  # photo softness: equivalent Gaussian sigma in px


# edge-detail ratio |I - G1(I)| / |I - G2(I)| on strong edges → equivalent blur sigma (calibrated on renders
# with known blur; the ratio falls monotonically as the photo gets softer)
_SHARP_RATIO = np.array([0.20, 0.30, 0.34, 0.43, 0.56, 0.61, 0.66])
_SHARP_SIGMA = np.array([3.06, 2.1, 1.6, 1.17, 0.8, 0.6, 0.5])
RENDER_SIGMA = 0.35  # the supersampled render's own edge softness


def photo_blur(gray_img: Image.Image) -> float:
    """Equivalent Gaussian blur of the photo, from how much fine detail its strongest edges keep."""
    a = np.asarray(gray_img, dtype=np.float32)
    if a.shape[0] < 16 or a.shape[1] < 16:
        return 0.6
    b1 = np.asarray(gray_img.filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
    b2 = np.asarray(gray_img.filter(ImageFilter.GaussianBlur(2.0)), dtype=np.float32)
    grad = np.hypot(np.abs(np.diff(a, axis=1))[:-1, :], np.abs(np.diff(a, axis=0))[:, :-1])
    m = grad >= max(float(np.percentile(grad, 98)), 12.0)
    if m.sum() < 200:
        return 0.6
    e1 = float(np.abs(a - b1)[:-1, :-1][m].mean())
    e2 = float(np.abs(a - b2)[:-1, :-1][m].mean())
    return float(np.interp(e1 / max(e2, 1e-6), _SHARP_RATIO, _SHARP_SIGMA))


def _ring_mask(size: tuple[int, int], quad: np.ndarray, grow: float, shrink: float) -> Image.Image:
    c = quad.mean(axis=0)
    outer = c + (quad - c) * grow
    inner = c + (quad - c) * shrink
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.polygon([tuple(p) for p in outer], fill=255)
    d.polygon([tuple(p) for p in inner], fill=0)
    return m


def sample_light(img: Image.Image, quad: np.ndarray, room: Room | None = None) -> Light:
    """Wall colour, brightness, grain and light side from a ring of wall around the product."""
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    mask = np.asarray(_ring_mask((w, h), quad, 1.45, 1.04)) > 0
    if room is not None:
        for label in ("door", "window", "tv_unit", "sofa", "person", "existing_tv"):
            for x0, y0, x1, y1 in room.all(label):
                mask[max(0, y0) : max(0, y1), max(0, x0) : max(0, x1)] = False
    if mask.sum() < 400:
        mask = np.ones((h, w), dtype=bool)
    px = arr[mask]
    rgb = np.median(px, axis=0)
    lum = float(rgb @ LUMA)
    gray = arr @ LUMA
    gray_img = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
    smooth = np.asarray(gray_img.filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32)
    res = (gray - smooth)[mask]
    noise = float(np.clip(1.4826 * np.median(np.abs(res - np.median(res))), 0.6, 6.0))
    xs = np.nonzero(mask)[1]
    cx = float(quad[:, 0].mean())
    left, right = gray[mask][xs < cx], gray[mask][xs >= cx]
    side = 0.0
    if len(left) > 50 and len(right) > 50:
        side = float(np.clip((np.median(right) - np.median(left)) / max(lum, 1.0) * 4.0, -1.0, 1.0))
    level = (room.light if room is not None and room.light else None) or (
        "sunny" if lum > 185 else "dim" if lum < 95 else "moderate"
    )
    blur = photo_blur(gray_img)
    return Light(rgb=rgb.astype(np.float32), lum=lum, noise=noise, side=side, level=level, blur=blur)


# ---------------------------------------------------------------- screen content


def make_wallpaper(w: int = 1600, h: int = 900) -> Image.Image:
    """Vivid abstract TV wallpaper: glowing ribbons on deep indigo (procedural, deterministic)."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    xn, yn = x / w, y / h
    t = np.clip(0.55 * xn + 0.45 * yn, 0, 1)[..., None]
    img = np.array([4, 10, 40], np.float32) * (1 - t) + np.array([46, 6, 74], np.float32) * t
    ribbons = [  # centre, amplitude, frequency, phase, width, colour
        (0.40, 0.22, 0.55, 5.3, 0.10, (90, 40, 230)),
        (0.63, 0.16, 0.85, 0.4, 0.060, (255, 40, 140)),
        (0.55, 0.20, 0.70, 2.1, 0.034, (255, 130, 20)),
        (0.47, 0.14, 1.05, 4.0, 0.026, (20, 190, 255)),
        (0.71, 0.10, 1.30, 1.2, 0.016, (255, 214, 90)),
    ]
    for c, a, k, ph, s, col in ribbons:
        curve = c + a * np.sin(2 * np.pi * k * xn + ph) + 0.04 * np.sin(2 * np.pi * 2.7 * k * xn + 2 * ph)
        d = (yn - curve) / s
        core = np.exp(-d * d)
        glow = np.exp(-0.08 * d * d) * 0.16
        img = img + (core + glow)[..., None] * np.array(col, np.float32) * 0.75
    gx, gy = xn - 0.72, yn - 0.30
    img = img + np.exp(-(gx * gx + gy * gy) / 0.04)[..., None] * np.array([70, 40, 110], np.float32)
    rng = np.random.default_rng(3)
    for _ in range(26):  # soft bokeh dots
        bx, by, br = rng.uniform(0, 1), rng.uniform(0.1, 0.9), rng.uniform(0.006, 0.02)
        dd = ((xn - bx) * w / h) ** 2 + (yn - by) ** 2
        img = img + (np.exp(-dd / (br * br)) * rng.uniform(0.15, 0.4))[..., None] * np.array([255, 190, 230])
    img = 255.0 * (1.0 - np.exp(-img / 235.0))  # soft tone map: rich colour, no harsh clipping
    vx, vy = xn - 0.5, yn - 0.5
    img *= (1.0 - 0.4 * (vx * vx + vy * vy) * 2.2)[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


@lru_cache(maxsize=1)
def screen_source() -> Image.Image:
    for p in (SCREEN_PATH, FALLBACK_SCREEN):
        try:
            if p.exists():
                im = Image.open(p)
                im.load()
                if im.width >= 160 and im.height >= 90:
                    return im.convert("RGB")
        except Exception:
            continue
    return make_wallpaper()


def _cover(im: Image.Image, w: int, h: int) -> np.ndarray:
    """Resize-and-crop to fill w×h (keeps the aspect)."""
    s = max(w / im.width, h / im.height)
    nw, nh = max(w, round(im.width * s)), max(h, round(im.height * s))
    r = im.resize((nw, nh), Image.Resampling.LANCZOS)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return np.asarray(r.crop((x0, y0, x0 + w, y0 + h)), dtype=np.float32)


# ---------------------------------------------------------------- sprite primitives


def _rrect(xx: np.ndarray, yy: np.ndarray, box: tuple[float, float, float, float], r: float, k: float) -> np.ndarray:
    """Anti-aliased coverage of a rounded rectangle (coords in cm, k px per cm)."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = max((x1 - x0) / 2 - r, 0.0), max((y1 - y0) / 2 - r, 0.0)
    qx, qy = np.abs(xx - cx) - hx, np.abs(yy - cy) - hy
    out = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r
    return np.clip(0.5 - out * k, 0.0, 1.0).astype(np.float32)


def _ellipse(xx, yy, cx, cy, rx, ry, k) -> np.ndarray:
    d = (np.hypot((xx - cx) / rx, (yy - cy) / ry) - 1.0) * min(rx, ry)
    return np.clip(0.5 - d * k, 0.0, 1.0).astype(np.float32)


def _blur(a: np.ndarray, radius_px: float) -> np.ndarray:
    if radius_px < 0.3:
        return a
    im = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8), "L")
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius_px)), dtype=np.float32) / 255.0


@dataclass
class Sprite:
    body: Image.Image  # RGBA product
    shadow: Image.Image  # RGBA (black, alpha) cast on the wall/floor
    k: float  # px per cm
    margin: float  # cm of canvas left/above the product's top-left corner
    edge_rgb: np.ndarray | None = None  # the real product's edge colour (photo sprites): for its drawn side faces


def _canvas(w_cm: float, h_cm: float, k: float, margin: float, extra_cm: float = 0.0):
    wp = int(np.ceil((w_cm + 2 * margin) * k))
    hp = int(np.ceil((h_cm + 2 * margin + extra_cm) * k))
    yy, xx = np.mgrid[0:hp, 0:wp].astype(np.float32)
    return (xx + 0.5) / k - margin, (yy + 0.5) / k - margin, wp, hp


def _shadow(xx, yy, w_cm, h_cm, k, light: Light, strength: float, spread: float, drop: float, r: float):
    """Soft drop shadow + tight contact shadow of a w×h panel, as alpha 0..1."""
    dx = -light.side * 0.012 * w_cm
    dy = drop
    soft = _rrect(xx, yy, (dx, dy, w_cm + dx, h_cm + dy), r, k)
    soft = _blur(soft, spread * k)
    tight = _rrect(xx, yy, (0.25, 0.4, w_cm - 0.25, h_cm + 0.35), r, k)
    tight = _blur(tight, max(0.35, 0.004 * w_cm) * k)
    a_soft = {"sunny": 0.46, "moderate": 0.38, "dim": 0.3}.get(light.level, 0.38) * strength
    return 1.0 - (1.0 - a_soft * soft) * (1.0 - 0.42 * strength * tight)


def _rgba(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    a = np.dstack([np.clip(rgb, 0, 255), np.clip(alpha * 255, 0, 255)]).astype(np.uint8)
    return Image.fromarray(a, "RGBA")


def _shadow_img(alpha: np.ndarray, tint: np.ndarray) -> Image.Image:
    col = np.broadcast_to((tint * 0.18).astype(np.float32), alpha.shape + (3,))
    return _rgba(col, alpha)


# ---------------------------------------------------------------- TV


def tv_sprite(w_cm: float, h_cm: float, k: float, light: Light, mount: str = "wall") -> Sprite:
    stand = mount == "stand"
    margin = max(0.16 * w_cm, 8.0)
    extra = STAND_GAP_CM + 2.0 if stand else 0.0
    xx, yy, wp, hp = _canvas(w_cm, h_cm, k, margin, extra)
    r = max(0.3, 0.0035 * w_cm)
    body = _rrect(xx, yy, (0, 0, w_cm, h_cm), r, k)
    bs, bb = max(0.5, 0.008 * w_cm), max(0.7, 0.0115 * w_cm)  # side/top bezel, bottom bezel
    sbox = (bs, bs, w_cm - bs, h_cm - bb)
    screen = _rrect(xx, yy, sbox, 0.1, k)
    inner_rim = _rrect(xx, yy, (0.14, 0.14, w_cm - 0.14, h_cm - 0.14), r, k)
    rim = np.clip(body - inner_rim, 0, 1)

    wall = light.rgb
    frame = np.array([15, 15, 17], np.float32) + wall * 0.05
    t = np.clip(yy / max(h_cm, 1), 0, 1)[..., None]
    rim_col = frame + wall * (0.20 * (1 - t) + 0.07 * t)
    rgb = np.broadcast_to(frame, xx.shape + (3,)).copy()
    rgb = rgb * (1 - rim[..., None]) + rim_col * rim[..., None]

    # screen content, lit for the room: slight desaturation, room colour cast, gentle glare veil
    sx0, sy0 = int(np.floor((sbox[0] + margin) * k)), int(np.floor((sbox[1] + margin) * k))
    sx1, sy1 = int(np.ceil((sbox[2] + margin) * k)), int(np.ceil((sbox[3] + margin) * k))
    scr = np.zeros_like(rgb)
    content = _cover(screen_source(), max(2, sx1 - sx0), max(2, sy1 - sy0))
    lum = (content @ LUMA)[..., None]
    content = lum + (content - lum) * 0.93
    cast = (wall / max(float(wall.mean()), 1.0)) ** 0.22
    gain = 0.9 + 0.06 * (light.lum / 255.0)
    content = content * cast * gain
    veil = 0.035 + 0.075 * (light.lum / 255.0) ** 2
    content = content * (1 - veil) + wall * veil
    scr[sy0:sy1, sx0:sx1] = content[: sy1 - sy0, : sx1 - sx0]
    # glass: inner edge shadow + a soft diagonal reflection
    ex = np.minimum(np.minimum(xx - sbox[0], sbox[2] - xx), np.minimum(yy - sbox[1], sbox[3] - yy))
    scr *= (1.0 - 0.32 * np.exp(-np.clip(ex, 0, None) / 0.3))[..., None]
    sw, sh = sbox[2] - sbox[0], sbox[3] - sbox[1]
    s = (xx - sbox[0]) / sw + 0.65 * (yy - sbox[1]) / sh
    amp = 0.05 + 0.05 * (light.lum / 255.0) + (0.02 if light.level == "sunny" else 0.0)
    refl = amp * np.exp(-(((s - 0.58) / 0.13) ** 2)) * (1 - 0.4 * (yy - sbox[1]) / sh)
    refl += 0.6 * amp * np.clip(1 - s / 0.75, 0, 1) ** 2
    scr = scr + (255.0 - scr) * np.clip(refl, 0, 0.3)[..., None]
    rgb = rgb * (1 - screen[..., None]) + scr * screen[..., None]
    alpha = body

    if stand:  # slim pedestal on the TV-unit top
        nw, base_w = 0.06 * w_cm, 0.3 * w_cm
        neck = _rrect(xx, yy, (w_cm / 2 - nw / 2, h_cm - 1, w_cm / 2 + nw / 2, h_cm + STAND_GAP_CM - 0.8), 0.3, k)
        base = _rrect(
            xx,
            yy,
            (w_cm / 2 - base_w / 2, h_cm + STAND_GAP_CM - 1.2, w_cm / 2 + base_w / 2, h_cm + STAND_GAP_CM),
            0.5,
            k,
        )
        st = np.clip(neck + base, 0, 1)
        st_col = frame * 1.25 + wall * 0.04 * (1 + (yy > h_cm + STAND_GAP_CM - 1.25))[..., None]
        rgb = rgb * (1 - st[..., None] * (1 - body[..., None])) + st_col * (st * (1 - body))[..., None]
        alpha = np.clip(alpha + st * (1 - alpha), 0, 1)

    if stand:  # ~20 cm off the wall: a wider, fainter shadow (the base's contact shadow is cast on the unit top)
        sh_a = _shadow(xx, yy, w_cm, h_cm, k, light, 0.55, 0.07 * w_cm, 0.06 * h_cm + 2, r)
    else:
        sh_a = _shadow(xx, yy, w_cm, h_cm, k, light, 1.0, 0.035 * w_cm, 0.03 * h_cm + 0.8, r)
    return Sprite(_rgba(rgb, alpha), _shadow_img(sh_a, wall), k, margin)


# ---------------------------------------------------------------- the real product, from its photo

SHADOW = {  # (strength, spread, drop, corner radius) as functions of the product size in cm
    "tv": lambda w, h: (1.0, 0.035 * w, 0.03 * h + 0.8, max(0.3, 0.0035 * w)),
    "tv_stand": lambda w, h: (0.55, 0.07 * w, 0.06 * h + 2, max(0.3, 0.0035 * w)),
    "ac": lambda w, h: (0.75, 0.05 * w, 0.05 * h + 1.0, 0.14 * h),
    "wm": lambda w, h: (0.6, 0.06 * w, 0.02 * h, 0.03 * w),
    "fridge": lambda w, h: (0.6, 0.05 * w, 0.015 * h, 0.03 * w),
}


def _lit_screen(wp: int, hp: int, light: Light) -> np.ndarray:
    """A switched-on screen for a TV photographed with its screen off: the picture, lit for the room (slight
    desaturation, room colour cast, glare veil), an inner edge shadow and a soft diagonal reflection."""
    content = _cover(screen_source(), max(2, wp), max(2, hp))[:hp, :wp]
    lum = (content @ LUMA)[..., None]
    content = lum + (content - lum) * 0.93
    wall = light.rgb
    content = content * (wall / max(float(wall.mean()), 1.0)) ** 0.22 * (0.9 + 0.06 * (light.lum / 255.0))
    veil = 0.035 + 0.075 * (light.lum / 255.0) ** 2
    content = content * (1 - veil) + wall * veil
    y, x = np.mgrid[0:hp, 0:wp].astype(np.float32)
    e = np.minimum(np.minimum(x, wp - 1 - x), np.minimum(y, hp - 1 - y))
    content *= (1.0 - 0.3 * np.exp(-e / max(1.0, 0.006 * wp)))[..., None]
    s = x / max(wp, 1) + 0.65 * y / max(hp, 1)
    amp = 0.05 + 0.05 * (light.lum / 255.0) + (0.02 if light.level == "sunny" else 0.0)
    refl = amp * np.exp(-(((s - 0.58) / 0.13) ** 2)) * (1 - 0.4 * y / max(hp, 1)) + 0.6 * amp * np.clip(1 - s / 0.75, 0, 1) ** 2
    return content + (255.0 - content) * np.clip(refl, 0, 0.3)[..., None]


def photo_sprite(cut, category: str, w_cm: float, h_cm: float, k: float, light: Light, stand: bool = False) -> Sprite:
    """The product's own catalogue photo (cut out) at true size: its panel/body mapped to w × h cm, a TV's blank
    screen switched on, exposure and colour matched to the room, and the same wall shadow as the drawn product."""
    margin = max(0.16 * w_cm, 8.0) if category == "tv" else max(0.12 * w_cm, 6.0)
    px0, py0, px1, py1 = cut.panel
    sx, sy = w_cm * k / max(px1 - px0, 1), h_cm * k / max(py1 - py0, 1)
    cw, ch = cut.rgba.size
    extra_cm = max(0.0, (ch - py1) * sy / k)  # a stand below the panel
    xx, yy, wp, hp = _canvas(w_cm, h_cm, k, margin, extra_cm)
    prod = cut.rgba.resize((max(1, round(cw * sx)), max(1, round(ch * sy))), Image.Resampling.LANCZOS)
    arr = np.asarray(prod, dtype=np.float32).copy()
    rgb, a = arr[..., :3], arr[..., 3]
    lit = np.ones(a.shape, np.float32)
    if cut.screen is not None:
        x0, y0 = round(cut.screen[0] * sx), round(cut.screen[1] * sy)
        x1, y1 = min(rgb.shape[1], round(cut.screen[2] * sx)), min(rgb.shape[0], round(cut.screen[3] * sy))
        if x1 - x0 > 4 and y1 - y0 > 4:
            rgb[y0:y1, x0:x1] = _lit_screen(x1 - x0, y1 - y0, light)
            lit[y0:y1, x0:x1] = 0.0  # the screen gives its own light
    wall = light.rgb
    cast = (wall / max(float(wall.mean()), 1.0)) ** 0.12
    gain = 0.86 + 0.18 * (light.lum / 255.0)
    top_down = np.linspace(1.03, 0.95, rgb.shape[0], dtype=np.float32)[:, None]
    shade = (cast[None, None, :] * (gain * top_down)[..., None])
    rgb = rgb * (1 - lit[..., None]) + rgb * shade * lit[..., None]
    out = np.dstack([np.clip(rgb, 0, 255), a]).astype(np.uint8)
    body = Image.new("RGBA", (wp, hp), (0, 0, 0, 0))
    body.alpha_composite(Image.fromarray(out, "RGBA"), (max(0, round(margin * k - px0 * sx)), max(0, round(margin * k - py0 * sy))))
    strength, spread, drop, r = SHADOW["tv_stand" if (category == "tv" and stand) else category](w_cm, h_cm)
    sh_a = _shadow(xx, yy, w_cm, h_cm, k, light, strength, spread, drop, r)
    del xx, yy
    return Sprite(body, _shadow_img(sh_a, wall), k, margin, edge_rgb=np.clip(cut.edge_rgb * cast * gain, 0, 255))


# ---------------------------------------------------------------- AC / WM


def _appliance_white(light: Light) -> np.ndarray:
    """A white appliance under the room's light: the wall colour scaled to white paint's albedo."""
    w = light.rgb * 1.1
    m = float(w.max())
    if m > 246:
        w = w * (246 / m)
    return np.maximum(w, 40).astype(np.float32)


def ac_sprite(w_cm: float, h_cm: float, k: float, light: Light) -> Sprite:
    margin = max(0.12 * w_cm, 6.0)
    xx, yy, wp, hp = _canvas(w_cm, h_cm, k, margin)
    r = 0.14 * h_cm
    body = _rrect(xx, yy, (0, 0, w_cm, h_cm), r, k)
    white = _appliance_white(light)
    t = np.clip(yy / h_cm, 0, 1)[..., None]
    rgb = white * (1.03 - 0.16 * t)  # lit from above
    ends = np.clip(1 - np.minimum(xx, w_cm - xx) / (0.05 * w_cm), 0, 1)[..., None]
    rgb = rgb * (1 - 0.12 * ends)
    seam = np.exp(-(((yy - 0.24 * h_cm) / 0.12) ** 2))[..., None] * ((xx > r) & (xx < w_cm - r))[..., None]
    rgb = rgb * (1 - 0.08 * seam)
    slot = _rrect(xx, yy, (0.06 * w_cm, 0.72 * h_cm, 0.94 * w_cm, 0.86 * h_cm), 0.04 * h_cm, k)
    rgb = rgb * (1 - slot[..., None]) + (white * 0.32) * slot[..., None]
    flap = _rrect(xx, yy, (0.07 * w_cm, 0.78 * h_cm, 0.93 * w_cm, 0.83 * h_cm), 0.02 * h_cm, k)
    rgb = rgb * (1 - flap[..., None]) + (white * 0.82) * flap[..., None]
    disp = _rrect(xx, yy, (0.84 * w_cm, 0.42 * h_cm, 0.9 * w_cm, 0.5 * h_cm), 0.01 * w_cm, k)
    rgb = rgb * (1 - 0.6 * disp[..., None]) + np.array([40, 60, 70], np.float32) * 0.6 * disp[..., None]
    sh_a = _shadow(xx, yy, w_cm, h_cm, k, light, 0.75, 0.05 * w_cm, 0.05 * h_cm + 1.0, r)
    return Sprite(_rgba(rgb, body), _shadow_img(sh_a, light.rgb), k, margin)


def wm_sprite(w_cm: float, h_cm: float, k: float, light: Light) -> Sprite:
    margin = max(0.14 * w_cm, 6.0)
    xx, yy, wp, hp = _canvas(w_cm, h_cm, k, margin)
    body = _rrect(xx, yy, (0, 0, w_cm, h_cm), 0.03 * w_cm, k)
    white = _appliance_white(light)
    t = np.clip(yy / h_cm, 0, 1)[..., None]
    rgb = white * (1.03 - 0.14 * t)
    panel_h = 0.15 * h_cm
    seam = np.exp(-(((yy - panel_h) / 0.15) ** 2))[..., None]
    rgb = rgb * (1 - 0.18 * seam)
    drawer = _rrect(xx, yy, (0.05 * w_cm, 0.03 * h_cm, 0.32 * w_cm, panel_h - 0.02 * h_cm), 0.01 * w_cm, k)
    drawer_in = _rrect(xx, yy, (0.055 * w_cm, 0.035 * h_cm, 0.315 * w_cm, panel_h - 0.025 * h_cm), 0.01 * w_cm, k)
    rgb = rgb * (1 - 0.12 * np.clip(drawer - drawer_in, 0, 1)[..., None])
    disp = _rrect(xx, yy, (0.42 * w_cm, 0.05 * h_cm, 0.62 * w_cm, 0.1 * h_cm), 0.008 * w_cm, k)
    rgb = rgb * (1 - disp[..., None]) + np.array([35, 42, 48], np.float32) * disp[..., None]
    dial = _ellipse(xx, yy, 0.78 * w_cm, 0.075 * h_cm, 0.045 * w_cm, 0.045 * w_cm, k)
    rgb = rgb * (1 - dial[..., None]) + (white * 0.78) * dial[..., None]
    cx, cy, ro = 0.5 * w_cm, 0.57 * h_cm, 0.36 * w_cm
    ring = _ellipse(xx, yy, cx, cy, ro, ro, k)
    glass = _ellipse(xx, yy, cx, cy, 0.27 * w_cm, 0.27 * w_cm, k)
    ring_shade = (0.62 + 0.3 * np.clip((cy - yy) / ro, -1, 1))[..., None]
    rgb = rgb * (1 - ring[..., None]) + (white * ring_shade) * ring[..., None]
    g = np.array([28, 34, 40], np.float32) + light.rgb * 0.06
    hl = np.exp(-(((xx - cx + 0.1 * w_cm) / (0.09 * w_cm)) ** 2 + ((yy - cy + 0.12 * w_cm) / (0.05 * w_cm)) ** 2))
    glass_col = g + (255 - g) * (0.35 * hl)[..., None]
    rgb = rgb * (1 - glass[..., None]) + glass_col * glass[..., None]
    kick = np.clip((yy - 0.95 * h_cm) * 5, 0, 1)[..., None]
    rgb = rgb * (1 - 0.1 * kick)
    sh_a = _shadow(xx, yy, w_cm, h_cm, k, light, 0.6, 0.06 * w_cm, 0.02 * h_cm, 0.03 * w_cm)
    return Sprite(_rgba(rgb, body), _shadow_img(sh_a, light.rgb), k, margin)


def fridge_sprite(w_cm: float, h_cm: float, k: float, light: Light, door: str = "double") -> Sprite:
    """A brushed-steel fridge front at true size: door seams by type (single + base drawer, double with freezer on
    top, bottom freezer, side-by-side) and long bar handles; same lighting as the room."""
    margin = max(0.12 * w_cm, 6.0)
    xx, yy, wp, hp = _canvas(w_cm, h_cm, k, margin)
    body = _rrect(xx, yy, (0, 0, w_cm, h_cm), 0.025 * w_cm, k)
    white = _appliance_white(light)
    steel = white * 0.86
    tx = np.clip(xx / w_cm, 0, 1)[..., None]
    t = np.clip(yy / h_cm, 0, 1)[..., None]
    rgb = steel * (1.04 - 0.10 * t) * (0.96 + 0.08 * np.sin(tx * np.pi))

    def hseam(y):
        return np.exp(-(((yy - y) / 0.35) ** 2))[..., None]

    def vseam(x):
        return np.exp(-(((xx - x) / 0.35) ** 2))[..., None]

    def bar(x0, y0, x1, y1):
        return _rrect(xx, yy, (x0, y0, x1, y1), min(x1 - x0, y1 - y0) / 2, k)[..., None]

    handles = np.zeros_like(rgb[..., :1])
    if door == "single":
        rgb = rgb * (1 - 0.35 * hseam(0.86 * h_cm))
        handles += bar(0.90 * w_cm, 0.30 * h_cm, 0.93 * w_cm, 0.60 * h_cm)
    elif door == "bottom_freezer":
        rgb = rgb * (1 - 0.35 * hseam(0.63 * h_cm))
        handles += bar(0.06 * w_cm, 0.30 * h_cm, 0.09 * w_cm, 0.58 * h_cm)
        handles += bar(0.20 * w_cm, 0.66 * h_cm, 0.80 * w_cm, 0.675 * h_cm)
    elif door == "side_by_side":
        rgb = rgb * (1 - 0.35 * vseam(0.47 * w_cm))
        handles += bar(0.42 * w_cm, 0.24 * h_cm, 0.44 * w_cm, 0.70 * h_cm)
        handles += bar(0.50 * w_cm, 0.24 * h_cm, 0.52 * w_cm, 0.70 * h_cm)
    else:
        rgb = rgb * (1 - 0.35 * hseam(0.29 * h_cm))
        handles += bar(0.06 * w_cm, 0.10 * h_cm, 0.09 * w_cm, 0.24 * h_cm)
        handles += bar(0.06 * w_cm, 0.34 * h_cm, 0.09 * w_cm, 0.58 * h_cm)
    handles = np.clip(handles, 0, 1)
    rgb = rgb * (1 - handles) + (white * 0.62) * handles
    kick = np.clip((yy - 0.975 * h_cm) * 5, 0, 1)[..., None]
    rgb = rgb * (1 - 0.35 * kick)
    sh_a = _shadow(xx, yy, w_cm, h_cm, k, light, 0.6, 0.05 * w_cm, 0.015 * h_cm, 0.03 * w_cm)
    return Sprite(_rgba(rgb, body), _shadow_img(sh_a, light.rgb), k, margin)


# ---------------------------------------------------------------- warp + photo match


def _sprite_h(plane_h: np.ndarray, p: Placed, sp: Sprite) -> np.ndarray:
    """Homography: sprite px → photo px, for the product's front plane (or the wall, for its shadow)."""
    uc, zc, w, h = p.rect
    u_left, z_top = uc - w / 2, zc + h / 2
    m = np.array([[1 / sp.k, 0, u_left - sp.margin], [0, -1 / sp.k, z_top + sp.margin], [0, 0, 1.0]])
    return plane_h @ m


def _faces(pose: Pose, p: Placed) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """Visible side faces of the product box as (kind, image quad, depth parameter per corner 0=back 1=front)."""
    uc, zc, w, h = p.rect
    u0, u1, z0, z1, nb, nf = uc - w / 2, uc + w / 2, zc - h / 2, zc + h / 2, p.back, p.depth
    if nf - nb < 0.5:
        return []
    cu, cz, _ = pose.centre
    out = []
    if cu < u0:
        out.append(("side", [(u0, z1, nb), (u0, z1, nf), (u0, z0, nf), (u0, z0, nb)]))
    if cu > u1:
        out.append(("side", [(u1, z1, nf), (u1, z1, nb), (u1, z0, nb), (u1, z0, nf)]))
    if cz > z1:
        out.append(("top", [(u0, z1, nb), (u1, z1, nb), (u1, z1, nf), (u0, z1, nf)]))
    if cz < z0:
        out.append(("bottom", [(u0, z0, nf), (u1, z0, nf), (u1, z0, nb), (u0, z0, nb)]))
    return [(kind, pose.project(c), np.array([(q[2] - nb) / (nf - nb) for q in c])) for kind, c in out]


def _face_colour(category: str, kind: str, light: Light, side_sign: float, edge: np.ndarray | None = None) -> np.ndarray:
    wall = light.rgb
    if edge is not None:  # the real product's own colour (photo sprite)
        lit = 0.9 if side_sign * light.side >= 0 else 0.76
        return edge * {"top": 1.02, "bottom": 0.6, "side": lit}[kind]
    if category == "tv":
        frame = np.array([15, 15, 17], np.float32) + wall * 0.05
        return {"top": frame + wall * 0.12, "bottom": frame * 0.8, "side": frame * 1.2 + wall * 0.05}[kind]
    white = _appliance_white(light)
    lit = 0.9 if side_sign * light.side >= 0 else 0.76  # the face turned towards the light is brighter
    return white * {"top": 0.99, "bottom": 0.6, "side": lit}[kind]


def _draw_faces(img: Image.Image, faces, colours, mask: np.ndarray) -> Image.Image:
    """Anti-aliased side faces (a light depth gradient towards the wall), added to the product mask."""
    if not faces:
        return img
    pts = np.vstack([f[1] for f in faces])
    x0, y0, x1, y1 = _overlay_box(img.size, pts, 3)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return img
    ov = Image.new("RGBA", ((x1 - x0) * SS, (y1 - y0) * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for (_kind, quad, tdep), col in zip(faces, colours, strict=True):
        q = (quad - [x0, y0]) * SS
        steps = 6 if abs(cross2(q[1] - q[0], q[3] - q[0])) > 1600 else 1
        # split along the depth direction: corners with t=0 are at the wall, t=1 at the front
        back = [i for i in range(4) if tdep[i] < 0.5]
        front = [i for i in range(4) if tdep[i] >= 0.5]
        if len(back) != 2 or len(front) != 2:
            d.polygon([tuple(v) for v in q], fill=tuple(int(c) for c in np.clip(col, 0, 255)) + (255,))
            continue
        b0, b1 = q[back[0]], q[back[1]]
        f0 = q[front[0]] if np.linalg.norm(q[front[0]] - b0) < np.linalg.norm(q[front[1]] - b0) else q[front[1]]
        f1 = q[front[1]] if f0 is q[front[0]] else q[front[0]]
        for i in range(steps):
            ta, tb = i / steps, (i + 1) / steps
            shade = 0.84 + 0.16 * (ta + tb) / 2
            poly = [b0 + (f0 - b0) * ta, b1 + (f1 - b1) * ta, b1 + (f1 - b1) * tb, b0 + (f0 - b0) * tb]
            c = tuple(int(v) for v in np.clip(col * shade, 0, 255)) + (255,)
            d.polygon([tuple(v) for v in poly], fill=c)
    ov = ov.resize((x1 - x0, y1 - y0), Image.Resampling.BOX)
    _composite(img, ov, (x0, y0))
    a = np.asarray(ov.getchannel("A"), dtype=np.float32) / 255.0
    sub = mask[y0:y1, x0:x1]
    np.maximum(sub, a[: sub.shape[0], : sub.shape[1]], out=sub)
    return img


def _ground_shadow(img: Image.Image, pose: Pose, p: Placed, z: float, foot, light: Light, strength: float) -> None:
    """Soft contact shadow on a horizontal surface (floor, TV-unit top) under the product."""
    (u0, u1), (n0, n1) = foot
    poly = pose.project([(u0, z, n0), (u1, z, n0), (u1, z, n1), (u0, z, n1)])
    if not np.all(np.isfinite(poly)):
        return
    w_px = float(np.linalg.norm(poly[1] - poly[0]))
    blur = max(2.0, 0.05 * w_px)
    x0, y0, x1, y1 = _overlay_box(img.size, poly, int(blur * 3) + 2)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    m = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(m).polygon([tuple(v) for v in poly - [x0, y0]], fill=255)
    a = np.asarray(m.filter(ImageFilter.GaussianBlur(blur)), dtype=np.float32) / 255.0
    a *= {"sunny": 0.6, "moderate": 0.52, "dim": 0.42}.get(light.level, 0.52) * strength
    _composite(img, _shadow_img(a, light.rgb), (x0, y0))


def warp(sprite_img: Image.Image, hs: np.ndarray, size: tuple[int, int]) -> tuple[Image.Image, tuple[int, int]] | None:
    """Warp an RGBA sprite into the photo; returns the warped crop and its top-left, or None if off-photo."""
    sw, sh = sprite_img.size
    corners = np.array([[0, 0, 1], [sw, 0, 1], [sw, sh, 1], [0, sh, 1]], float) @ hs.T
    if np.any(corners[:, 2] <= 0):
        return None
    pts = corners[:, :2] / corners[:, 2:3]
    x0, y0 = max(0, int(np.floor(pts[:, 0].min())) - 2), max(0, int(np.floor(pts[:, 1].min())) - 2)
    x1, y1 = min(size[0], int(np.ceil(pts[:, 0].max())) + 2), min(size[1], int(np.ceil(pts[:, 1].max())) + 2)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    t = np.array([[1 / SS, 0, x0], [0, 1 / SS, y0], [0, 0, 1.0]])
    m = np.linalg.inv(hs) @ t
    m = m / m[2, 2]
    out = sprite_img.transform(
        ((x1 - x0) * SS, (y1 - y0) * SS),
        Image.Transform.PERSPECTIVE,
        tuple(m.flatten()[:8]),
        Image.Resampling.BICUBIC,
    )
    return out.resize((x1 - x0, y1 - y0), Image.Resampling.BOX), (x0, y0)


def _composite(base: Image.Image, layer: Image.Image, at: tuple[int, int]) -> None:
    region = (at[0], at[1], at[0] + layer.width, at[1] + layer.height)
    crop = base.crop(region).convert("RGBA")
    crop.alpha_composite(layer)
    base.paste(crop.convert("RGB"), region[:2])


def match_photo(img: Image.Image, mask: np.ndarray, light: Light, seed: int = 7) -> Image.Image:
    """Give the rendered pixels the photo's softness and grain (only where the product is)."""
    if not mask.any():
        return img
    ys, xs = np.nonzero(mask > 0.02)
    x0, y0 = max(0, xs.min() - 3), max(0, ys.min() - 3)
    x1, y1 = min(img.width, xs.max() + 4), min(img.height, ys.max() + 4)
    crop = img.crop((x0, y0, x1, y1))
    a = np.asarray(crop, dtype=np.float32)
    sigma = float(np.clip(np.sqrt(max(light.blur**2 - RENDER_SIGMA**2, 0.0)), 0.3, 2.5))
    soft = np.asarray(crop.filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32)
    m = mask[y0:y1, x0:x1, None]
    m_soft = _blur(np.clip(mask[y0:y1, x0:x1] * 1.0, 0, 1), 0.8)[..., None]
    out = a * (1 - m_soft) + soft * m_soft
    rng = np.random.default_rng(seed)
    n = rng.normal(0.0, light.noise, out.shape[:2])[..., None] + rng.normal(0.0, light.noise * 0.35, out.shape)
    out = out + n * m
    img.paste(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB"), (x0, y0))
    return img


def _inside(pt: tuple[float, float], quad) -> bool:
    q = np.asarray(quad, dtype=float)
    s = [cross2(q[(i + 1) % 4] - q[i], np.asarray(pt, dtype=float) - q[i]) for i in range(4)]
    return all(v >= 0 for v in s) or all(v <= 0 for v in s)


def cover_old_tv(img: Image.Image, box: PxBox, light: Light) -> Image.Image:
    """Paint the wall over the old TV (Coons patch from the surrounding wall + grain)."""
    arr = np.asarray(img, dtype=np.float32).copy()
    h, w = arr.shape[:2]
    pad = 4
    x0, y0 = max(1, box[0] - pad), max(1, box[1] - pad)
    x1, y1 = min(w - 1, box[2] + pad), min(h - 1, box[3] + pad)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return img
    band = 4
    top = arr[max(0, y0 - band) : y0, x0:x1].mean(axis=0) if y0 > 0 else None
    bot = arr[y1 : min(h, y1 + band), x0:x1].mean(axis=0) if y1 < h else None
    lef = arr[y0:y1, max(0, x0 - band) : x0].mean(axis=1) if x0 > 0 else None
    rig = arr[y0:y1, x1 : min(w, x1 + band)].mean(axis=1) if x1 < w else None
    sides = {"t": top, "b": bot, "l": lef, "r": rig}
    med = np.median([s.mean(axis=0) for s in sides.values() if s is not None], axis=0)
    for key, s in list(sides.items()):  # drop a side that is not wall (e.g. the TV unit under the TV)
        if s is not None and np.abs(s.mean(axis=0) - med).max() > 40:
            sides[key] = None
    ny, nx = y1 - y0, x1 - x0
    ty = ((np.arange(ny) + 0.5) / ny)[:, None, None]
    tx = ((np.arange(nx) + 0.5) / nx)[None, :, None]
    fill_v = fill_h = None
    t_, b_ = sides["t"], sides["b"]
    if t_ is not None or b_ is not None:
        t_ = t_ if t_ is not None else b_
        b_ = b_ if b_ is not None else t_
        fill_v = t_[None, :, :] * (1 - ty) + b_[None, :, :] * ty
    l_, r_ = sides["l"], sides["r"]
    if l_ is not None or r_ is not None:
        l_ = l_ if l_ is not None else r_
        r_ = r_ if r_ is not None else l_
        fill_h = l_[:, None, :] * (1 - tx) + r_[:, None, :] * tx
    if fill_v is None and fill_h is None:
        fill = np.broadcast_to(light.rgb, (ny, nx, 3))
    elif fill_v is None:
        fill = fill_h
    elif fill_h is None:
        fill = fill_v
    else:
        fill = (fill_v + fill_h) / 2
    rng = np.random.default_rng(11)
    fill = fill + rng.normal(0, light.noise, (ny, nx, 1))
    arr[y0:y1, x0:x1] = fill
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    soft = out.crop((x0 - 1, y0 - 1, x1 + 1, y1 + 1)).filter(ImageFilter.GaussianBlur(1.0))
    out.paste(soft, (x0 - 1, y0 - 1))
    return out


# ---------------------------------------------------------------- outline + markers


def _overlay_box(img_size, pts: np.ndarray, pad: int):
    x0 = max(0, int(np.floor(pts[:, 0].min())) - pad)
    y0 = max(0, int(np.floor(pts[:, 1].min())) - pad)
    x1 = min(img_size[0], int(np.ceil(pts[:, 0].max())) + pad)
    y1 = min(img_size[1], int(np.ceil(pts[:, 1].max())) + pad)
    return x0, y0, x1, y1


def dashed_quad(img: Image.Image, quad, color=SAFFRON, lw: int = 3) -> Image.Image:
    """Clean dashed outline (solid corner brackets, even dashes) with a faint dark halo, anti-aliased."""
    q = np.asarray(quad, dtype=float)
    x0, y0, x1, y1 = _overlay_box(img.size, q, lw * 3)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return img
    ov = Image.new("RGBA", ((x1 - x0) * SS, (y1 - y0) * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    pts = (q - [x0, y0]) * SS
    dash, gap = 5.0 * lw * SS, 3.5 * lw * SS
    segs = []
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        L = float(np.linalg.norm(b - a))
        if L < 1:
            continue
        u = (b - a) / L
        corner = min(2.2 * dash, 0.3 * L)
        segs += [(a, a + u * corner), (b - u * corner, b)]
        inner = L - 2 * corner - 2 * gap
        if inner > dash:
            n = max(1, int((inner + gap) // (dash + gap)))
            used = n * dash + (n - 1) * gap
            s0 = corner + gap + (inner - used) / 2
            for j in range(n):
                p0 = a + u * (s0 + j * (dash + gap))
                segs.append((p0, p0 + u * dash))
    for width, col in ((lw * SS + 2 * SS, (0, 0, 0, 70)), (lw * SS, color + (255,))):
        for p0, p1 in segs:
            d.line([tuple(p0), tuple(p1)], fill=col, width=int(width))
            r = width / 2
            for p in (p0, p1):
                d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=col)
    ov = ov.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    _composite(img, ov, (x0, y0))
    return img


def fit_marker(img: Image.Image, quad, fits: bool) -> tuple[Image.Image, PxBox]:
    """Tick (fits the niche) / cross at the product's top-right corner. Returns the image and its box."""
    q = np.asarray(quad, dtype=float)
    w = float(np.linalg.norm(q[1] - q[0]))
    r = max(14.0, w * 0.09)
    cx, cy = q[1][0] - r * 0.4, q[1][1] - r * 0.4
    cx = min(max(cx, r + 2), img.width - r - 2)
    cy = min(max(cy, r + 2), img.height - r - 2)
    box = (int(cx - r - 3), int(cy - r - 3), int(cx + r + 4), int(cy + r + 4))
    ov = Image.new("RGBA", ((box[2] - box[0]) * SS, (box[3] - box[1]) * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    ox, oy = (cx - box[0]) * SS, (cy - box[1]) * SS
    R = r * SS
    color = (46, 139, 87, 255) if fits else (200, 40, 40, 255)
    d.ellipse((ox - R, oy - R, ox + R, oy + R), fill=color, outline=(255, 255, 255, 255), width=int(3 * SS))
    lw = int(max(3, r // 4) * SS)
    if fits:
        d.line(
            [(ox - R * 0.5, oy), (ox - R * 0.1, oy + R * 0.4), (ox + R * 0.55, oy - R * 0.45)], fill="white", width=lw
        )
    else:
        d.line([(ox - R * 0.45, oy - R * 0.45), (ox + R * 0.45, oy + R * 0.45)], fill="white", width=lw)
        d.line([(ox - R * 0.45, oy + R * 0.45), (ox + R * 0.45, oy - R * 0.45)], fill="white", width=lw)
    ov = ov.resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
    out = img.convert("RGB").copy()
    _composite(out, ov, (box[0], box[1]))
    return out, box


# ---------------------------------------------------------------- compose


def compose(
    img: Image.Image,
    category: str,
    placed: list[Placed],
    wall: Wall,
    room: Room | None = None,
    niche_width_cm: float | None = None,
) -> Image.Image:
    """Solid products rendered to scale as 3D boxes against the wall (front face at its true depth, visible
    sides, shadows on the wall and the floor); comparison sizes as dashed outlines of their front face."""
    out = img.convert("RGB").copy()
    solids = [p for p in placed if p.solid]
    if not placed:
        return out
    ref = solids[0] if solids else placed[0]
    light = sample_light(out, np.asarray(ref.quad), room)
    old = room.first("existing_tv") if room is not None else None
    if old is not None and category == "tv" and solids:
        x0, y0, x1, y1 = old
        corners = [(x0 - 3, y0 - 3), (x1 + 3, y0 - 3), (x1 + 3, y1 + 3), (x0 - 3, y1 + 3)]
        if not all(_inside(c, solids[0].quad) for c in corners):
            out = cover_old_tv(out, old, light)
    pose = wall.pose()
    mask = np.zeros((out.height, out.width), dtype=np.float32)
    for p in solids:
        uc, zc, w, h = p.rect
        three_d = pose is not None and p.depth > 0
        face_h = pose.plane_h(p.depth) if three_d else wall.h
        wall_h = pose.plane_h(0.0) if three_d else wall.h
        cz = [(uc - w / 2, zc + h / 2), (uc + w / 2, zc + h / 2), (uc + w / 2, zc - h / 2), (uc - w / 2, zc - h / 2)]
        pts = geometry.apply(face_h, cz)
        k = SS * max(float(np.linalg.norm(pts[1] - pts[0])) / w, float(np.linalg.norm(pts[3] - pts[0])) / h)
        k = float(min(max(k, 0.5), 40.0))
        margin = max(0.16 * w, 8.0)  # the widest sprite margin (TV): canvas ≈ (w + 2m) × (h + 2m) cm at k px/cm
        k = min(k, (MAX_SPRITE_PX / ((w + 2 * margin) * (h + 2 * margin + STAND_GAP_CM + 2))) ** 0.5)
        sp = None
        if getattr(p, "photo", None):  # the real product, from its catalogue photo, when it cuts out cleanly
            from chanakya.tryon.photo import cutout

            stand = category == "tv" and p.mount == "stand"
            cut = cutout(p.photo, category, w / h, keep_stand=stand)
            if cut is not None:
                sp = photo_sprite(cut, category, w, h, k, light, stand)
        if sp is None and category == "ac":
            sp = ac_sprite(w, h, k, light)
        elif sp is None and category == "wm":
            sp = wm_sprite(w, h, k, light)
        elif sp is None and category == "fridge":
            sp = fridge_sprite(w, h, k, light, getattr(p, "door", None) or "double")
        elif sp is None:
            sp = tv_sprite(w, h, k, light, p.mount)
        res = warp(sp.shadow, _sprite_h(wall_h, p, sp), out.size)  # cast on the wall
        if res is not None:
            _composite(out, *res)
        if three_d and category in ("wm", "fridge"):
            _ground_shadow(out, pose, p, 0.0, ((uc - w / 2 - 2, uc + w / 2 + 2), (0.0, p.depth + 4)), light, 1.0)
        if three_d and category == "tv" and p.mount == "stand":
            unit_top = zc - h / 2 - STAND_GAP_CM
            foot = ((uc - 0.16 * w, uc + 0.16 * w), (p.back - 8, p.depth + 4))
            _ground_shadow(out, pose, p, unit_top, foot, light, 0.8)
        if three_d:
            faces = _faces(pose, p)
            cols = [
                _face_colour(category, kind, light, -1.0 if quad[:, 0].mean() < pts[:, 0].mean() else 1.0, sp.edge_rgb)
                for kind, quad, _ in faces
            ]
            out = _draw_faces(out, faces, cols, mask)
        res = warp(sp.body, _sprite_h(face_h, p, sp), out.size)
        if res is not None:
            crop, at = res
            _composite(out, crop, at)
            a = np.asarray(crop.getchannel("A"), dtype=np.float32) / 255.0
            sub = mask[at[1] : at[1] + a.shape[0], at[0] : at[0] + a.shape[1]]
            np.maximum(sub, a[: sub.shape[0], : sub.shape[1]], out=sub)
    out = match_photo(out, mask, light)
    lw = max(3, round(out.width / 300))
    for p in placed:
        if not p.solid:
            out = dashed_quad(out, p.quad, SAFFRON, lw)
    return out
