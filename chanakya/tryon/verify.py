"""Verify an edited image (spec 17.1.6): same size, TV rectangle where planned, nothing else changed."""

from __future__ import annotations

import numpy as np
from PIL import Image

from chanakya.tryon.read_room import PxBox

SIZE_TOL = 2
IOU_MIN = 0.9
DILATE = 40
OUTSIDE_MAX = 12 / 255
DARK_THRESHOLD = 70  # grayscale level counted as the dark TV body


def iou(a: PxBox, b: PxBox) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _dark_bbox(gray: np.ndarray, region: PxBox, margin: int) -> PxBox | None:
    h, w = gray.shape
    x0, y0 = max(0, region[0] - margin), max(0, region[1] - margin)
    x1, y1 = min(w, region[2] + margin), min(h, region[3] + margin)
    sub = gray[y0:y1, x0:x1]
    mask = sub < DARK_THRESHOLD
    if not mask.any():
        return None
    # keep rows/cols that are mostly dark: the bezel frame forms dense dark rows/cols
    rows = np.where(mask.mean(axis=1) > 0.5)[0]
    cols = np.where(mask.mean(axis=0) > 0.5)[0]
    if not len(rows) or not len(cols):
        return None
    return (x0 + int(cols[0]), y0 + int(rows[0]), x0 + int(cols[-1]) + 1, y0 + int(rows[-1]) + 1)


def check(composite: Image.Image, edited: Image.Image | None, planned: PxBox) -> bool:
    if edited is None:
        return False
    if abs(edited.width - composite.width) > SIZE_TOL or abs(edited.height - composite.height) > SIZE_TOL:
        return False
    if edited.size != composite.size:
        edited = edited.resize(composite.size)
    a = np.asarray(composite.convert("RGB"), dtype=np.float32) / 255.0
    b = np.asarray(edited.convert("RGB"), dtype=np.float32) / 255.0
    # 1. dark TV rectangle inside the planned box (measured on the edited image vs the composite's own)
    gb = np.asarray(edited.convert("L"))
    found = _dark_bbox(gb, planned, margin=DILATE)
    if found is None or iou(found, planned) < IOU_MIN:
        return False
    # 2. nothing else changed outside the dilated planned box
    h, w = a.shape[:2]
    mask = np.ones((h, w), dtype=bool)
    x0, y0 = max(0, planned[0] - DILATE), max(0, planned[1] - DILATE)
    x1, y1 = min(w, planned[2] + DILATE), min(h, planned[3] + DILATE)
    mask[y0:y1, x0:x1] = False
    if not mask.any():
        return True
    diff = np.abs(a - b).mean(axis=2)[mask].mean()
    return bool(diff <= OUTSIDE_MAX)
