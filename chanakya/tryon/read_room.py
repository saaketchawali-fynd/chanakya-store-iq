"""Room geometry: normalised (0-1000) boxes → pixel boxes. Geometry and light only — never decor."""

from __future__ import annotations

from chanakya.ai.schemas import ImageRead

PxBox = tuple[int, int, int, int]  # (x0, y0, x1, y1) in pixels

# Default wall when no reading is available: central region y 12–62 %, x 15–85 %.
DEFAULT_WALL_NORM = (120, 150, 620, 850)  # ymin, xmin, ymax, xmax


def norm_to_px(box_2d: list[int] | tuple[int, ...], w: int, h: int) -> PxBox:
    ymin, xmin, ymax, xmax = (max(0, min(1000, int(v))) for v in box_2d[:4])
    if ymax < ymin:
        ymin, ymax = ymax, ymin
    if xmax < xmin:
        xmin, xmax = xmax, xmin
    return (round(xmin * w / 1000), round(ymin * h / 1000), round(xmax * w / 1000), round(ymax * h / 1000))


def to_pixels(read: ImageRead | None, w: int, h: int) -> dict[str, list[PxBox]]:
    """Pixel boxes by label. Only spatial labels from the schema are used."""
    out: dict[str, list[PxBox]] = {}
    if read is not None and read.kind == "room":
        for b in read.boxes:
            if len(b.box_2d) < 4:
                continue
            px = norm_to_px(b.box_2d, w, h)
            if px[2] - px[0] < 2 or px[3] - px[1] < 2:
                continue
            out.setdefault(b.label, []).append(px)
    if not out.get("wall"):
        # no wall read → default geometry only (other boxes are not trusted without a wall)
        out = {"wall": [norm_to_px(DEFAULT_WALL_NORM, w, h)]}
        if read is not None and read.kind == "room":
            for b in read.boxes:
                if b.label == "person" and len(b.box_2d) >= 4:
                    out.setdefault("person", []).append(norm_to_px(b.box_2d, w, h))
    # the main wall is the largest wall box
    out["wall"].sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return out


def main_wall(boxes: dict[str, list[PxBox]]) -> PxBox:
    return boxes["wall"][0]


def first(boxes: dict[str, list[PxBox]], label: str) -> PxBox | None:
    lst = boxes.get(label) or []
    return lst[0] if lst else None
