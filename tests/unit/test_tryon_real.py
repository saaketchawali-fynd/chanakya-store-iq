"""Try-on: the TV centred where it really goes, and the real product drawn from its catalogue photo.

- A side table or an armchair at the edge of the photo never pulls the TV off centre; a real (wide, low) TV unit
  holds it, even off centre.
- The product's own photo (plain studio background) is cut out and drawn at true size; a blank TV screen is
  switched on; a photo that does not cut out cleanly falls back to the drawn product."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw

from chanakya.tryon import TryonProduct, TryonRequest, run
from chanakya.tryon.photo import cutout
from chanakya.tryon.scale import tv_height_cm, tv_width_cm
from tests.unit.test_tryon import W, _img, _read, _room

PPC = 2.6  # px per cm in the synthetic rooms


def tv(inches: int, solid: bool = True, photo: bytes | None = None) -> TryonProduct:
    return TryonProduct(f"TV{inches}", "tv", tv_width_cm(inches) * 10, tv_height_cm(inches) * 10, f'{inches}"', solid,
                        photo=photo)


def product_photo(bezel=(200, 30, 30), screen=(12, 12, 14), with_stand=True, size=(1000, 1000)) -> bytes:
    """A catalogue-style TV photo: white studio background, a 16:9 panel with a coloured bezel, two feet."""
    im = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(im)
    pw = 800
    ph = round(pw * tv_height_cm(55) / tv_width_cm(55))
    x0, y0 = 100, 250
    d.rectangle((x0, y0, x0 + pw, y0 + ph), fill=bezel)
    d.rectangle((x0 + 10, y0 + 10, x0 + pw - 10, y0 + ph - 18), fill=screen)
    if with_stand:
        d.rectangle((x0 + 120, y0 + ph, x0 + 140, y0 + ph + 60), fill=(40, 40, 40))
        d.rectangle((x0 + pw - 140, y0 + ph, x0 + pw - 120, y0 + ph + 60), fill=(40, 40, 40))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return b.getvalue()


async def centre_x(**room) -> float:
    photo, boxes, floor_y, ceil_y = _room(PPC, **room)
    res = await run(TryonRequest(photo=photo, read=_read(boxes, floor_y=floor_y, ceiling_y=ceil_y),
                                 products=[tv(55)], wall_width_ft=W / PPC / 30.48))
    assert res.image, res.error
    box = res.planned[0][1]
    return (box[0] + box[2]) / 2


async def test_side_table_does_not_pull_the_tv_off_centre():
    x = await centre_x(unit=(160, 45, 60))  # a 45 cm side table at the left
    assert abs(x - W / 2) < 0.05 * W


async def test_armchair_at_the_edge_does_not_pull_the_tv():
    x = await centre_x(sofa=(W - 90, 80))  # an 80 cm armchair at the right edge
    assert abs(x - W / 2) < 0.05 * W


async def test_a_real_tv_unit_holds_the_tv_even_off_centre():
    x = await centre_x(unit=(380, 160, 50))  # a 160 cm unit left of centre
    assert abs(x - 380) < 0.04 * W


def test_cutout_takes_the_panel_and_switches_the_screen_on():
    asp = tv_width_cm(55) / tv_height_cm(55)
    c = cutout(product_photo(), "tv", asp)
    assert c is not None and c.screen is not None  # blank screen: lit with a picture
    pw, ph = c.panel[2] - c.panel[0], c.panel[3] - c.panel[1]
    assert abs(pw / ph - asp) < 0.06 and c.rgba.size == (pw, ph)  # the feet are not part of a wall-mounted TV
    stand = cutout(product_photo(), "tv", asp, keep_stand=True)
    assert stand is not None and stand.rgba.size[1] > ph  # on a TV unit it keeps its own feet
    lit = cutout(product_photo(screen=(40, 160, 220)), "tv", asp)
    assert lit is not None and lit.screen is None  # a picture already on the screen stays


def test_unusable_photos_are_refused():
    asp = tv_width_cm(55) / tv_height_cm(55)
    noisy = Image.fromarray((np.random.default_rng(1).random((600, 600, 3)) * 255).astype(np.uint8))
    b = io.BytesIO()
    noisy.save(b, "JPEG")
    assert cutout(b.getvalue(), "tv", asp) is None  # a lifestyle photo: no plain background
    assert cutout(product_photo(), "tv", asp * 1.6) is None  # the wrong shape for this product's true size
    assert cutout(b"not an image", "tv", asp) is None


async def test_the_real_product_is_drawn_from_its_photo():
    photo, boxes, floor_y, ceil_y = _room(PPC, unit=(W / 2, 160, 50))
    read = _read(boxes, floor_y=floor_y, ceiling_y=ceil_y)
    res = await run(TryonRequest(photo=photo, read=read, products=[tv(55, photo=product_photo())],
                                 wall_width_ft=W / PPC / 30.48))
    im = np.asarray(_img(res.image), dtype=np.float32)
    x0, y0, x1, y1 = res.planned[0][1]
    ring = np.concatenate([im[y0 + 2, x0 + 6:x1 - 6], im[y1 - 3, x0 + 6:x1 - 6]])
    assert ((ring[:, 0] > ring[:, 1] + 40) & (ring[:, 0] > 90)).mean() > 0.5  # the photo's own red bezel
    centre = im[(y0 + y1) // 2 - 20:(y0 + y1) // 2 + 20, (x0 + x1) // 2 - 40:(x0 + x1) // 2 + 40]
    assert centre.std() > 12  # the blank screen is switched on (a picture, not flat black)
    bad = await run(TryonRequest(photo=photo, read=read, products=[tv(55, photo=b"junk")], wall_width_ft=W / PPC / 30.48))
    assert bad.image and not bad.error  # an unusable photo: the drawn TV, never a failure


def test_placeholder_picture_is_not_the_product():
    from chanakya.truth.jio_map import map_product
    from tests.unit.test_jio import item

    it = dict(item("Panasonic 584 litres"))
    assert "/l2_category/" in it["medias"][0]["url"]
    assert map_product(it, "fridge")["image"] is None  # the generic icon sheet is never shown as the product


async def test_a_unit_at_the_far_edge_does_not_pull_the_tv():
    """4 Nov 2026 on the phone: the TV again sat off centre. A TV unit (or an 'existing TV') anchors the TV only
    when it is near the middle of the wall; at the edge of the photo, the TV goes at the wall's centre."""
    x = await centre_x(unit=(W - 110, 160, 50))  # a wide unit pushed to the right edge
    assert abs(x - W / 2) < 0.05 * W


async def test_a_picture_frame_mistaken_for_a_tv_is_ignored():
    photo, boxes, floor_y, ceil_y = _room(PPC)
    boxes["existing_tv"] = (90, 200, 150, 240)  # tiny and at the edge: a frame, not a TV
    res = await run(TryonRequest(photo=photo, read=_read(boxes, floor_y=floor_y, ceiling_y=ceil_y),
                                 products=[tv(55)], wall_width_ft=W / PPC / 30.48))
    box = res.planned[0][1]
    assert abs((box[0] + box[2]) / 2 - W / 2) < 0.05 * W
    assert res.placement[next(iter(res.placement))]["anchor"] == "wall_centre"
