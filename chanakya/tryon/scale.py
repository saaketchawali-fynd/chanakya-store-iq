"""Scale estimate (spec 17.1.3): pixels per centimetre from a known reference."""

from __future__ import annotations

from chanakya.tryon.read_room import PxBox, first, main_wall

DOOR_CM = 205.0  # Indian standard door ≈ 2,050–2,100 mm
TILE_CM = 60.0  # 600 mm floor tiles
CEILING_CM = 275.0  # typical floor-to-ceiling height
FT_CM = 30.48
MIN_CONFIDENCE = 0.6


def estimate(boxes: dict[str, list[PxBox]], wall_width_ft: float | None) -> tuple[float | None, float]:
    """Returns (px_per_cm, confidence). Order: associate's wall width → door → floor tile → wall height."""
    wall = main_wall(boxes)
    if wall_width_ft and wall_width_ft > 0:
        return (wall[2] - wall[0]) / (wall_width_ft * FT_CM), 0.9
    door = first(boxes, "door")
    if door and door[3] - door[1] > 10:
        return (door[3] - door[1]) / DOOR_CM, 0.7
    tile = first(boxes, "floor_tile")
    if tile and tile[2] - tile[0] > 4:
        return (tile[2] - tile[0]) / TILE_CM, 0.6
    if wall[3] - wall[1] > 10:
        return (wall[3] - wall[1]) / CEILING_CM, 0.4
    return None, 0.0
