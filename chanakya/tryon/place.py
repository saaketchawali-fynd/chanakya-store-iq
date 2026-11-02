"""Placement (spec 17.1.4): where each product goes on the wall, in centimetres, from geometry only.

TV: on the old TV's centre when one is on the wall (the customer's real spot); else above the TV unit
(wall mount: bottom edge 15 cm above the unit top; stand: on the unit top); else centre 105 cm above the
floor, centred on the sofa opposite or on the wall. AC: top edge 30 cm below the ceiling (bottom edge at
least 210 cm above the floor). WM: standing on the floor line. Everything is clamped inside the wall."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from chanakya.tryon.geometry import Wall
from chanakya.tryon.read_room import PxBox, Room
from chanakya.tryon.scale import TV_FACE_DEPTH_CM, WALL_HEIGHT_CM, tv_height_cm, tv_width_cm

SAFFRON = (232, 131, 58)
BEZEL = (17, 17, 17)
TV_CENTRE_ABOVE_FLOOR_CM = 105.0
TV_ABOVE_UNIT_CM = 15.0
STAND_GAP_CM = 6.0  # pedestal: panel bottom above the TV-unit top
TV_ABOVE_SOFA_CM = 10.0
UNIT_MIN_WIDTH_CM = 80.0  # narrower than this is a side table, not a TV unit
UNIT_MAX_TOP_CM = 100.0
SOFA_MIN_WIDTH_CM = 120.0  # a sofa, not an armchair
AC_BELOW_CEILING_CM = 30.0
AC_ABOVE_FLOOR_CM = 210.0  # minimum height of the AC's bottom edge
CEILING_MARGIN_CM = 8.0
FLOOR_MARGIN_CM = 5.0
OBSTACLE_GAP_CM = 8.0
# (back, front) distance from the wall in cm: a slim wall-mounted TV, a TV on its pedestal on the unit,
# a split-AC indoor unit, a front-load washer standing against the wall.
DEPTH_CM = {"tv": (1.0, TV_FACE_DEPTH_CM), "tv_stand": (19.0, 24.0), "ac": (0.0, 22.0), "wm": (0.0, 56.0), "fridge": (0.0, 66.0)}


@dataclass
class Placed:
    sku: str
    box: PxBox  # bounding box of the product's image quad (px)
    solid: bool
    label: str
    quad: tuple[tuple[float, float], ...] = ()  # TL, TR, BR, BL in px
    rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # u centre, z centre, width, height (cm)
    dims_text: str | None = None
    category: str = "tv"
    mount: str = "wall"
    fits: bool | None = None  # WM vs niche width
    depth: float = 0.0  # front face, cm in front of the wall
    back: float = 0.0  # back face, cm in front of the wall
    door: str | None = None  # fridge door type (drawing only)
    photo: bytes | None = field(default=None, repr=False)  # the product's catalogue photo (drawn from it if usable)


@dataclass
class _Obj:
    label: str
    u0: float
    u1: float
    z0: float
    z1: float
    uc: float = field(init=False)

    def __post_init__(self) -> None:
        self.uc = (self.u0 + self.u1) / 2


def product_dims_cm(p) -> tuple[float, float] | None:
    """(width, height) in cm; for a TV without dimensions, from the size in its label (16:9)."""
    try:
        w, h = float(p.width_mm or 0) / 10, float(p.height_mm or 0) / 10
    except (TypeError, ValueError):
        w = h = 0.0
    if w > 1 and h > 1:
        return w, h
    if (p.category or "tv") == "tv":
        m = re.search(r"(\d{2,3}(?:\.\d)?)\s*(?:\"|”|inch|in\b)", p.label or "")
        if m:
            d = float(m.group(1))
            return tv_width_cm(d), tv_height_cm(d)
    return None


def _objects(wall: Wall, room: Room) -> dict[str, list[_Obj]]:
    out: dict[str, list[_Obj]] = {}
    for label in ("door", "window", "tv_unit", "sofa", "existing_tv"):
        for b in room.all(label):
            x0, y0, x1, y1 = b
            xc = (x0 + x1) / 2
            p = wall.to_wall([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (xc, y0)])
            if not np.all(np.isfinite(p)):
                continue
            u0, u1 = float(p[:4, 0].min()), float(p[:4, 0].max())
            z1 = float(p[4, 1])  # top edge at the box's centre: the back-top edge, on the wall plane
            z0 = float(p[:4, 1].min())
            out.setdefault(label, []).append(_Obj(label, u0, u1, min(z0, z1), z1))
    return out


def _overlaps(uc: float, zc: float, w: float, h: float, objs: list[_Obj], gap: float) -> bool:
    return any(
        uc - w / 2 < o.u1 + gap and uc + w / 2 > o.u0 - gap and zc - h / 2 < o.z1 and zc + h / 2 > o.z0 for o in objs
    )


def _avoid(uc: float, zc: float, w: float, h: float, objs: list[_Obj], lo: float, hi: float) -> float:
    """Shift horizontally to the nearest spot (inside [lo, hi]) that clears the obstacles."""
    gap = OBSTACLE_GAP_CM
    if not objs or not _overlaps(uc, zc, w, h, objs, gap):
        return uc
    cands = [uc] + [c for o in objs for c in (o.u0 - gap - w / 2, o.u1 + gap + w / 2)]
    ok = [c for c in cands if lo + w / 2 <= c <= hi - w / 2 and not _overlaps(c, zc, w, h, objs, gap)]
    return min(ok, key=lambda c: abs(c - uc)) if ok else uc


def _clamp(v: float, size: float, lo: float, hi: float) -> float:
    if hi - lo >= size:
        return min(max(v, lo + size / 2), hi - size / 2)
    return v


def _u_range(wall: Wall, z: float) -> tuple[float, float]:
    vis = wall.visible_u(z)
    if vis is None or vis[1] - vis[0] < 1:
        return 0.0, wall.width_cm
    return max(0.0, vis[0]), min(wall.width_cm, vis[1])


def _real_unit(units: list[_Obj], tv_w: float) -> _Obj | None:
    """A TV unit the TV would really stand on or hang above: wide and low (a side table or stool is not one)."""
    ok = [u for u in units if (u.u1 - u.u0) >= max(UNIT_MIN_WIDTH_CM, 0.6 * tv_w) and 20 <= u.z1 <= UNIT_MAX_TOP_CM]
    return max(ok, key=lambda u: u.u1 - u.u0) if ok else None


def _facing_sofa(sofas: list[_Obj], lo: float, hi: float) -> _Obj | None:
    """A sofa along this wall, wide and near the middle (an armchair at the edge of the photo does not decide)."""
    span = hi - lo
    ok = [s for s in sofas if (s.u1 - s.u0) >= SOFA_MIN_WIDTH_CM and lo + 0.2 * span <= s.uc <= hi - 0.2 * span]
    return max(ok, key=lambda s: s.u1 - s.u0) if ok else None


def _tv(wall: Wall, room: Room, objs: dict[str, list[_Obj]], w: float, h: float, mount: str):
    ceiling = wall.ceiling_z or WALL_HEIGHT_CM
    old = (objs.get("existing_tv") or [None])[0]
    unit = _real_unit(objs.get("tv_unit") or [], w)
    anchored = True
    if old is not None:
        ob = room.first("existing_tv")
        uc, zc = (float(v) for v in wall.to_wall([((ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2)])[0])
        if unit is not None and unit.u0 - w / 2 < uc < unit.u1 + w / 2:
            if mount == "stand":
                zc = unit.z1 + STAND_GAP_CM + h / 2
            elif zc - h / 2 < unit.z1 + 4:
                zc = unit.z1 + 4 + h / 2
    elif unit is not None:
        uc = unit.uc
        zc = unit.z1 + (STAND_GAP_CM if mount == "stand" else TV_ABOVE_UNIT_CM) + h / 2
    else:
        anchored = False
        zc = TV_CENTRE_ABOVE_FLOOR_CM
        lo, hi = _u_range(wall, zc)
        sofa = _facing_sofa(objs.get("sofa") or [], lo, hi)
        uc = sofa.uc if sofa is not None else (lo + hi) / 2  # else the middle of the visible wall
        if sofa is not None and uc - w / 2 < sofa.u1 and uc + w / 2 > sofa.u0 and zc - h / 2 < sofa.z1 + 10:
            zc = sofa.z1 + TV_ABOVE_SOFA_CM + h / 2
    zc = min(zc, ceiling - CEILING_MARGIN_CM - h / 2) if h < ceiling - 2 * CEILING_MARGIN_CM else zc
    zc = max(zc, FLOOR_MARGIN_CM + h / 2)
    lo, hi = _u_range(wall, zc)
    if not anchored:
        uc = _avoid(uc, zc, w, h, (objs.get("door") or []) + (objs.get("window") or []), lo, hi)
    uc = _clamp(uc, w, 0.0, wall.width_cm)
    if not anchored:
        uc = _clamp(uc, w, lo, hi)
    return uc, _fit_frame_z(wall, uc, zc, h, ceiling)


def _fit_frame_z(wall: Wall, uc: float, zc: float, h: float, ceiling: float) -> float:
    """Keep the product inside the photo vertically when it fits (true size, approximate height)."""
    vis = wall.visible_z(uc, margin_px=4)
    if vis is None:
        return zc
    lo, hi = vis[0], min(vis[1], ceiling)
    return _clamp(zc, h, lo, hi)


def _ac(wall: Wall, objs: dict[str, list[_Obj]], w: float, h: float):
    if wall.ceiling_z is not None:
        zc = wall.ceiling_z - AC_BELOW_CEILING_CM - h / 2
    else:
        zc = max(WALL_HEIGHT_CM - AC_BELOW_CEILING_CM - h / 2, AC_ABOVE_FLOOR_CM + h / 2)
    lo, hi = _u_range(wall, zc)
    uc = (lo + hi) / 2
    uc = _avoid(uc, zc, w, h, (objs.get("window") or []) + (objs.get("existing_tv") or []), lo, hi)
    uc = _clamp(_clamp(uc, w, 0.0, wall.width_cm), w, lo, hi)
    return uc, _fit_frame_z(wall, uc, zc, h, wall.ceiling_z or WALL_HEIGHT_CM)


def _wm(wall: Wall, objs: dict[str, list[_Obj]], w: float, h: float):
    zc = h / 2
    lo, hi = _u_range(wall, zc)
    uc = (lo + hi) / 2
    floor_objs = [o for k in ("door", "tv_unit", "sofa") for o in objs.get(k) or []]
    uc = _avoid(uc, zc, w, h, floor_objs, lo, hi)
    uc = _clamp(_clamp(uc, w, 0.0, wall.width_cm), w, lo, hi)
    if wall.frame:  # floor only guessed: keep the machine in the photo
        zc = _fit_frame_z(wall, uc, zc, h, WALL_HEIGHT_CM)
    return uc, zc


def plan(
    category: str,
    products: list,
    wall: Wall,
    room: Room,
    niche_width_cm: float | None = None,
) -> list[Placed]:
    """Wall rectangle and image quad for every product. The primary (solid) sets the anchor; comparison
    sizes share its centre (TV on a stand / WM: its bottom edge)."""
    sized = [(p, product_dims_cm(p)) for p in products]
    sized = [(p, d) for p, d in sized if d is not None]
    if not sized:
        return []
    primary, (w, h) = next(((p, d) for p, d in sized if p.solid), sized[0])
    mount = getattr(primary, "mount", "wall") or "wall"
    objs = _objects(wall, room)
    if category == "ac":
        uc, zc = _ac(wall, objs, w, h)
    elif category in ("wm", "fridge"):
        uc, zc = _wm(wall, objs, w, h)  # floor-standing: same rules as the machine
    else:
        uc, zc = _tv(wall, room, objs, w, h, mount)
    back, front = DEPTH_CM.get("tv_stand" if category == "tv" and mount == "stand" else category, DEPTH_CM["tv"])
    pose = wall.pose()
    if pose is None or front > 0.6 * pose.centre[2]:
        back = front = 0.0  # no usable camera: draw on the wall plane
    out: list[Placed] = []
    for p, (pw, ph) in sized:
        pzc = zc
        if category in ("wm", "fridge") or (category == "tv" and mount == "stand"):
            pzc = zc - h / 2 + ph / 2  # same bottom edge
        if front > 0 and pose is not None:
            corners = [(uc - pw / 2, pzc + ph / 2), (uc + pw / 2, pzc + ph / 2), (uc + pw / 2, pzc - ph / 2)]
            corners.append((uc - pw / 2, pzc - ph / 2))
            quad = pose.project([(u, z, front) for u, z in corners])
        else:
            quad = wall.rect_quad(uc, pzc, pw, ph)
        box = (
            int(np.floor(quad[:, 0].min())),
            int(np.floor(quad[:, 1].min())),
            int(np.ceil(quad[:, 0].max())),
            int(np.ceil(quad[:, 1].max())),
        )
        fits = (pw <= niche_width_cm) if category in ("wm", "fridge") and niche_width_cm else None
        out.append(
            Placed(
                sku=p.sku,
                box=box,
                solid=p is primary,
                label=p.label,
                quad=tuple((float(x), float(y)) for x, y in quad),
                rect=(float(uc), float(pzc), float(pw), float(ph)),
                dims_text=getattr(p, "dims_text", None),
                category=category,
                mount=mount,
                fits=fits,
                depth=front,
                back=back,
                door=getattr(p, "door", None),
                photo=getattr(p, "photo", None) if p is primary else None,
            )
        )
    return out
