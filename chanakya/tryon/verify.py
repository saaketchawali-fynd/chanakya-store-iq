"""Verify an edited image (spec 17.1.6): same size, the product exactly where planned, nothing else changed.

Geometry passes when the dark TV frame is found on the planned box (legacy check) or when the product's
outline edges are still on the planned quad (works for thin bezels and perspective)."""

from __future__ import annotations

import numpy as np
from PIL import Image

from chanakya.tryon.read_room import PxBox

SIZE_TOL = 2
IOU_MIN = 0.9
DILATE = 40
OUTSIDE_MAX = 12 / 255
DARK_THRESHOLD = 70  # grayscale level counted as the dark TV body
EDGE_KEEP = 0.55  # each outline side keeps ≥ 55 % of its edge contrast
EDGE_MIN = 8.0  # grey levels: the composite must show an edge to compare against


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


def _sample(gray: np.ndarray, pts: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    xi = np.clip(np.round(pts[:, 0] - 0.5).astype(int), 0, w - 1)
    yi = np.clip(np.round(pts[:, 1] - 0.5).astype(int), 0, h - 1)
    return gray[yi, xi]


def edge_strength(gray: np.ndarray, quad, offset: float = 2.5, n: int = 32) -> np.ndarray:
    """Median contrast across each side of the quad (inside vs outside), one value per side."""
    q = np.asarray(quad, dtype=float)
    c = q.mean(axis=0)
    out = []
    t = np.linspace(0.12, 0.88, n)[:, None]
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 4:
            out.append(0.0)
            continue
        nrm = np.array([-d[1], d[0]]) / L
        if np.dot(nrm, (a + b) / 2 - c) < 0:
            nrm = -nrm
        p = a + t * d
        inside = _sample(gray, p - nrm * offset)
        outside = _sample(gray, p + nrm * offset)
        out.append(float(np.median(np.abs(inside - outside))))
    return np.asarray(out)


def check(composite: Image.Image, edited: Image.Image | None, planned: PxBox, quad=None) -> bool:
    if edited is None:
        return False
    if abs(edited.width - composite.width) > SIZE_TOL or abs(edited.height - composite.height) > SIZE_TOL:
        return False
    if edited.size != composite.size:
        edited = edited.resize(composite.size)
    a = np.asarray(composite.convert("RGB"), dtype=np.float32) / 255.0
    b = np.asarray(edited.convert("RGB"), dtype=np.float32) / 255.0
    # 1. the product is still exactly where it was planned
    gb = np.asarray(edited.convert("L"))
    found = _dark_bbox(gb, planned, margin=DILATE)
    geometry_ok = found is not None and iou(found, planned) >= IOU_MIN
    if not geometry_ok:
        q = (
            quad
            if quad is not None
            else [
                (planned[0], planned[1]),
                (planned[2], planned[1]),
                (planned[2], planned[3]),
                (planned[0], planned[3]),
            ]
        )
        ga = np.asarray(composite.convert("L"), dtype=np.float32)
        se, sa = edge_strength(gb.astype(np.float32), q), edge_strength(ga, q)
        geometry_ok = bool(np.all(sa >= EDGE_MIN) and np.all(se >= EDGE_KEEP * sa))
    if not geometry_ok:
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
