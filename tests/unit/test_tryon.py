"""Ghar Mein Dekho: true scale, perspective, placement, labels, verify, wealth-neutrality, never-raise.

Synthetic rooms are drawn with Pillow at a known px/cm (frontal) or projected with a known pinhole camera
(perspective), so every assertion is against ground truth."""

from __future__ import annotations

import io
import json
import math
import time
from itertools import combinations
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from chanakya.ai.schemas import Box, ImageRead, Point
from chanakya.tryon import TryonProduct, TryonRequest, run, scale, verify
from chanakya.tryon.geometry import FOCAL_FRAC
from chanakya.tryon.place import DEPTH_CM
from chanakya.tryon.read_room import to_pixels

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "data/fixtures/images"
READ = ROOT / "data/fixtures/image_read"
W, H = 1080, 810
WALL_RGB = (214, 206, 192)
WALL_LUM = 0.299 * WALL_RGB[0] + 0.587 * WALL_RGB[1] + 0.114 * WALL_RGB[2]


def _fixture_read(name: str) -> ImageRead:
    return ImageRead.model_validate(json.loads((READ / f"{name}.json").read_text()))


def _photo(name: str) -> bytes:
    return (IMG / f"{name}.jpg").read_bytes()


TV43 = TryonProduct("TV-MER-43", "tv", 957, 557, '43" · Meru Smart LED', True, dims_text="96 × 56 cm")
TV55 = TryonProduct(
    sku="TV-MER-55-QL120", category="tv", width_mm=1230, height_mm=708, label='55" · Meru QLED Pro', solid=True
)
TV65 = TryonProduct(sku="TV-MER-65-QL120", category="tv", width_mm=1450, height_mm=835, label='65"', solid=False)


def _req(name="room_living", read="room_living", **kw) -> TryonRequest:
    return TryonRequest(
        photo=_photo(name),
        read=_fixture_read(read) if read else None,
        products=[TV55, TV65],
        distance_text="Sofa se 10 ft",
        **kw,
    )


def _img(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b)).convert("RGB")


# ---------------------------------------------------------------- synthetic rooms


