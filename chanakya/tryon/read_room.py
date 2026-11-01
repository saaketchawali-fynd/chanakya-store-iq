"""Room geometry: normalised (0-1000) read → pixel boxes, wall corners and floor/ceiling lines.
Geometry and light only — never decor."""

from __future__ import annotations

from dataclasses import dataclass, field

from chanakya.ai.schemas import ImageRead
from chanakya.tryon import geometry

PxBox = tuple[int, int, int, int]  # (x0, y0, x1, y1) in pixels

# Default wall when there is no reading: the whole photo (the associate framed the wall edge to edge).
DEFAULT_WALL_NORM = (0, 0, 1000, 1000)  # ymin, xmin, ymax, xmax
CORNER_LIMIT = (-1500, 2500)  # corners may lie outside the frame, but not absurdly far


def norm_to_px(box_2d: list[int] | tuple[int, ...], w: int, h: int) -> PxBox:
    ymin, xmin, ymax, xmax = (max(0, min(1000, int(v))) for v in box_2d[:4])
    if ymax < ymin:
        ymin, ymax = ymax, ymin
    if xmax < xmin:
        xmin, xmax = xmax, xmin
    return (round(xmin * w / 1000), round(ymin * h / 1000), round(xmax * w / 1000), round(ymax * h / 1000))


def _area(b: PxBox) -> int:
    return (b[2] - b[0]) * (b[3] - b[1])


@dataclass
class Room:
    """Everything the try-on knows about the photo, in pixels of the (resized) photo."""

    w: int
    h: int
    boxes: dict[str, list[PxBox]] = field(default_factory=dict)  # real boxes only, largest wall first
    corners: list[tuple[float, float]] | None = None  # TL, TR, BR, BL of the main wall (validated)
    floor_px: float | None = None  # wall/floor line at the wall's centre
    ceiling_px: float | None = None  # wall/ceiling line at the wall's centre
    frontal: bool | None = None
    light: str | None = None
    has_read: bool = False

    def first(self, label: str) -> PxBox | None:
        lst = self.boxes.get(label) or []
        return lst[0] if lst else None

    def all(self, label: str) -> list[PxBox]:
        return list(self.boxes.get(label) or [])

    @property
    def wall_box(self) -> PxBox | None:
        return self.first("wall")


def _corners(read: ImageRead, w: int, h: int) -> list[tuple[float, float]] | None:
    pts = read.wall_corners or []
    if len(pts) != 4:
        return None
    lo, hi = CORNER_LIMIT
    if any(not (lo <= p.x <= hi and lo <= p.y <= hi) for p in pts):
        return None
    px = [(p.x * w / 1000.0, p.y * h / 1000.0) for p in pts]
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = px
    # TL/TR above BL/BR, left edge left of the right edge, convex, big enough to be a wall
    if not (tly < bly and try_ < bry and tlx < trx and blx < brx):
        return None
    if not geometry.is_convex_quad(px) or geometry.quad_area(px) < 0.04 * w * h:
        return None
    return px


def parse(read: ImageRead | None, w: int, h: int) -> Room:
    room = Room(w=w, h=h)
    if read is None or read.kind != "room":
        return room
    room.has_read = True
    room.frontal, room.light = read.frontal, read.light
    for b in read.boxes:
        if len(b.box_2d) < 4:
            continue
        px = norm_to_px(b.box_2d, w, h)
        if px[2] - px[0] < 2 or px[3] - px[1] < 2:
            continue
        room.boxes.setdefault(b.label, []).append(px)
    for label in ("wall", "existing_tv", "door", "tv_unit", "sofa"):
        if room.boxes.get(label):
            room.boxes[label].sort(key=_area, reverse=True)  # the main one is the largest
    room.corners = _corners(read, w, h)
    if read.floor_y is not None and -500 <= read.floor_y <= 1500:
        room.floor_px = read.floor_y * h / 1000.0
    if read.ceiling_y is not None and -500 <= read.ceiling_y <= 1500:
        room.ceiling_px = read.ceiling_y * h / 1000.0
    elif room.boxes.get("ceiling_line"):
        cb = room.boxes["ceiling_line"][0]
        if (cb[3] - cb[1]) < 0.15 * h:  # a thin horizontal band, not a region
            room.ceiling_px = (cb[1] + cb[3]) / 2.0
    if room.floor_px is not None and room.ceiling_px is not None and room.floor_px - room.ceiling_px < 0.05 * h:
        room.floor_px = room.ceiling_px = None  # inconsistent lines: trust neither
    return room


def to_pixels(read: ImageRead | None, w: int, h: int) -> dict[str, list[PxBox]]:
    """Pixel boxes by label (legacy helper). Without a wall box the wall is the whole photo."""
    room = parse(read, w, h)
    out = {k: list(v) for k, v in room.boxes.items()}
    if not out.get("wall"):
        out["wall"] = [norm_to_px(DEFAULT_WALL_NORM, w, h)]
    return out


def main_wall(boxes: dict[str, list[PxBox]]) -> PxBox:
    return boxes["wall"][0]


def first(boxes: dict[str, list[PxBox]], label: str) -> PxBox | None:
    lst = boxes.get(label) or []
    return lst[0] if lst else None
