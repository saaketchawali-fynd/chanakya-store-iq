"""Wall-plane geometry (spec 17.1.3): homographies between the wall (cm) and the photo (px).

Wall coordinates: u = cm to the right of the wall's left edge, z = cm above the floor line.
The unit square (a to the right, b downwards) maps to the wall quad TL, TR, BR, BL in the photo; the
metric scale is cm per unit a (the wall width) and cm per unit b. A frontal wall is the affine special case.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Phone main camera (26 mm-equivalent on a 4:3 sensor): focal length ≈ 0.75 × the long side in pixels.
FOCAL_FRAC = 0.75
UNIT_SQUARE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))  # TL, TR, BR, BL


def apply(H: np.ndarray, pts) -> np.ndarray:
    """Map (N, 2) points through a 3x3 homography."""
    p = np.atleast_2d(np.asarray(pts, dtype=float))
    ph = np.c_[p, np.ones(len(p))] @ H.T
    w = ph[:, 2:3]
    w = np.where(np.abs(w) < 1e-12, 1e-12, w)
    return ph[:, :2] / w


def _normaliser(pts: np.ndarray) -> np.ndarray:
    c = pts.mean(axis=0)
    d = float(np.sqrt(((pts - c) ** 2).sum(axis=1)).mean()) or 1.0
    s = np.sqrt(2.0) / d
    return np.array([[s, 0.0, -s * c[0]], [0.0, s, -s * c[1]], [0.0, 0.0, 1.0]])


def homography(src, dst) -> np.ndarray:
    """Exact 3x3 H with dst ~ H·src from 4 point pairs (Hartley-normalised DLT)."""
    s0 = np.asarray(src, dtype=float)
    d0 = np.asarray(dst, dtype=float)
    ts, td = _normaliser(s0), _normaliser(d0)
    s, d = apply(ts, s0), apply(td, d0)
    a = np.zeros((8, 8))
    b = np.zeros(8)
    for i, ((x, y), (u, v)) in enumerate(zip(s, d, strict=True)):
        a[2 * i] = [x, y, 1, 0, 0, 0, -u * x, -u * y]
        a[2 * i + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y]
        b[2 * i], b[2 * i + 1] = u, v
    hn = np.append(np.linalg.solve(a, b), 1.0).reshape(3, 3)
    h = np.linalg.inv(td) @ hn @ ts
    return h / h[2, 2]


def affine_rect(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """Unit square → axis-aligned rectangle (frontal wall)."""
    return np.array([[x1 - x0, 0.0, x0], [0.0, y1 - y0, y0], [0.0, 0.0, 1.0]])


def camera_aspect(g: np.ndarray, w: int, h: int, focal_frac: float = FOCAL_FRAC) -> float:
    """True width/height of the wall rectangle behind the unit-square homography g (pinhole camera,
    principal point at the image centre). Exact pixel aspect for a frontal (affine) wall."""
    f = focal_frac * max(w, h)
    u0, v0 = w / 2.0, h / 2.0

    def k(col: np.ndarray) -> np.ndarray:
        return np.array([(col[0] - u0 * col[2]) / f, (col[1] - v0 * col[2]) / f, col[2]])

    n2 = float(np.linalg.norm(k(g[:, 1])))
    return float(np.linalg.norm(k(g[:, 0])) / n2) if n2 > 1e-12 else 1.0


def cross2(u, v) -> float:
    return float(u[0] * v[1] - u[1] * v[0])


def is_convex_quad(pts) -> bool:
    p = np.asarray(pts, dtype=float)
    if p.shape != (4, 2):
        return False
    signs = []
    for i in range(4):
        a, b, c = p[i], p[(i + 1) % 4], p[(i + 2) % 4]
        signs.append(cross2(b - a, c - b))
    signs = np.asarray(signs)
    return bool(np.all(signs > 1e-6) or np.all(signs < -1e-6))


def quad_area(pts) -> float:
    p = np.asarray(pts, dtype=float)
    x, y = p[:, 0], p[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


@dataclass
class Pose:
    """Camera pose recovered from the wall homography with the phone-camera intrinsics.
    Wall frame: u along the wall (right), z up, n out of the wall towards the camera (cm)."""

    k: np.ndarray  # intrinsics
    r: np.ndarray  # columns: u, z, n axes in camera coordinates
    t: np.ndarray  # wall origin in camera coordinates
    centre: np.ndarray  # camera centre in wall coordinates (u, z, n)

    def project(self, uzn) -> np.ndarray:
        p = np.atleast_2d(np.asarray(uzn, dtype=float)) @ self.r.T + self.t
        z = np.where(np.abs(p[:, 2]) < 1e-9, 1e-9, p[:, 2])
        q = p @ self.k.T
        return q[:, :2] / z[:, None]

    def plane_h(self, n: float) -> np.ndarray:
        """Homography (u, z) → px for the plane n cm in front of the wall."""
        hm = self.k @ np.c_[self.r[:, 0], self.r[:, 1], self.t + n * self.r[:, 2]]
        return hm / hm[2, 2]


@dataclass
class Wall:
    """Metric model of the main wall: G (unit square → px), the wall width in cm, cm per unit b and
    where the floor line sits. to_img/to_wall convert between (u, z) cm and photo pixels."""

    g: np.ndarray
    width_cm: float
    kz: float  # cm per unit b (down the quad)
    b_floor: float = 1.0
    img_size: tuple[int, int] = (0, 0)
    perspective: bool = False
    ceiling_b: float | None = None  # b of the ceiling line when it is known
    frame: bool = False  # True = no wall read: the whole photo is taken as the wall
    h: np.ndarray = field(init=False, repr=False)
    h_inv: np.ndarray = field(init=False, repr=False)
    _pose: Pose | None = field(init=False, repr=False, default=None)
    _pose_done: bool = field(init=False, repr=False, default=False)

    def __post_init__(self) -> None:
        m = np.array([[1.0 / self.width_cm, 0.0, 0.0], [0.0, -1.0 / self.kz, self.b_floor], [0.0, 0.0, 1.0]])
        self.h = self.g @ m
        self.h_inv = np.linalg.inv(self.h)

    def to_img(self, uz) -> np.ndarray:
        return apply(self.h, uz)

    def to_wall(self, xy) -> np.ndarray:
        return apply(self.h_inv, xy)

    def scale_at(self, u: float, z: float, d: float = 1.0) -> tuple[float, float]:
        """Local px per cm along the wall's horizontal and vertical at (u, z)."""
        p = self.to_img([(u, z), (u + d, z), (u, z + d)])
        return float(np.linalg.norm(p[1] - p[0]) / d), float(np.linalg.norm(p[2] - p[0]) / d)

    @property
    def top_z(self) -> float:  # z of the quad's top edge
        return self.b_floor * self.kz

    @property
    def ceiling_z(self) -> float | None:
        return None if self.ceiling_b is None else (self.b_floor - self.ceiling_b) * self.kz

    def rect_quad(self, uc: float, zc: float, w: float, h: float) -> np.ndarray:
        """Image quad (TL, TR, BR, BL) of a wall rectangle centred at (uc, zc)."""
        return self.to_img(
            [(uc - w / 2, zc + h / 2), (uc + w / 2, zc + h / 2), (uc + w / 2, zc - h / 2), (uc - w / 2, zc - h / 2)]
        )

    def visible_u(self, z: float, margin_px: float = 0.0) -> tuple[float, float] | None:
        """u-range of the wall line at height z (within the wall) that falls inside the photo."""
        w, h = self.img_size
        us = np.linspace(0.0, self.width_cm, 241)
        p = self.to_img(np.c_[us, np.full_like(us, z)])
        ok = (p[:, 0] >= margin_px) & (p[:, 0] <= w - margin_px) & (p[:, 1] >= -h) & (p[:, 1] <= 2 * h)
        if not ok.any():
            return None
        idx = np.where(ok)[0]
        return float(us[idx[0]]), float(us[idx[-1]])

    def visible_z(self, u: float, margin_px: float = 0.0) -> tuple[float, float] | None:
        """z-range of the vertical wall line at u that falls inside the photo."""
        _, h = self.img_size
        zs = np.linspace(-0.5 * self.kz, 1.5 * self.kz, 321)
        p = self.to_img(np.c_[np.full_like(zs, u), zs])
        ok = (p[:, 1] >= margin_px) & (p[:, 1] <= h - margin_px)
        if not ok.any():
            return None
        idx = np.where(ok)[0]
        lo, hi = float(zs[idx].min()), float(zs[idx].max())
        return lo, hi

    def pose(self) -> Pose | None:
        """Camera pose behind the photo (assumed phone focal length, principal point at the centre)."""
        if self._pose_done:
            return self._pose
        self._pose_done = True
        w, h = self.img_size
        if w <= 0 or h <= 0:
            return None
        f = FOCAL_FRAC * max(w, h)
        k = np.array([[f, 0.0, w / 2.0], [0.0, f, h / 2.0], [0.0, 0.0, 1.0]])
        m = np.linalg.inv(k) @ self.h
        if (m @ np.array([self.width_cm / 2, 100.0, 1.0]))[2] < 0:
            m = -m  # the wall must be in front of the camera
        n1, n2 = float(np.linalg.norm(m[:, 0])), float(np.linalg.norm(m[:, 1]))
        if n1 < 1e-12 or n2 < 1e-12:
            return None
        lam = 2.0 / (n1 + n2)
        r1, r2, t = m[:, 0] * lam, m[:, 1] * lam, m[:, 2] * lam
        rot = np.c_[r1, r2, np.cross(r1, r2)]
        u_, _, vt = np.linalg.svd(rot)
        rot = u_ @ vt  # nearest rotation
        if np.linalg.det(rot) <= 0:
            return None
        centre = -rot.T @ t
        if not np.all(np.isfinite(centre)) or centre[2] <= 30:
            return None  # camera behind (or inside) the wall: inconsistent read
        self._pose = Pose(k, rot, t, centre)
        return self._pose