def _jpeg(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _norm(b) -> list[int]:
    x0, y0, x1, y1 = b
    return [round(y0 / H * 1000), round(x0 / W * 1000), round(y1 / H * 1000), round(x1 / W * 1000)]


def _room(ppc: float, floor_y: float = 700.0, *, door_x=None, old_tv=None, unit=None, sofa=None, tile=False):
    """Flat frontal room at a known px per cm. Returns (photo, pixel boxes, floor_y, ceiling_y)."""
    im = Image.new("RGB", (W, H), WALL_RGB)
    d = ImageDraw.Draw(im)
    ceil_y = floor_y - scale.WALL_HEIGHT_CM * ppc
    if floor_y < H:
        d.rectangle((0, floor_y, W, H), fill=(168, 158, 144))
    if ceil_y > 0:
        d.rectangle((0, 0, W, ceil_y), fill=(236, 234, 230))
    boxes = {"wall": (0, max(0.0, ceil_y), W, floor_y)}
    if door_x is not None:
        boxes["door"] = (door_x, floor_y - scale.DOOR_CM * ppc, door_x + 90 * ppc, floor_y)
        d.rectangle(boxes["door"], fill=(126, 84, 52))
    if old_tv is not None:  # (centre x, centre y, inches)
        cx, cy, inches = old_tv
        tw, th = scale.tv_width_cm(inches) * ppc, scale.tv_height_cm(inches) * ppc
        boxes["existing_tv"] = (cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2)
        d.rectangle(boxes["existing_tv"], fill=(18, 18, 20))
    if unit is not None:  # (centre x, width cm, height cm)
        cx, uw, uh = unit
        boxes["tv_unit"] = (cx - uw * ppc / 2, floor_y - uh * ppc, cx + uw * ppc / 2, floor_y)
        d.rectangle(boxes["tv_unit"], fill=(150, 112, 78))
    if sofa is not None:  # (centre x, width cm)
        cx, sw = sofa
        boxes["sofa"] = (cx - sw * ppc / 2, floor_y - 85 * ppc, cx + sw * ppc / 2, floor_y + 30)
        d.rectangle(boxes["sofa"], fill=(70, 96, 120))
    if tile:
        x0 = W / 2 - 30 * ppc
        boxes["floor_tile"] = (x0, floor_y, x0 + 60 * ppc, floor_y + 12)
    return _jpeg(im), boxes, floor_y, ceil_y


def _read(boxes: dict, labels=None, floor_y=None, ceiling_y=None, corners=None, frontal=True) -> ImageRead:
    keep = labels or list(boxes)
    return ImageRead(
        kind="room",
        boxes=[Box(label=k, box_2d=_norm(boxes[k])) for k in keep if k in boxes],
        frontal=frontal,
        light="moderate",
        floor_y=None if floor_y is None else round(floor_y / H * 1000),
        ceiling_y=None if ceiling_y is None else round(ceiling_y / H * 1000),
        wall_corners=corners or [],
    )


def _depth_factor(ppc: float, depth: float = DEPTH_CM["tv"][1]) -> float:
    """The TV's front face sits `depth` cm proud of the wall: it looks Z/(Z-depth) bigger."""
    z = FOCAL_FRAC * W / ppc
    return z / (z - depth)


def _rendered_width(img: Image.Image, box) -> int:
    """Outer width of the dark TV frame along the TV's centre row, measured on the output image."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    y = (box[1] + box[3]) // 2
    x0, x1 = max(0, box[0] - 25), min(img.width, box[2] + 25)
    dark = np.where(g[y, x0:x1] < 0.55 * WALL_LUM)[0]
    assert len(dark), "no TV frame found"
    return int(dark[-1] - dark[0] + 1)


def _within(value: float, expected: float, tol: float = 0.05) -> bool:
    return abs(value - expected) <= tol * expected


async def _check_true_width(res, ppc: float, width_cm: float = 123.0) -> None:
    assert res.error is None and res.image and not res.needs_wall_width
    expected = width_cm * ppc * _depth_factor(ppc)
    box = dict(res.planned)[TV55.sku]
    assert _within(box[2] - box[0], expected), (box, expected)
    measured = _rendered_width(_img(res.image), box)
    assert _within(measured, expected), (measured, expected)
    assert res.ms < 3000


# ---------------------------------------------------------------- true scale, one test per source


async def test_scale_existing_tv_true_width_and_centred_on_old_tv():
    ppc = 2.2
    photo, boxes, fy, cy = _room(ppc, old_tv=(600, 360, 32))
    res = await run(
        TryonRequest(photo=photo, read=_read(boxes, ["wall", "existing_tv"]), products=[TV55], existing_tv_size_in=32)
    )
    assert res.scale_source == "existing_tv" and res.scale_confidence == pytest.approx(0.95)
    await _check_true_width(res, ppc)
    b = dict(res.planned)[TV55.sku]
    assert abs((b[0] + b[2]) / 2 - 600) <= 3 and abs((b[1] + b[3]) / 2 - 360) <= 3  # the customer's real spot
    # the old TV is fully covered by the bigger new one: no dark old-TV pixels left outside the new frame
    g = np.asarray(_img(res.image).convert("L"))
    ox0, oy0, ox1, oy1 = (int(v) for v in boxes["existing_tv"])
    assert g[oy0 + 3 : oy1 - 3, ox0 + 3 : ox1 - 3].mean() > 60  # screen content, not the old black set


async def test_scale_wall_width_true_width():
    ppc = 2.5  # wall box spans 12 ft at 2.5 px/cm
    photo, boxes, fy, cy = _room(ppc, floor_y=720)
    wall_px = 12 * scale.FT_CM * ppc
    boxes["wall"] = (W / 2 - wall_px / 2, max(0, cy), W / 2 + wall_px / 2, fy)
    res = await run(TryonRequest(photo=photo, read=_read(boxes, ["wall"]), products=[TV55], wall_width_ft=12))
    assert res.scale_source == "wall_width" and res.scale_confidence == pytest.approx(0.9)
    await _check_true_width(res, ppc)


async def test_scale_door_true_width():
    ppc = 2.3
    photo, boxes, fy, cy = _room(ppc, door_x=60)
    res = await run(TryonRequest(photo=photo, read=_read(boxes, ["wall", "door"]), products=[TV55]))
    assert res.scale_source == "door" and res.scale_confidence == pytest.approx(0.75)
    await _check_true_width(res, ppc)


async def test_scale_floor_to_ceiling_true_width():
    ppc = 2.4
    photo, boxes, fy, cy = _room(ppc, floor_y=740)
    res = await run(TryonRequest(photo=photo, read=_read(boxes, ["wall"], floor_y=fy, ceiling_y=cy), products=[TV55]))
    assert res.scale_source == "ceiling" and res.scale_confidence == pytest.approx(0.6)
    await _check_true_width(res, ppc)
    # wall-mounted, no TV unit: centre ≈ 105 cm above the floor line
    b = dict(res.planned)[TV55.sku]
    assert abs((fy - (b[1] + b[3]) / 2) / ppc - 105) <= 4


async def test_scale_floor_tile_true_width():
    ppc = 2.4
    photo, boxes, fy, cy = _room(ppc, floor_y=700, tile=True)
    res = await run(TryonRequest(photo=photo, read=_read(boxes, ["wall", "floor_tile"]), products=[TV55]))
    assert res.scale_source == "floor_tile" and res.scale_confidence == pytest.approx(0.6)
    box = dict(res.planned)[TV55.sku]
    assert _within(box[2] - box[0], 123 * ppc * _depth_factor(ppc), 0.08)


async def test_priority_existing_tv_beats_wall_width_beats_door():
    ppc = 2.2
    photo, boxes, fy, cy = _room(ppc, door_x=40, old_tv=(620, 380, 40))
    read = _read(boxes, ["wall", "door", "existing_tv"])
    a = await run(TryonRequest(photo=photo, read=read, products=[TV55], existing_tv_size_in=40, wall_width_ft=30))
    assert a.scale_source == "existing_tv"
    b = await run(TryonRequest(photo=photo, read=read, products=[TV55], wall_width_ft=W / ppc / scale.FT_CM))
    assert b.scale_source == "wall_width"
    c = await run(TryonRequest(photo=photo, read=read, products=[TV55]))
    assert c.scale_source == "door"
    for r in (a, b, c):  # all three agree with the truth
        assert _within(r.px_per_cm, ppc * _depth_factor(ppc), 0.05) or _within(r.px_per_cm, ppc, 0.05)


async def test_no_read_wall_width_frames_the_photo():
    photo, *_ = _room(3.0)
    res = await run(TryonRequest(photo=photo, read=None, products=[TV55], wall_width_ft=10))
    assert res.scale_source == "frame_width" and not res.needs_wall_width and res.image
    b = dict(res.planned)[TV55.sku]
    ratio = (b[2] - b[0]) / W
    ppc = W / (10 * scale.FT_CM)
    assert _within(ratio, 123 / (10 * scale.FT_CM) * _depth_factor(ppc))  # NOT the old central-70 % wall


async def test_needs_wall_width_when_nothing_is_known():
    photo, boxes, fy, cy = _room(2.4)
    res = await run(TryonRequest(photo=photo, read=None, products=[TV55]))
    assert res.needs_wall_width and res.image is None and res.blurred_photo
    res = await run(TryonRequest(photo=photo, read=ImageRead(kind="room"), products=[TV55]))
    assert res.needs_wall_width and res.image is None
    res = await run(TryonRequest(photo=photo, read=_read(boxes, ["wall"]), products=[TV55]))  # wall box only: 0.4
    assert res.needs_wall_width and res.scale_confidence < scale.MIN_CONFIDENCE and res.image is None
    res2 = await run(TryonRequest(photo=photo, read=_read(boxes, ["wall"]), products=[TV55], wall_width_ft=11))
    assert not res2.needs_wall_width and res2.image and res2.error is None


async def test_cut_off_door_is_not_a_reference():
    ppc = 2.4
    photo, boxes, fy, cy = _room(ppc, floor_y=H + 40, door_x=60)  # floor below the frame: door cut off
    boxes["door"] = (boxes["door"][0], boxes["door"][1], boxes["door"][2], H)
    res = await run(TryonRequest(photo=photo, read=_read(boxes, ["wall", "door"]), products=[TV55]))
    assert res.scale_source != "door"


# ---------------------------------------------------------------- perspective


def _camera(u: float, z: float, n: float, yaw_deg: float):
    th = math.radians(yaw_deg)
    right = np.array([math.cos(th), 0.0, math.sin(th)])
    down = np.array([0.0, -1.0, 0.0])
    fwd = np.array([math.sin(th), 0.0, -math.cos(th)])
    c = np.array([u, z, n])
    f = FOCAL_FRAC * W

    def project(p):
        q = np.asarray(p, float) - c
        x, y, zz = q @ right, q @ down, q @ fwd
        return np.array([W / 2 + f * x / zz, H / 2 + f * y / zz])

    def backproject(px, plane_n: float):
        dvec = right * (px[0] - W / 2) / f + down * (px[1] - H / 2) / f + fwd
        t = (plane_n - c[2]) / dvec[2]
        return c + t * dvec

    return project, backproject


async def test_perspective_wall_quad_matches_the_homography():
    wall_w, wall_h = 400.0, 285.0
    project, backproject = _camera(110.0, 140.0, 300.0, 25.0)
    corners3 = [(0, wall_h, 0), (wall_w, wall_h, 0), (wall_w, 0, 0), (0, 0, 0)]
    quad = [project(p) for p in corners3]
    im = Image.new("RGB", (W, H), (236, 234, 230))
    d = ImageDraw.Draw(im)
    d.polygon([tuple(q) for q in quad], fill=WALL_RGB)
    d.polygon([tuple(quad[3]), tuple(quad[2]), (W * 3, H * 3), (-W * 2, H * 3)], fill=(168, 158, 144))
    fy, cy = project((wall_w / 2, 0, 0))[1], project((wall_w / 2, wall_h, 0))[1]
    read = ImageRead(
        kind="room",
        boxes=[Box(label="wall", box_2d=_norm((max(0, quad[0][0]), 0, min(W, quad[1][0]), H)))],
        frontal=False,
        wall_corners=[Point(x=round(q[0] / W * 1000), y=round(q[1] / H * 1000)) for q in quad],
        floor_y=round(fy / H * 1000),
        ceiling_y=round(cy / H * 1000),
    )
    res = await run(TryonRequest(photo=_jpeg(im), read=read, products=[TV55, TV65], wall_width_ft=wall_w / scale.FT_CM))
    assert res.error is None and res.image and res.perspective and res.scale_source == "wall_width"
    front = DEPTH_CM["tv"][1]
    for sku, (w_cm, h_cm) in ((TV55.sku, (123.0, 70.8)), (TV65.sku, (145.0, 83.5))):
        q = dict(res.quads)[sku]
        p = [backproject(c, front) for c in q]  # TL TR BR BL on the TV's front plane
        assert _within(np.linalg.norm(p[1] - p[0]), w_cm, 0.03) and _within(np.linalg.norm(p[2] - p[3]), w_cm, 0.03)
        assert _within(np.linalg.norm(p[0] - p[3]), h_cm, 0.03) and _within(np.linalg.norm(p[1] - p[2]), h_cm, 0.03)
        assert abs(p[0][1] - p[1][1]) < 1.5 and abs(p[0][0] - p[3][0]) < 1.5  # level and plumb on the wall
    p = [backproject(c, front) for c in dict(res.quads)[TV55.sku]]
    assert abs(np.mean([v[1] for v in p]) - 105) < 4  # centre 105 cm above the floor
    # the rendered frame really is at the quad: dark pixels just inside the left edge, wall just outside
    g = np.asarray(_img(res.image).convert("L"), dtype=np.float32)
    tl, bl = np.asarray(dict(res.quads)[TV55.sku][0]), np.asarray(dict(res.quads)[TV55.sku][3])
    mid = (tl + bl) / 2
    assert g[int(mid[1]), int(mid[0]) + 1] < 0.55 * WALL_LUM and g[int(mid[1]), int(mid[0]) - 8] > 0.75 * WALL_LUM


async def test_perspective_fixture_room_angle():
    res = await run(_req("room_angle", "room_angle", wall_width_ft=440 / scale.FT_CM))
    assert res.error is None and res.image and res.perspective and res.scale_source == "wall_width"
    q = np.asarray(dict(res.quads)[TV55.sku])
    left, right = q[3][1] - q[0][1], q[2][1] - q[1][1]
    assert left > 1.08 * right  # the nearer (left) edge is taller: the TV follows the receding wall
    assert res.ms < 3000


# ---------------------------------------------------------------- placement


async def test_tv_unit_anchor_and_stand_mount():
    ppc = 2.4
    photo, boxes, fy, cy = _room(ppc, door_x=40, unit=(700, 180, 45))
    read = _read(boxes, ["wall", "door", "tv_unit"])
    res = await run(TryonRequest(photo=photo, read=read, products=[TV55]))
    b = dict(res.planned)[TV55.sku]
    assert abs((b[0] + b[2]) / 2 - 700) <= 4  # centred on the TV unit
    unit_top = boxes["tv_unit"][1]
    assert _within((unit_top - b[3]) / ppc, 15, 0.25)  # wall mount: bottom edge 15 cm above the unit
    stand = TryonProduct(**{**TV55.__dict__, "mount": "stand"})
    res = await run(TryonRequest(photo=photo, read=read, products=[stand]))
    b = dict(res.planned)[TV55.sku]
    assert 0 <= (unit_top - b[3]) / ppc <= 10  # on its pedestal, on the unit top


async def test_sofa_centre_when_no_tv_unit():
    ppc = 2.4
    photo, boxes, fy, cy = _room(ppc, floor_y=700, door_x=40, sofa=(760, 180))
    boxes["sofa"] = (boxes["sofa"][0], fy + 40, boxes["sofa"][2], H)  # sofa in the foreground, facing the wall
    res = await run(TryonRequest(photo=photo, read=_read(boxes, ["wall", "door", "sofa"]), products=[TV55]))
    b = dict(res.planned)[TV55.sku]
    assert abs((b[0] + b[2]) / 2 - 760) <= 4


async def test_existing_tv_without_size_still_centres_there():
    ppc = 2.2
    photo, boxes, fy, cy = _room(ppc, door_x=40, old_tv=(640, 350, 32))
    res = await run(TryonRequest(photo=photo, read=_read(boxes, ["wall", "door", "existing_tv"]), products=[TV55]))
    assert res.scale_source == "door"
    b = dict(res.planned)[TV55.sku]
    assert abs((b[0] + b[2]) / 2 - 640) <= 4 and abs((b[1] + b[3]) / 2 - 350) <= 4


async def test_replacing_old_32_fixture_centres_on_it():
    read = _fixture_read("room_oldtv")
    res = await run(_req("room_oldtv", "room_oldtv", existing_tv_size_in=32))
    assert res.scale_source == "existing_tv" and res.error is None and res.image
    old = to_pixels(read, 1080, 810)["existing_tv"][0]
    b = dict(res.planned)[TV55.sku]
    assert abs((b[0] + b[2]) / 2 - (old[0] + old[2]) / 2) <= 4
    assert abs((b[1] + b[3]) / 2 - (old[1] + old[3]) / 2) <= 4
    assert b[0] < old[0] and b[2] > old[2] and b[1] < old[1] and b[3] > old[3]  # old set hidden behind the new


async def test_ac_and_wm_place():
    ac = TryonProduct(sku="AC-MER-15-5S", category="ac", width_mm=970, height_mm=300, label="1.5T", solid=True)
    ppc = 2.4
    photo, boxes, fy, cy = _room(ppc, floor_y=760)
    read = _read(boxes, ["wall"], floor_y=fy, ceiling_y=cy)
    res = await run(TryonRequest(photo=photo, read=read, products=[ac]))
    assert res.image and res.error is None
    q = np.asarray(dict(res.quads)[ac.sku])
    # top edge 30 cm below the ceiling, on the AC's front face (22 cm proud of the wall): project it with the
    # level camera behind this frontal photo (eye at the centre row, distance f / px-per-cm)
    f, back_front = FOCAL_FRAC * W, DEPTH_CM["ac"]
    eye_cm = (fy - H / 2) / ppc
    expected_y = H / 2 - f * (scale.WALL_HEIGHT_CM - 30 - eye_cm) / (f / ppc - back_front[1])
    assert abs(q[0][1] - expected_y) <= 3
    wm = TryonProduct(sku="WM-MER-8-FL", category="wm", width_mm=600, height_mm=850, label="8 kg", solid=True)
    for niche, fits in ((55, False), (65, True)):
        res = await run(TryonRequest(photo=photo, read=read, products=[wm], niche_width_cm=niche))
        assert res.image and res.error is None
        q = np.asarray(dict(res.quads)[wm.sku])
        assert q[3][1] >= fy - 2  # standing on the floor (its front edge is nearer the camera: lower)
        fb = dict(res.label_boxes)[f"fit:{wm.sku}"]
        c = np.asarray(_img(res.image).crop(fb), dtype=np.float32).reshape(-1, 3).mean(axis=0)
        assert (c[1] > c[0]) == fits  # green tick when it fits the niche, red cross when it does not
    res = await run(
        TryonRequest(
            photo=_photo("room_utility"),
            read=_fixture_read("room_utility"),
            products=[wm],
            wall_width_ft=220 / scale.FT_CM,
            niche_width_cm=62,
        )
    )
    assert res.image and res.error is None and res.perspective


# ---------------------------------------------------------------- labels


async def test_labels_never_overlap():
    long1 = TryonProduct(
        "TV-A", "tv", 1230, 708, '55" · Meru QLED Pro Ultra Slim Edition', True, dims_text="123 × 71 cm"
    )
    long2 = TryonProduct("TV-B", "tv", 1450, 835, '65" · Meru QLED Pro Ultra Slim', False, dims_text="145 × 84 cm")
    cases = [_req(), _req("room_angle", "room_angle", wall_width_ft=14.4)]
    for size in ((1080, 810), (720, 1280), (640, 480)):
        im = Image.new("RGB", size, WALL_RGB)
        cases.append(
            TryonRequest(
                photo=_jpeg(im),
                read=None,
                products=[long1, long2],
                wall_width_ft=9,
                distance_text="सोफ़ा से 10 ft · Sofa se 10 ft",
            )
        )
    for req in cases:
        res = await run(req)
        assert res.image and res.error is None
        im = _img(res.image)
        names = [n for n, _ in res.label_boxes]
        assert "footer" in names and any(n.startswith("label:") for n in names) and "scale_bar" in names
        for _, b in res.label_boxes:
            assert 0 <= b[0] and 0 <= b[1] and b[2] <= im.width and b[3] <= im.height
        for (na, a), (nb, b) in combinations(res.label_boxes, 2):
            overlap = min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
            assert not overlap, (na, a, nb, b)


# ---------------------------------------------------------------- fixtures, wealth-neutral, editor


async def test_two_tvs_door_scale():
    res = await run(_req())
    assert res.error is None and res.image and not res.needs_wall_width
    assert res.scale_source == "door" and res.scale_confidence == pytest.approx(0.75)
    im = _img(res.image)
    assert im.width == 1080
    boxes = dict(res.planned)
    b55, b65 = boxes["TV-MER-55-QL120"], boxes["TV-MER-65-QL120"]
    r = (b65[2] - b65[0]) / (b55[2] - b55[0])
    assert r == pytest.approx(1450 / 1230, rel=0.02)  # to scale against each other
    assert abs((b55[0] + b55[2]) - (b65[0] + b65[2])) <= 2  # same centre
    assert abs((b55[1] + b55[3]) - (b65[1] + b65[3])) <= 2
    wall = to_pixels(_fixture_read("room_living"), 1080, 810)["wall"][0]
    assert wall[0] <= b55[0] and b55[2] <= wall[2] and wall[1] <= b55[1] and b55[3] <= wall[3]
    # the fixture camera: 2.25 px/cm at the wall (a 210 cm door is 474 px tall)
    assert _within(res.px_per_cm, 2.25, 0.02)
    assert not res.verified  # no editor → composite


async def test_wall_width_scale_wins():
    res = await run(_req(wall_width_ft=440 / scale.FT_CM))
    assert res.scale_source == "wall_width" and res.scale_confidence == pytest.approx(0.9)
    assert res.px_per_cm == pytest.approx(2.25, rel=0.01)  # the 440 cm fixture wall at 2.25 px/cm


async def test_luxury_room_same_geometry_as_living():
    a = await run(_req("room_living", "room_living"))
    b = await run(_req("room_luxury", "room_luxury"))
    assert a.planned == b.planned and a.quads == b.quads
    assert a.px_per_cm == b.px_per_cm and a.scale_confidence == b.scale_confidence


class _Editor:
    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    async def edit_image(self, data: bytes, prompt: str):
        self.calls += 1
        return self.fn(data)


async def test_editor_none_keeps_composite():
    ed = _Editor(lambda d: None)
    res = await run(_req(), editor=ed, harmonise=True)
    assert ed.calls == 1 and res.image and not res.harmonised and not res.verified and res.error is None


async def test_editor_identical_is_verified():
    ed = _Editor(lambda d: d)
    res = await run(_req(), editor=ed, harmonise=True)
    assert res.harmonised and res.verified
    res = await run(_req("room_angle", "room_angle", wall_width_ft=14.4), editor=ed, harmonise=True)
    assert res.harmonised and res.verified  # perspective quad passes the edge check


def _shifted(data: bytes) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    out = Image.new("RGB", im.size, (226, 222, 210))
    out.paste(im, (120, 0))  # moves everything (incl. the TV) sideways
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=92)
    return buf.getvalue()


async def test_editor_moving_tv_is_rejected():
    res = await run(_req(), editor=_Editor(_shifted), harmonise=True)
    assert res.image and not res.verified and not res.harmonised


def test_verify_unit():
    base = Image.new("RGB", (600, 400), (220, 220, 210))
    d = ImageDraw.Draw(base)
    box = (200, 120, 400, 240)
    d.rectangle(box, fill=(17, 17, 17))
    d.rectangle((206, 126, 394, 234), fill=(150, 200, 240))
    assert verify.check(base, base.copy(), box)
    moved = Image.new("RGB", (600, 400), (220, 220, 210))
    ImageDraw.Draw(moved).rectangle((60, 120, 260, 240), fill=(17, 17, 17))
    assert not verify.check(base, moved, box)
    assert not verify.check(base, base.resize((640, 400)), box)
    changed = base.copy()
    ImageDraw.Draw(changed).rectangle((0, 300, 600, 400), fill=(20, 90, 20))
    assert not verify.check(base, changed, box)


def test_verify_thin_bezel_perspective_quad():
    base = Image.new("RGB", (600, 400), (220, 214, 200))
    quad = [(180, 110), (420, 130), (420, 250), (180, 270)]
    ImageDraw.Draw(base).polygon(quad, fill=(30, 30, 34))
    inner = [(183, 113), (417, 133), (417, 247), (183, 267)]
    ImageDraw.Draw(base).polygon(inner, fill=(120, 60, 200))
    box = (180, 110, 420, 270)
    assert verify.check(base, base.copy(), box, quad)
    shifted = Image.new("RGB", (600, 400), (220, 214, 200))
    shifted.paste(base.crop((0, 0, 560, 400)), (40, 0))
    assert not verify.check(base, shifted, box, quad)


async def test_people_blurred():
    read = _fixture_read("room_living").model_copy(deep=True)
    read.boxes.append(type(read.boxes[0])(label="person", box_2d=[300, 300, 700, 450]))
    res = await run(TryonRequest(photo=_photo("room_living"), read=read, products=[TV55]))
    assert res.blurred_photo and res.image
    raw = np.asarray(_img(_photo("room_living")).resize((1080, 810)), dtype=np.float32)
    blurred = np.asarray(_img(res.blurred_photo), dtype=np.float32)
    region = (slice(260, 540), slice(330, 480))
    assert np.abs(np.diff(blurred[region], axis=1)).mean() < np.abs(np.diff(raw[region], axis=1)).mean()


async def test_never_raises_on_garbage():
    res = await run(TryonRequest(photo=b"not an image", read=None, products=[TV55], wall_width_ft=10))
    assert res.error and res.image is None
    res = await run(TryonRequest(photo=_photo("room_living"), read=None, products=[], wall_width_ft=10))
    assert res.error and res.image is None
    bad = ImageRead(kind="room", wall_corners=[Point(x=500, y=500)] * 4, floor_y=900, ceiling_y=950)
    res = await run(TryonRequest(photo=_photo("room_living"), read=bad, products=[TV55], wall_width_ft=10))
    assert res.error is None and res.image  # degenerate corners/lines ignored, frame fallback


async def test_tv_without_dimensions_uses_its_size():
    nodims = TryonProduct("TV-X", "tv", None, None, '55" · Meru QLED Pro', True)  # type: ignore[arg-type]
    res = await run(TryonRequest(photo=_photo("room_living"), read=_fixture_read("room_living"), products=[nodims]))
    assert res.error is None and res.image
    b = dict(res.planned)["TV-X"]
    assert _within(b[2] - b[0], scale.tv_width_cm(55) * 2.25 * _depth_factor(2.25), 0.05)


async def test_fast():
    for req in (
        _req(),
        _req("room_angle", "room_angle", wall_width_ft=14.4),
        _req("room_oldtv", "room_oldtv", existing_tv_size_in=32),
    ):
        t = time.perf_counter()
        res = await run(req)
        assert res.image and time.perf_counter() - t < 3


# ---------- where the TV really goes (user, 6 Nov 2026: "realistically placed on the customer wall ... best fit") ----
def _plain_room(*boxes: tuple[str, list[int]]) -> ImageRead:
    """A frontal wall filling the photo, with the given boxes ([ymin, xmin, ymax, xmax], 0-1000)."""
    return ImageRead(kind="room", frontal=True, floor_y=900,
                     boxes=[Box(label="wall", box_2d=[0, 0, 900, 1000])] + [Box(label=lb, box_2d=b) for lb, b in boxes])


def _x(res, sku="TV-MER-55-QL120") -> float:
    """The TV's centre as a share of the photo width."""
    box = dict(res.planned)[sku]
    return (box[0] + box[2]) / 2 / 1080


async def test_tv_goes_on_the_spot_the_reading_chose_centred_on_its_unit():
    """A TV unit at the edge of the photo (before: ignored, the TV floated in the middle of the wall); the reading's
    spot is over it, a little off its centre: the TV is centred on the unit, just above it."""
    read = _plain_room(("tv_unit", [700, 0, 900, 280]), ("tv_spot", [420, 30, 660, 300]))
    res = await run(TryonRequest(photo=_photo("room_living"), read=read, products=[TV55], wall_width_ft=16))
    assert res.error is None and res.placement["TV-MER-55-QL120"]["anchor"] == "spot_unit"
    assert abs(_x(res) - 0.14) < 0.01  # the unit's centre, not the spot's (0.165) nor the wall's (0.5)
    box = dict(res.planned)["TV-MER-55-QL120"]
    assert 0.6 * 810 < box[3] < 0.7 * 810  # just above the unit top (at 0.7 of the photo)
    before = await run(TryonRequest(photo=_photo("room_living"), read=_plain_room(("tv_unit", [700, 0, 900, 280])),
                                    products=[TV55], wall_width_ft=16))
    assert before.placement["TV-MER-55-QL120"]["anchor"] == "wall_centre"  # without the spot: the old rule


async def test_tv_spot_on_a_bare_wall_and_a_wrong_spot_over_a_window():
    read = _plain_room(("tv_spot", [380, 560, 640, 860]))
    res = await run(TryonRequest(photo=_photo("room_living"), read=read, products=[TV55], wall_width_ft=12))
    assert res.placement["TV-MER-55-QL120"]["anchor"] == "spot" and abs(_x(res) - 0.71) < 0.02
    read = _plain_room(("window", [200, 600, 700, 1000]), ("tv_spot", [380, 640, 640, 940]))  # the spot is wrong
    res = await run(TryonRequest(photo=_photo("room_living"), read=read, products=[TV55], wall_width_ft=12))
    box = dict(res.planned)["TV-MER-55-QL120"]
    assert box[2] < 0.6 * 1080 and abs(_x(res) - 0.29) < 0.03  # off the window: centred on the clear wall (0–0.6)


async def test_tv_is_centred_on_the_clear_wall_not_pushed_against_a_window():
    """Nothing to stand on: the middle of the wall; if that would cover a window, the middle of the clear stretch
    of wall beside it (before: shifted just enough to touch the window frame)."""
    res = await run(TryonRequest(photo=_photo("room_living"), read=_plain_room(), products=[TV55], wall_width_ft=12))
    assert res.placement["TV-MER-55-QL120"]["anchor"] == "wall_centre" and abs(_x(res) - 0.5) < 0.01
    read = _plain_room(("window", [150, 480, 650, 760]))
    res = await run(TryonRequest(photo=_photo("room_living"), read=read, products=[TV55], wall_width_ft=12))
    box = dict(res.planned)["TV-MER-55-QL120"]
    win_left = 0.48 * 1080
    assert box[2] < win_left and abs(_x(res) - 0.235) < 0.03  # the clear stretch is 0–0.47 of the wall
    assert win_left - box[2] > 0.06 * 1080  # with room to spare, not flush against the window frame (8 cm)
