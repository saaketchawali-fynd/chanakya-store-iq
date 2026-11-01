"""True scale (spec 17.1.3): a metric model of the main wall from the best reference in the photo.

Priority — the first usable reference wins; the other wall dimension follows from the camera-model aspect
of the wall quad (exact for a frontal photo), so products keep their true proportions:
  (a) existing TV box + the size the associate stated → 16:9 width + ~1 % bezel     conf 0.95
  (b) wall width stated by the associate → the wall's pixel width (corners / box)    conf 0.9
      (no read at all: the photo is taken to frame the wall edge to edge            conf 0.7)
  (c) door box height ≈ 210 cm                                                       conf 0.75
  (d) floor line → ceiling line ≈ 285 cm (Mumbai flats)                              conf 0.6
  (e) floor tile ≈ 60 cm                                                             conf 0.6
  (f) wall box height ≈ 285 cm                                                       conf 0.4
Below MIN_CONFIDENCE the caller asks the associate for the wall width and calls again.
With wall corners the model is a homography, so the scale is right at the depth where the product goes."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from chanakya.tryon import geometry
from chanakya.tryon.geometry import Wall
from chanakya.tryon.read_room import PxBox, Room, first

DOOR_CM = 210.0  # Indian door leaf / frame opening ≈ 2,100 mm
TILE_CM = 60.0  # 600 mm vitrified floor tiles
WALL_HEIGHT_CM = 285.0  # floor-to-ceiling, typical Mumbai flat
CEILING_CM = WALL_HEIGHT_CM  # legacy name
FT_CM = 30.48
IN_CM = 2.54
TV_WIDTH_PER_IN = 0.8716  # 16:9 screen: width / diagonal
TV_HEIGHT_PER_IN = 0.4903  # 16:9 screen: height / diagonal
TV_BEZEL = 1.01  # thin modern bezel adds ~1 %
TV_FACE_DEPTH_CM = 5.0  # a wall-mounted TV's screen stands ~5 cm proud of the wall
CAMERA_HEIGHT_CM = 135.0  # phone held roughly level at chest/eye height (no-read fallback only)
MIN_CONFIDENCE = 0.6
CONF = {
    "existing_tv": 0.95,
    "wall_width": 0.9,
    "frame_width": 0.7,
    "door": 0.75,
    "ceiling": 0.6,
    "floor_tile": 0.6,
    "wall_height": 0.4,
}
SANE_CEILING_CM = (215.0, 420.0)
EDGE_PX = 2


@dataclass
class Scale:
    wall: Wall | None
    confidence: float
    source: str | None
    note: str = ""

    @property
    def px_per_cm(self) -> float | None:
        if self.wall is None:
            return None
        return self.wall.scale_at(self.wall.width_cm / 2, 105.0)[0]


def tv_width_cm(diag_in: float) -> float:
    return diag_in * TV_WIDTH_PER_IN * IN_CM * TV_BEZEL


def tv_height_cm(diag_in: float) -> float:
    return diag_in * TV_HEIGHT_PER_IN * IN_CM + diag_in * TV_WIDTH_PER_IN * IN_CM * (TV_BEZEL - 1)


def _b_at(g: np.ndarray, a: float, y: float) -> float:
    """b on the wall's vertical line a whose image row is y."""
    num = g[1, 0] * a + g[1, 2] - y * (g[2, 0] * a + g[2, 2])
    den = y * g[2, 1] - g[1, 1]
    return float(num / den) if abs(den) > 1e-12 else 1.0


def _is_affine(g: np.ndarray) -> bool:
    sx, sy = abs(g[0, 0]) + 1e-9, abs(g[1, 1]) + 1e-9
    return abs(g[2, 0]) < 1e-5 and abs(g[2, 1]) < 1e-5 and abs(g[0, 1]) < 0.01 * sy and abs(g[1, 0]) < 0.01 * sx


def _base(room: Room) -> tuple[np.ndarray, bool, bool]:
    """(G, perspective, frame): wall corners → homography; wall box → frontal; nothing → whole photo."""
    if room.corners:
        try:
            g = geometry.homography(geometry.UNIT_SQUARE, room.corners)
            if np.all(np.isfinite(g)):
                return g, not _is_affine(g), False
        except np.linalg.LinAlgError:
            pass
    wb = room.wall_box
    if wb is not None:
        return geometry.affine_rect(*wb), False, False
    return geometry.affine_rect(0, 0, room.w, room.h), False, True


def _hspan(g_inv: np.ndarray, x0: float, x1: float, y: float) -> float:
    p = geometry.apply(g_inv, [(x0, y), (x1, y)])
    return float(abs(p[1, 0] - p[0, 0]))


def _vspan(g_inv: np.ndarray, x: float, y0: float, y1: float) -> float:
    p = geometry.apply(g_inv, [(x, y0), (x, y1)])
    return float(abs(p[1, 1] - p[0, 1]))


def _touches_lr(b: PxBox, w: int) -> bool:
    return b[0] <= EDGE_PX or b[2] >= w - EDGE_PX


def _touches_tb(b: PxBox, h: int) -> bool:
    return b[1] <= EDGE_PX or b[3] >= h - EDGE_PX


