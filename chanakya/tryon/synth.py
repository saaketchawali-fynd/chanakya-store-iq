"""Dev tool (not used at runtime): photo-like synthetic rooms with exact geometry for the try-on fixtures.

    python -m chanakya.tryon.synth   →  data/fixtures/images/room_*.jpg + data/fixtures/image_read/room_*.json

A small numpy ray caster — box room, furniture boxes/spheres, tiles, wood, fabric, window and wall-washer
light, ambient occlusion, contact shadows, glossy floor reflections, lens softness, sensor grain, JPEG — and
the "Gemini read" (normalised boxes, wall corners, floor/ceiling lines) projected with the same camera.
room_living and room_luxury share every dimension (only materials differ: the wealth-neutral test)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
IMG_DIR = ROOT / "data" / "fixtures" / "images"
READ_DIR = ROOT / "data" / "fixtures" / "image_read"
W, H = 1280, 960
FOCAL = 0.75 * W  # same phone-camera model as geometry.FOCAL_FRAC


def lin(c) -> np.ndarray:
    return (np.asarray(c, np.float32) / 255.0) ** 2.2


# ---------------------------------------------------------------- noise


def _hash(ix: np.ndarray, iy: np.ndarray, seed: int) -> np.ndarray:
    h = (ix.astype(np.int64) * 73856093) ^ (iy.astype(np.int64) * 19349663) ^ (seed * 83492791)
    h = (h ^ (h >> 13)) * 1274126177
    h = h ^ (h >> 16)
    return (h & 0xFFFF).astype(np.float32) / 65535.0


def vnoise(u: np.ndarray, v: np.ndarray, seed: int = 0) -> np.ndarray:
    iu, iv = np.floor(u), np.floor(v)
    fu, fv = u - iu, v - iv
    su, sv = fu * fu * (3 - 2 * fu), fv * fv * (3 - 2 * fv)
    a, b = _hash(iu, iv, seed), _hash(iu + 1, iv, seed)
    c, d = _hash(iu, iv + 1, seed), _hash(iu + 1, iv + 1, seed)
    return (a + (b - a) * su) * (1 - sv) + (c + (d - c) * su) * sv


def fbm(u, v, seed=0, octaves=4) -> np.ndarray:
    out, amp, tot = 0.0, 1.0, 0.0
    for o in range(octaves):
        out = out + amp * vnoise(u * 2**o, v * 2**o, seed + o)
        tot += amp
        amp *= 0.5
    return out / tot


# ---------------------------------------------------------------- scene


@dataclass
class Cam:
    pos: tuple[float, float, float]
    yaw: float = 0.0  # degrees, + turns right
    pitch: float = 0.0  # degrees, + looks up
    f: float = FOCAL
    w: int = W
    h: int = H

    def rot(self) -> np.ndarray:
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        ryaw = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        rpitch = np.array([[1, 0, 0], [0, cp, sp], [0, -sp, cp]])
        return ryaw @ rpitch

    def project(self, pts) -> np.ndarray:
        p = (np.atleast_2d(np.asarray(pts, float)) - np.asarray(self.pos)) @ self.rot()
        return np.c_[self.w / 2 + self.f * p[:, 0] / p[:, 2], self.h / 2 - self.f * p[:, 1] / p[:, 2]]


@dataclass
class Obj:
    lo: tuple[float, float, float]  # box min (or sphere centre)
    hi: tuple[float, float, float]  # box max (or (r, r, r) for a sphere)
    mat: str
    ao: float = 0.0  # contact-shadow strength on other surfaces
    sphere: bool = False


@dataclass
class Room:
    xl: float
    xr: float
    hc: float
    zw: float
    zb: float = -400.0
    objs: list[Obj] = field(default_factory=list)
    mats: dict = field(default_factory=dict)  # name -> fn(P, n) -> (albedo_lin, emission_lin, gloss)
    spots: list[tuple[tuple[float, float, float], float]] = field(default_factory=list)  # wall washers
    windows: list[tuple[float, float, float, float]] = field(default_factory=list)  # x0, x1, y0, y1 on main wall
    window_power: float = 1.0
    ambient: float = 0.55
    exposure: float = 1.0
    floor_gloss: float = 0.12


FLOOR, CEIL, MAIN, LEFT, RIGHT, BACK = range(6)


def _intersect(room: Room, o: np.ndarray, d: np.ndarray):
    n_rays = len(d)
    t_best = np.full(n_rays, np.inf, np.float32)
    oid = np.full(n_rays, -1, np.int32)
    nrm = np.zeros((n_rays, 3), np.float32)
    planes = [
        (FLOOR, 1, 0.0, (0, 1, 0)),
        (CEIL, 1, room.hc, (0, -1, 0)),
        (MAIN, 2, room.zw, (0, 0, -1)),
        (LEFT, 0, room.xl, (1, 0, 0)),
        (RIGHT, 0, room.xr, (-1, 0, 0)),
        (BACK, 2, room.zb, (0, 0, 1)),
    ]
    with np.errstate(divide="ignore", invalid="ignore"):
        for pid, ax, val, nv in planes:
            t = (val - o[ax]) / d[:, ax]
            upd = (t > 1e-3) & (t < t_best)
            t_best[upd], oid[upd] = t[upd], pid
            nrm[upd] = nv
        inv = 1.0 / np.where(np.abs(d) < 1e-9, 1e-9, d)
        for i, ob in enumerate(room.objs):
            if ob.sphere:
                c, r = np.asarray(ob.lo, np.float32), ob.hi[0]
                oc = o - c
                b = d @ oc
                disc = b * b - (oc @ oc - r * r)
                t = -b - np.sqrt(np.maximum(disc, 0))
                upd = (disc > 0) & (t > 1e-3) & (t < t_best)
                if upd.any():
                    t_best[upd], oid[upd] = t[upd], 10 + i
                    p = o + d[upd] * t[upd, None]
                    nrm[upd] = (p - c) / r
                continue
            t0 = (np.asarray(ob.lo, np.float32) - o) * inv
            t1 = (np.asarray(ob.hi, np.float32) - o) * inv
            tmin, tmax = np.minimum(t0, t1), np.maximum(t0, t1)
            tn, tf = tmin.max(axis=1), tmax.min(axis=1)
            upd = (tn <= tf) & (tn > 1e-3) & (tn < t_best)
            if upd.any():
                t_best[upd], oid[upd] = tn[upd], 10 + i
                ax = tmin[upd].argmax(axis=1)
                nn = np.zeros((int(upd.sum()), 3), np.float32)
                nn[np.arange(len(ax)), ax] = -np.sign(d[upd][np.arange(len(ax)), ax])
                nrm[upd] = nn
    return t_best, oid, nrm


def _edge_ao(room: Room, p: np.ndarray, oid: np.ndarray) -> np.ndarray:
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    ao = np.ones(len(p), np.float32)

    def dark(mask, *dists, k=0.38, s=22.0):
        for dd in dists:
            ao[mask] *= 1 - k * np.exp(-np.maximum(dd[mask], 0) / s)

    m = (oid == FLOOR) | (oid == CEIL)
    dark(m, room.zw - z, x - room.xl, room.xr - x)
    m = oid == MAIN
    dark(m, y, room.hc - y, x - room.xl, room.xr - x)
    m = (oid == LEFT) | (oid == RIGHT)
    dark(m, y, room.hc - y, room.zw - z)
    return ao


def _obj_ao(room: Room, p: np.ndarray, oid: np.ndarray) -> np.ndarray:
    ao = np.ones(len(p), np.float32)
    for i, ob in enumerate(room.objs):
        if ob.ao <= 0:
            continue
        if ob.sphere:
            dist = np.linalg.norm(p - np.asarray(ob.lo), axis=1) - ob.hi[0]
            sig = max(4.0, 0.5 * ob.hi[0])
        else:
            lo, hi = np.asarray(ob.lo), np.asarray(ob.hi)
            q = np.maximum(np.maximum(lo - p, 0), p - hi)
            dist = np.linalg.norm(q, axis=1)
            sig = float(np.clip(0.3 * min(hi - lo), 4.0, 26.0))
        occ = ob.ao * np.exp(-np.maximum(dist, 0) / sig)
        occ[oid == 10 + i] = 0
        ao *= 1 - occ
    return ao


def _shade(room: Room, o, d, t, oid, nrm) -> np.ndarray:
    p = o + d * t[:, None]
    alb = np.zeros_like(p)
    emi = np.zeros_like(p)
    gloss = np.zeros(len(p), np.float32)
    for k in np.unique(oid):
        idx = oid == k
        name = {FLOOR: "floor", CEIL: "ceiling", MAIN: "wall", LEFT: "side", RIGHT: "side", BACK: "side"}.get(int(k))
        if name is None:
            name = room.objs[int(k) - 10].mat
        a, e, g = room.mats[name](p[idx], nrm[idx])
        alb[idx], emi[idx], gloss[idx] = a, e, g
    light = room.ambient * (0.86 + 0.06 * nrm[:, 1])[:, None] * np.ones(3, np.float32)
    for (sx, sy, sz), power in room.spots:  # wall washers: downward cones
        lv = np.asarray((sx, sy, sz), np.float32) - p
        dist = np.linalg.norm(lv, axis=1) + 1e-3
        ldir = lv / dist[:, None]
        c = np.clip((ldir[:, 1] - 0.78) / 0.12, 0, 1)  # downward cone (~38° half-angle, soft edge)
        cone = c * c * (3 - 2 * c)
        lam = np.clip((nrm * ldir).sum(axis=1), 0, 1)
        light += (power * cone * lam / (1 + (dist / 120.0) ** 2))[:, None] * np.array([1.0, 0.93, 0.82], np.float32)
    for x0, x1, y0, y1 in room.windows:  # window as a grid of soft lights just inside the room
        for fx in (0.2, 0.5, 0.8):
            for fy in (0.25, 0.75):
                wp = np.array([x0 + (x1 - x0) * fx, y0 + (y1 - y0) * fy, room.zw - 25.0], np.float32)
                lv = wp - p
                dist = np.linalg.norm(lv, axis=1) + 1e-3
                lam = np.clip((nrm * lv).sum(axis=1) / dist, 0, 1)
                light += (room.window_power / 6 * lam / (1 + (dist / 150.0) ** 2))[:, None] * np.array(
                    [0.92, 0.97, 1.05], np.float32
                )
    ao = _edge_ao(room, p, oid) * _obj_ao(room, p, oid)
    col = alb * light * ao[:, None] + emi
    fl = (oid == FLOOR) & (gloss > 0)
    if fl.any() and room.windows:  # glossy floor: blurry reflection of the window and the wall
        r = d[fl] * np.array([1, -1, 1], np.float32)
        tt = (room.zw - p[fl, 2]) / np.maximum(r[:, 2], 1e-3)
        hx, hy = p[fl, 0] + r[:, 0] * tt, p[fl, 1] + r[:, 1] * tt
        soft = 6 + 0.12 * tt
        refl = np.zeros(len(tt), np.float32)
        for x0, x1, y0, y1 in room.windows:
            bx = np.clip((hx - x0) / soft + 0.5, 0, 1) * np.clip((x1 - hx) / soft + 0.5, 0, 1)
            by = np.clip((hy - y0) / soft + 0.5, 0, 1) * np.clip((y1 - hy) / soft + 0.5, 0, 1)
            refl += bx * by
        fres = 0.35 + 0.65 * (1 - np.clip(-d[fl, 1] * 2.5, 0, 1)) ** 2
        col[fl] += (gloss[fl] * fres * (1.4 * refl + 0.06))[:, None] * np.array([0.95, 0.97, 1.0], np.float32)
    return col


def render(room: Room, cam: Cam, ssr: int = 2, rows: int = 96, seed: int = 1) -> Image.Image:
    wr, hr = cam.w * ssr, cam.h * ssr
    out = np.zeros((hr, wr, 3), np.float32)
    rot = cam.rot().astype(np.float32)
    o = np.asarray(cam.pos, np.float32)
    for r0 in range(0, hr, rows):
        r1 = min(hr, r0 + rows)
        jj, ii = np.mgrid[r0:r1, 0:wr].astype(np.float32)
        dx = ((ii + 0.5) / ssr - cam.w / 2) / cam.f
        dy = -((jj + 0.5) / ssr - cam.h / 2) / cam.f
        d = np.stack([dx, dy, np.ones_like(dx)], -1).reshape(-1, 3)
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        d = d @ rot.T
        t, oid, nrm = _intersect(room, o, d)
        out[r0:r1] = _shade(room, o, d, t, oid, nrm).reshape(r1 - r0, wr, 3)
    x = out * room.exposure * np.array([1.03, 1.0, 0.95], np.float32)  # warm indoor white balance
    x = (x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14)  # filmic tone curve
    yy, xx = np.mgrid[0:hr, 0:wr].astype(np.float32)
    rr = ((xx / wr - 0.5) ** 2 + (yy / hr - 0.5) ** 2) / 0.5
    x *= (1 - 0.22 * rr)[..., None]  # vignette
    img = Image.fromarray((np.clip(x, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8), "RGB")
    img = img.resize((cam.w, cam.h), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(0.45))
    a = np.asarray(img, np.float32)
    rng = np.random.default_rng(seed)
    a = a + rng.normal(0, 1.9, a.shape[:2])[..., None] + rng.normal(0, 0.8, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------- materials


def _const(rgb, gloss=0.0, emit=None):
    a = lin(rgb)
    e = lin(emit) * 3.0 if emit is not None else np.zeros(3, np.float32)

    def fn(p, n):
        return np.broadcast_to(a, p.shape).copy(), np.broadcast_to(e, p.shape).copy(), np.full(len(p), gloss)

    return fn


def _paint(rgb, seed=1):
    a = lin(rgb)

    def fn(p, n):
        u = p[:, 0] + p[:, 2]
        v = p[:, 1]
        f = 0.985 + 0.03 * fbm(u / 40, v / 40, seed, 3)
        return a * f[:, None], np.zeros_like(p), np.zeros(len(p))

    return fn


def _tiles(rgb, size=60.0, grout=(150, 144, 136), vein=0.05, gloss=0.12, seed=3, ox=0.0, oz=0.0):
    a, g = lin(rgb), lin(grout)

    def fn(p, n):
        x, z = p[:, 0] - ox, p[:, 2] - oz
        ix, iz = np.floor(x / size), np.floor(z / size)
        fx, fz = x / size - ix, z / size - iz
        tint = 0.96 + 0.08 * _hash(ix, iz, seed)
        marb = fbm(x / 35 + ix * 7, z / 35 + iz * 5, seed + 9, 4)
        veins = np.abs(np.sin((x + z) / 18 + marb * 7)) ** 14
        col = a * (tint * (0.97 + 0.06 * marb) - vein * veins)[:, None]
        gw = 0.3 / size
        is_g = (np.minimum(fx, 1 - fx) < gw) | (np.minimum(fz, 1 - fz) < gw)
        col[is_g] = g
        gl = np.where(is_g, 0.0, gloss)
        return col, np.zeros_like(p), gl

    return fn


def _wall_tiles(rgb, tw=30.0, th=45.0, top=210.0, paint=(230, 228, 222), grout=(196, 194, 188), seed=5):
    a, g, pa = lin(rgb), lin(grout), lin(paint)

    def fn(p, n):
        u = np.where(np.abs(n[:, 2]) > 0.5, p[:, 0], p[:, 2])
        v = p[:, 1]
        iu, iv = np.floor(u / tw), np.floor(v / th)
        fu, fv = u / tw - iu, v / th - iv
        tint = 0.97 + 0.05 * _hash(iu, iv, seed)
        col = a * (tint + 0.02 * fbm(u / 20, v / 20, seed, 2))[:, None]
        is_g = (np.minimum(fu, 1 - fu) < 0.25 / tw) | (np.minimum(fv, 1 - fv) < 0.25 / th)
        col[is_g] = g
        col[v > top] = pa * (0.985 + 0.03 * fbm(u[v > top] / 40, v[v > top] / 40, seed, 3))[:, None]
        return col, np.zeros_like(p), np.zeros(len(p))

    return fn


def _wood(rgb, vertical=True, seed=7, grooves=(), groove_y=(), handles=()):
    a = lin(rgb)

    def fn(p, n):
        if vertical:
            u = np.where(np.abs(n[:, 2]) > 0.5, p[:, 0], p[:, 2])
            v = p[:, 1]
        else:
            u = p[:, 1] + np.where(np.abs(n[:, 1]) > 0.5, p[:, 2], 0)
            v = p[:, 0]
        warp = fbm(u / 6, v / 60, seed, 3)
        rings = 0.5 + 0.5 * np.sin(u * 1.7 + warp * 9)
        fine = vnoise(u * 3, v / 4, seed + 3)
        col = a * (0.86 + 0.1 * rings + 0.06 * fine)[:, None]
        front = np.abs(n[:, 2]) > 0.5
        for gx in grooves:
            col[front & (np.abs(p[:, 0] - gx) < 0.35)] *= 0.35
        for gy in groove_y:
            col[front & (np.abs(p[:, 1] - gy) < 0.35)] *= 0.35
        for hx0, hx1, hy in handles:
            col[front & (p[:, 0] > hx0) & (p[:, 0] < hx1) & (np.abs(p[:, 1] - hy) < 0.6)] *= 0.25
        return col, np.zeros_like(p), np.zeros(len(p))

    return fn


def _fabric(rgb, seed=11, folds=0.0, sheer=False):
    a = lin(rgb)

    def fn(p, n):
        u = np.where(np.abs(n[:, 2]) > 0.5, p[:, 0], p[:, 2])
        v = p[:, 1]
        weave = 0.94 + 0.06 * vnoise(u * 2.5, v * 2.5, seed) + 0.04 * fbm(u / 15, v / 15, seed + 1, 2)
        if folds:
            weave = weave * (0.86 + 0.14 * np.sin(u * 2 * np.pi / folds))
        e = a * 0.35 if sheer else np.zeros(3, np.float32)
        return (
            a * weave[:, None],
            np.broadcast_to(e, p.shape).copy() * (weave[:, None] if sheer else 1),
            np.zeros(len(p)),
        )

    return fn


def _leaves(rgb, seed=13):
    a = lin(rgb)

    def fn(p, n):
        f = fbm(p[:, 0] / 1.5, p[:, 1] / 1.5 + p[:, 2] / 1.5, seed, 3)
        up = 0.75 + 0.35 * np.clip(n[:, 1], -1, 1)
        col = a * ((0.35 + 1.0 * f) * up)[:, None]
        return col, np.zeros_like(p), np.zeros(len(p))

    return fn


def _sky(seed=17):
    def fn(p, n):
        y = p[:, 1]
        col = np.ones_like(p) * np.array([1.0, 1.02, 1.08], np.float32)
        bld = (np.sin(p[:, 0] / 9) > 0.2) & (y < 140 + 25 * vnoise(p[:, 0] / 12, 0 * y, seed))
        col[bld] = np.array([0.72, 0.75, 0.8], np.float32)
        return np.zeros_like(p), col * 2.6, np.zeros(len(p))

    return fn


def _old_tv(x0, x1, y0, y1):
    def fn(p, n):
        col = np.broadcast_to(lin((22, 22, 24)), p.shape).copy()
        front = n[:, 2] < -0.5
        u, v = (p[:, 0] - x0) / (x1 - x0), (p[:, 1] - y0) / (y1 - y0)
        inner = (u > 0.015) & (u < 0.985) & (v > 0.03) & (v < 0.985) & front
        sheen = np.clip(1.2 - (u + (1 - v)) * 1.1, 0, 1) ** 2 * 0.05
        col[inner] = lin((10, 10, 12)) + sheen[inner, None]
        return col, np.zeros_like(p), np.zeros(len(p))

    return fn


# ---------------------------------------------------------------- the fixture rooms

THEMES = {
    "living": dict(
        wall=(228, 220, 206),
        side=(222, 214, 200),
        floor=(186, 176, 160),
        grout=(140, 130, 116),
        door=(150, 102, 62),
        frame=(120, 80, 48),
        unit=(176, 136, 96),
        chair=(64, 104, 118),
        rug=(150, 120, 96),
        pot=(232, 228, 220),
        decor=(190, 120, 70),
        curtain=(236, 230, 218),
        vein=0.03,
        gloss=0.1,
    ),
    "luxury": dict(
        wall=(206, 194, 176),
        side=(214, 204, 188),
        floor=(218, 214, 208),
        grout=(186, 182, 176),
        door=(86, 54, 34),
        frame=(70, 44, 28),
        unit=(96, 62, 40),
        chair=(26, 86, 66),
        rug=(120, 96, 120),
        pot=(198, 160, 70),
        decor=(206, 170, 80),
        curtain=(226, 214, 190),
        vein=0.09,
        gloss=0.22,
    ),
}

# Shared geometry (cm). Main wall z = ZW, floor y = 0, ceiling y = HC.
XL, XR, HC, ZW = -220.0, 220.0, 285.0, 360.0
DOOR = (-200.0, -110.0, 0.0, 210.0)  # leaf x0, x1, y0, y1
WINDOW = (102.0, 182.0, 95.0, 225.0)
UNIT = (-95.0, 85.0, 0.0, 48.0, ZW - 42.0, ZW - 1.0)  # x0 x1 y0 y1 z0 z1 (with legs)
CHAIR = (106.0, 178.0, 0.0, 82.0, ZW - 86.0, ZW - 14.0)
UTIL_ZW = 320.0  # utility wall distance
OLD_TV = (-5.0 - 36.25, -5.0 + 36.25, 95.0 - 21.25, 95.0 + 21.25, ZW - 6.0, ZW - 1.0)  # a 32" set, 72.5 cm wide


def living_room(theme: str, old_tv: bool = False) -> Room:
    t = THEMES[theme]
    r = Room(XL, XR, HC, ZW)
    r.mats = {
        "wall": _paint(t["wall"], 1),
        "side": _paint(t["side"], 2),
        "ceiling": _paint((212, 210, 204), 3),
        "floor": _tiles(t["floor"], 60.0, t["grout"], t["vein"], t["gloss"], 3, ox=XL + 10, oz=ZW),
        "skirting": _const(tuple(int(c * 0.82) for c in t["floor"])),
        "door": _wood(t["door"], True, 7, handles=()),
        "frame": _wood(t["frame"], True, 8),
        "metal": _const((196, 194, 188)),
        "glass": _sky(),
        "upvc": _const((238, 238, 234)),
        "curtain": _fabric(t["curtain"], 21, folds=9.0, sheer=True),
        "rod": _const((60, 56, 52)),
        "unit": _wood(
            t["unit"],
            False,
            9,
            grooves=(-20.0, 40.0),
            groove_y=(40.5,),
            handles=((-58, -42, 30), (2, 18, 30), (62, 78, 30)),
        ),
        "leg": _const((40, 36, 34)),
        "chair": _fabric(t["chair"], 23),
        "rug": _fabric(t["rug"], 25),
        "pot": _const(t["pot"]),
        "leaves": _leaves((58, 104, 50)),
        "decor": _const(t["decor"]),
        "downlight": _const((255, 255, 255), emit=(255, 248, 236)),
    }
    o = r.objs.append
    # skirting on the three walls
    o(Obj((XL, 0, ZW - 1.2), (XR, 9, ZW), "skirting"))
    o(Obj((XL, 0, -400), (XL + 1.2, 9, ZW), "skirting"))
    o(Obj((XR - 1.2, 0, -400), (XR, 9, ZW), "skirting"))
    # door: leaf, architrave, lever
    x0, x1, y0, y1 = DOOR
    o(Obj((x0, y0, ZW - 2.0), (x1, y1, ZW), "door", 0.0))
    o(Obj((x0 - 7, 0, ZW - 3.0), (x0, y1 + 7, ZW), "frame"))
    o(Obj((x1, 0, ZW - 3.0), (x1 + 7, y1 + 7, ZW), "frame"))
    o(Obj((x0, y1, ZW - 3.0), (x1, y1 + 7, ZW), "frame"))
    o(Obj((x1 - 14, 99.0, ZW - 8.0), (x1 - 4, 101.5, ZW - 2.0), "metal", 0.2))
    o(Obj((x1 - 8, 95.0, ZW - 3.0), (x1 - 4, 106.0, ZW - 2.0), "metal"))
    # window: glass, uPVC frame + mullion, sheer curtains on a rod
    wx0, wx1, wy0, wy1 = WINDOW
    o(Obj((wx0, wy0, ZW - 0.5), (wx1, wy1, ZW + 0.1), "glass"))
    for lo, hi in (
        ((wx0 - 5, wy0 - 5, ZW - 3), (wx0, wy1 + 5, ZW)),
        ((wx1, wy0 - 5, ZW - 3), (wx1 + 5, wy1 + 5, ZW)),
        ((wx0, wy1, ZW - 3), (wx1, wy1 + 5, ZW)),
        ((wx0, wy0 - 5, ZW - 3), (wx1, wy0, ZW)),
        (((wx0 + wx1) / 2 - 2, wy0, ZW - 3), ((wx0 + wx1) / 2 + 2, wy1, ZW)),
    ):
        o(Obj(lo, hi, "upvc"))
    o(Obj((wx0 - 22, 246, ZW - 11), (wx1 + 22, 249, ZW - 7), "rod"))
    o(Obj((wx0 - 20, 14, ZW - 12), (wx0 - 2, 245, ZW - 5), "curtain", 0.25))
    o(Obj((wx1 + 2, 14, ZW - 12), (wx1 + 20, 245, ZW - 5), "curtain", 0.25))
    # TV unit: carcass on four legs, decor at the ends
    ux0, ux1, _, uy1, uz0, uz1 = UNIT
    o(Obj((ux0, 12, uz0), (ux1, uy1, uz1), "unit", 0.5))
    for lx in (ux0 + 4, ux1 - 8):
        for lz in (uz0 + 3, uz1 - 7):
            o(Obj((lx, 0, lz), (lx + 4, 12, lz + 4), "leg", 0.3))
    o(Obj((ux0 + 2, uy1, uz0 + 14), (ux0 + 14, uy1 + 15, uz0 + 26), "pot", 0.3))
    rng = np.random.default_rng(5)
    for _ in range(16):  # a leafy clump: many small overlapping blobs
        dx, dy, dz = rng.normal(0, 4.5), abs(rng.normal(0, 7)) + 6, rng.normal(0, 4)
        o(Obj((ux0 + 8 + dx, uy1 + 15 + dy, uz0 + 20 + dz), (rng.uniform(2.5, 4.5), 0, 0), "leaves", 0.12, sphere=True))
    o(Obj((ux1 - 15, uy1, uz0 + 14), (ux1 - 5, uy1 + 22, uz0 + 24), "decor", 0.3))
    # armchair by the window (seat, back, arms, legs)
    cx0, cx1, _, cy1, cz0, cz1 = CHAIR
    o(Obj((cx0 + 10, 12, cz0 + 4), (cx1 - 10, 46, cz1 - 14), "chair", 0.45))
    o(Obj((cx0 + 8, 30, cz1 - 16), (cx1 - 8, cy1, cz1), "chair", 0.45))
    o(Obj((cx0, 12, cz0), (cx0 + 12, 64, cz1), "chair", 0.45))
    o(Obj((cx1 - 12, 12, cz0), (cx1, 64, cz1), "chair", 0.45))
    for lx in (cx0 + 3, cx1 - 6):
        for lz in (cz0 + 3, cz1 - 6):
            o(Obj((lx, 0, lz), (lx + 3, 12, lz + 3), "leg", 0.25))
    # rug, wall-washer downlights
    o(Obj((-120, 0, ZW - 270), (110, 0.8, ZW - 70), "rug"))
    for x in (-110.0, 120.0):
        o(Obj((x - 6, HC - 1.0, ZW - 51), (x + 6, HC, ZW - 39), "downlight"))
        r.spots.append(((x, HC - 6, ZW - 45), 1.6))
    if old_tv:
        tx0, tx1, ty0, ty1, tz0, tz1 = OLD_TV
        r.mats["old_tv"] = _old_tv(tx0, tx1, ty0, ty1)
        o(Obj((tx0, ty0, tz0), (tx1, ty1, tz1), "old_tv", 0.55))
    r.windows = [WINDOW]
    r.window_power = 2.0
    r.ambient = 0.5
    r.exposure = 1.2 if theme == "living" else 1.3
    return r


def utility_room() -> Room:
    zw, xl, xr = UTIL_ZW, -110.0, 110.0
    r = Room(xl, xr, 280.0, zw)
    r.mats = {
        "wall": _wall_tiles((226, 226, 220), grout=(168, 166, 160)),
        "side": _wall_tiles((226, 226, 220), grout=(168, 166, 160), seed=6),
        "ceiling": _paint((232, 232, 228), 3),
        "floor": _tiles((168, 166, 160), 30.0, (120, 118, 112), 0.0, 0.05, 4, ox=xl, oz=zw),
        "metal": _const((200, 200, 204)),
        "pipe": _const((228, 228, 224)),
        "glass": _sky(),
        "grill": _const((70, 70, 72)),
    }
    o = r.objs.append
    o(Obj((40, 98, zw - 9), (44, 102, zw), "metal", 0.3))  # tap
    o(Obj((36, 100, zw - 12), (48, 104, zw - 8), "metal", 0.2))
    o(Obj((-104, 0, zw - 9), (-98, 280, zw - 3), "pipe", 0.35))  # drain pipe in the corner
    o(Obj((-60, 218, zw - 0.5), (20, 262, zw + 0.1), "glass"))  # ventilator
    for gx in np.arange(-56, 20, 8):
        o(Obj((gx, 218, zw - 2), (gx + 1.2, 262, zw), "grill"))
    r.windows = [(-60.0, 20.0, 218.0, 262.0)]
    r.window_power = 0.7
    r.ambient = 0.7
    r.exposure = 1.15
    r.floor_gloss = 0.05
    return r


# ---------------------------------------------------------------- reads


def _norm_box(cam: Cam, lo, hi) -> list[int] | None:
    xs = [lo[0], hi[0]]
    ys = [lo[1], hi[1]]
    zs = [lo[2], hi[2]]
    pts = cam.project([(x, y, z) for x in xs for y in ys for z in zs])
    x0, y0 = pts[:, 0].min(), pts[:, 1].min()
    x1, y1 = pts[:, 0].max(), pts[:, 1].max()
    x0, x1 = max(0, x0), min(cam.w, x1)
    y0, y1 = max(0, y0), min(cam.h, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return [round(y0 / cam.h * 1000), round(x0 / cam.w * 1000), round(y1 / cam.h * 1000), round(x1 / cam.w * 1000)]


def _pt(cam: Cam, p) -> dict:
    q = cam.project([p])[0]
    return {"x": round(q[0] / cam.w * 1000), "y": round(q[1] / cam.h * 1000)}


def living_read(cam: Cam, old_tv: bool = False, frontal: bool = True) -> dict:
    boxes = [
        ("wall", (XL, 0, ZW), (XR, HC, ZW)),
        ("door", (DOOR[0], DOOR[2], ZW - 2), (DOOR[1], DOOR[3], ZW)),
        ("window", (WINDOW[0], WINDOW[2], ZW), (WINDOW[1], WINDOW[3], ZW)),
        ("tv_unit", (UNIT[0], UNIT[2], UNIT[4]), (UNIT[1], UNIT[3], UNIT[5])),
        ("sofa", (CHAIR[0], CHAIR[2], CHAIR[4]), (CHAIR[1], CHAIR[3], CHAIR[5])),
        ("floor_tile", (-50.0, 0, ZW - 60), (10.0, 0, ZW)),
        ("ceiling_line", (XL, HC - 2, ZW), (XR, HC + 2, ZW)),
    ]
    if old_tv:
        boxes.append(("existing_tv", (OLD_TV[0], OLD_TV[2], OLD_TV[4]), (OLD_TV[1], OLD_TV[3], OLD_TV[5])))
    out = []
    for label, lo, hi in boxes:
        b = _norm_box(cam, lo, hi)
        if b:
            out.append({"label": label, "box_2d": b})
    cx = (XL + XR) / 2
    return {
        "kind": "room",
        "boxes": out,
        "light": "sunny",
        "frontal": frontal,
        "people_present": False,
        "wall_corners": [_pt(cam, p) for p in ((XL, HC, ZW), (XR, HC, ZW), (XR, 0, ZW), (XL, 0, ZW))],
        "floor_y": _pt(cam, (cx, 0, ZW))["y"],
        "ceiling_y": _pt(cam, (cx, HC, ZW))["y"],
    }


def utility_read(cam: Cam) -> dict:
    zw, xl, xr, hc = UTIL_ZW, -110.0, 110.0, 280.0
    boxes = [
        ("wall", (xl, 0, zw), (xr, hc, zw)),
        ("window", (-60.0, 218, zw), (20.0, 262, zw)),
    ]
    out = [{"label": lb, "box_2d": b} for lb, lo, hi in boxes if (b := _norm_box(cam, lo, hi))]
    return {
        "kind": "room",
        "boxes": out,
        "light": "moderate",
        "frontal": True,
        "people_present": False,
        "wall_corners": [_pt(cam, p) for p in ((xl, hc, zw), (xr, hc, zw), (xr, 0, zw), (xl, 0, zw))],
        "floor_y": _pt(cam, (0, 0, zw))["y"],
    }  # the ceiling is out of the frame: no ceiling_y


FRONTAL = Cam((0.0, 135.0, 0.0))
ANGLED = Cam((-150.0, 140.0, 70.0), yaw=26.0, pitch=-2.0)
UTILITY = Cam((0.0, 150.0, 0.0), pitch=-12.0)


def main() -> int:
    jobs = [
        ("room_living", living_room("living"), FRONTAL, living_read(FRONTAL)),
        ("room_luxury", living_room("luxury"), FRONTAL, living_read(FRONTAL)),
        ("room_oldtv", living_room("living", old_tv=True), FRONTAL, living_read(FRONTAL, old_tv=True)),
        ("room_angle", living_room("living"), ANGLED, living_read(ANGLED, frontal=False)),
        ("room_utility", utility_room(), UTILITY, utility_read(UTILITY)),
    ]
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    READ_DIR.mkdir(parents=True, exist_ok=True)
    for name, room, cam, read in jobs:
        img = render(room, cam)
        img.save(IMG_DIR / f"{name}.jpg", quality=86)
        (READ_DIR / f"{name}.json").write_text(json.dumps(read, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
