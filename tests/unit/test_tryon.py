"""Ghar Mein Dekho: geometry, scale, verify, wealth-neutrality, never-raise."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from chanakya.ai.schemas import ImageRead
from chanakya.tryon import TryonProduct, TryonRequest, run, scale, verify
from chanakya.tryon.read_room import to_pixels

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "data/fixtures/images"
READ = ROOT / "data/fixtures/image_read"


def _read(name: str) -> ImageRead:
    return ImageRead.model_validate(json.loads((READ / f"{name}.json").read_text()))


def _photo(name: str) -> bytes:
    return (IMG / f"{name}.jpg").read_bytes()


TV55 = TryonProduct(
    sku="TV-MER-55-QL120", category="tv", width_mm=1230, height_mm=708, label='55" · Meru QLED Pro', solid=True
)
TV65 = TryonProduct(sku="TV-MER-65-QL120", category="tv", width_mm=1450, height_mm=835, label='65"', solid=False)


def _req(name="room_living", read="room_living", **kw) -> TryonRequest:
    return TryonRequest(
        photo=_photo(name),
        read=_read(read) if read else None,
        products=[TV55, TV65],
        distance_text="Sofa se 10 ft",
        **kw,
    )


def _img(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b))


async def test_two_tvs_door_scale():
    res = await run(_req())
    assert res.error is None and res.image and not res.needs_wall_width
    assert res.scale_confidence == pytest.approx(0.7)
    im = _img(res.image)
    assert im.width == 1080
    boxes = dict(res.planned)
    b55, b65 = boxes["TV-MER-55-QL120"], boxes["TV-MER-65-QL120"]
    # to scale: width ratio follows the real widths
    r = (b65[2] - b65[0]) / (b55[2] - b55[0])
    assert r == pytest.approx(1450 / 1230, rel=0.02)
    # same centre
    assert abs((b55[0] + b55[2]) - (b65[0] + b65[2])) <= 2
    assert abs((b55[1] + b55[3]) - (b65[1] + b65[3])) <= 2
    # inside the wall box
    wall = to_pixels(_read("room_living"), 1080, 810)["wall"][0]
    assert wall[0] <= b55[0] and b55[2] <= wall[2] and wall[1] <= b55[1] and b55[3] <= wall[3]
    assert not res.verified  # no editor → composite


async def test_wall_width_scale_wins():
    res = await run(_req(wall_width_ft=8))
    assert res.scale_confidence == pytest.approx(0.9)
    wall = to_pixels(_read("room_living"), 1080, 810)["wall"][0]
    assert res.px_per_cm == pytest.approx((wall[2] - wall[0]) / (8 * 30.48))
    b55 = dict(res.planned)["TV-MER-55-QL120"]
    assert (b55[2] - b55[0]) == pytest.approx(123.0 * res.px_per_cm, abs=2)


async def test_default_geometry_asks_wall_width_then_works():
    res = await run(_req(read=None))
    assert res.needs_wall_width and res.image is None and res.blurred_photo
    assert res.scale_confidence < scale.MIN_CONFIDENCE
    res2 = await run(_req(read=None, wall_width_ft=10))
    assert not res2.needs_wall_width and res2.image and res2.error is None


async def test_luxury_room_same_geometry_as_living():
    a = await run(_req("room_living", "room_living"))
    b = await run(_req("room_luxury", "room_luxury"))
    assert a.planned == b.planned
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


async def test_people_blurred():
    read = _read("room_living").model_copy(deep=True)
    read.boxes.append(type(read.boxes[0])(label="person", box_2d=[300, 300, 700, 450]))
    res = await run(TryonRequest(photo=_photo("room_living"), read=read, products=[TV55]))
    assert res.blurred_photo and res.image


async def test_never_raises_on_garbage():
    res = await run(TryonRequest(photo=b"not an image", read=None, products=[TV55], wall_width_ft=10))
    assert res.error and res.image is None


async def test_ac_and_wm_place():
    ac = TryonProduct(sku="AC-MER-15-5S", category="ac", width_mm=970, height_mm=300, label="1.5T", solid=True)
    res = await run(TryonRequest(photo=_photo("room_living"), read=_read("room_living"), products=[ac]))
    assert res.image and res.error is None
    wm = TryonProduct(sku="WM-MER-8-FL", category="wm", width_mm=600, height_mm=850, label="8 kg", solid=True)
    res = await run(
        TryonRequest(photo=_photo("room_living"), read=_read("room_living"), products=[wm], niche_width_cm=55)
    )
    assert res.image and res.error is None


async def test_fast():
    t = time.perf_counter()
    res = await run(_req())
    assert res.image and time.perf_counter() - t < 5