def estimate(room: Room, wall_width_ft: float | None = None, existing_tv_size_in: float | None = None) -> Scale:
    w, h = room.w, room.h
    g, perspective, frame = _base(room)
    g_inv = np.linalg.inv(g)
    rho = geometry.camera_aspect(g, w, h)  # true width / height of the wall quad

    b_floor = 1.0
    if room.floor_px is not None:
        b_floor = _b_at(g, 0.5, room.floor_px)
    ceiling_b: float | None = None
    if room.ceiling_px is not None:
        ceiling_b = _b_at(g, 0.5, room.ceiling_px)
    elif room.corners:
        ceiling_b = 0.0  # the wall quad's top edge is the ceiling line

    wc = kz = None
    source, conf, note = None, 0.0, ""

    # (a) the TV already on that wall, with the size the associate stated
    tv = first(room.boxes, "existing_tv")
    if tv is not None and existing_tv_size_in and existing_tv_size_in > 0 and not _touches_lr(tv, w):
        span = _hspan(g_inv, tv[0], tv[2], (tv[1] + tv[3]) / 2)
        if span > 1e-3:
            wc, source = tv_width_cm(existing_tv_size_in) / span, "existing_tv"

    # (b) wall width stated by the associate
    if source is None and wall_width_ft and wall_width_ft > 0:
        wc = wall_width_ft * FT_CM
        source = "frame_width" if frame else "wall_width"
        wb = room.wall_box
        if source == "wall_width" and not room.corners and wb is not None and _touches_lr(wb, w):
            note = "wall box touches the frame edge"

    # (c) door ≈ 210 cm
    if source is None:
        for door in room.all("door"):
            if _touches_tb(door, h) or door[3] - door[1] < 0.05 * h:
                continue  # cut off by the frame: its height is unknown
            xc = (door[0] + door[2]) / 2
            span = _vspan(g_inv, xc, door[1], door[3])
            if span <= 1e-3:
                continue
            kz, source = DOOR_CM / span, "door"
            a_door = float(geometry.apply(g_inv, [(xc, door[3])])[0, 0])
            floor_y = float(geometry.apply(g, [(min(max(a_door, 0.0), 1.0), b_floor)])[0, 1])
            if not (-0.02 <= a_door <= 1.02) or door[3] > floor_y + 0.04 * h:
                note = "door not on the main wall plane"  # side wall or nearer the camera
            break

    # (d) floor line → ceiling line ≈ 285 cm
    if source is None and room.floor_px is not None and room.ceiling_px is not None and ceiling_b is not None:
        span = b_floor - ceiling_b
        if span > 0.02:
            kz, source = WALL_HEIGHT_CM / span, "ceiling"

    # (e) floor tile ≈ 60 cm. Its box width is its near edge; carry that scale back to the wall's floor line
    # with the level-camera horizon (image centre row): px per cm on the floor ∝ (y − horizon).
    if source is None:
        tile = first(room.boxes, "floor_tile")
        if tile is not None and tile[2] - tile[0] > 4:
            span = _hspan(g_inv, tile[0], tile[2], tile[3])
            horizon = h / 2
            a_t = float(geometry.apply(g_inv, [((tile[0] + tile[2]) / 2, tile[1])])[0, 0])
            floor_y = float(geometry.apply(g, [(a_t, b_floor)])[0, 1])
            if not frame and floor_y > horizon + 10 and tile[3] > floor_y:
                span *= (floor_y - horizon) / (tile[3] - horizon)
            if span > 1e-3:
                wc, source = TILE_CM / span, "floor_tile"

    # (f) the wall box / quad spans floor to ceiling ≈ 285 cm (weak: the box may be cut by the frame)
    if source is None and not frame:
        span = b_floor - (ceiling_b if ceiling_b is not None else 0.0)
        if span > 0.02:
            kz, source = WALL_HEIGHT_CM / span, "wall_height"

    if source is None:
        return Scale(None, 0.0, None, "no reference")
    conf = CONF[source]
    if wc is None:
        wc = kz * rho
    if kz is None:
        kz = wc / rho
    if not (40.0 <= wc <= 4000.0 and 40.0 <= kz <= 6000.0):
        return Scale(None, 0.0, source, "implausible scale")

    if frame and room.floor_px is None:
        b_floor = _frame_floor(room, kz)

    def build(wc_: float, kz_: float) -> Wall:
        return Wall(
            g=g,
            width_cm=float(wc_),
            kz=float(kz_),
            b_floor=float(b_floor),
            img_size=(w, h),
            perspective=perspective,
            ceiling_b=ceiling_b,
            frame=frame,
        )

    wall = build(wc, kz)
    if source == "existing_tv" and (pose := wall.pose()) is not None and pose.centre[2] > 4 * TV_FACE_DEPTH_CM:
        # the old TV's box is its screen, ~5 cm proud of the wall: carry its scale back to the wall plane
        k = pose.centre[2] / (pose.centre[2] - TV_FACE_DEPTH_CM)
        wall = build(wc * k, kz * k)

    # confidence adjustments: an off-plane door, an oblique photo without corners, an implausible ceiling
    if note == "door not on the main wall plane":
        conf = min(conf, 0.65)
    if note == "wall box touches the frame edge":
        conf = min(conf, 0.8)
    if room.frontal is False and not room.corners and source != "existing_tv":
        conf -= 0.1
        note = note or "oblique photo without wall corners"
    if source != "ceiling" and room.floor_px is not None and room.ceiling_px is not None and wall.ceiling_z:
        if not (SANE_CEILING_CM[0] <= wall.ceiling_z <= SANE_CEILING_CM[1]):
            conf -= 0.2
            note = f"implied ceiling {wall.ceiling_z:.0f} cm"
    return Scale(wall, round(max(0.0, conf), 3), source, note)


def _frame_floor(room: Room, kz: float) -> float:
    """b of the floor when only the photo frame is known: a door or TV unit standing on the floor, else a
    roughly level phone at CAMERA_HEIGHT_CM (image centre row)."""
    for label in ("door", "tv_unit"):
        b = first(room.boxes, label)
        if b is not None and b[3] < room.h - EDGE_PX:
            return b[3] / room.h
    return 0.5 + CAMERA_HEIGHT_CM / kz
