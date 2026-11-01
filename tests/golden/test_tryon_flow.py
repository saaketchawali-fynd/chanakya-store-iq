"""'Ghar mein dekho' decision flow: photo first (two answers), then a ready-made menu of what to show —
best pick, size compare, all options one by one, several product types together — each picture < 5 s."""

from __future__ import annotations

import time

from chanakya.config import ROOT
from tests.helpers import button_ids, check_floor

LABELS = ROOT / "data" / "labels"


def rows(msg):
    return [r.id for s in msg.sections for r in s.rows]


async def to_options_without_photo(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:hi")
    await h.voice("assess_1")
    await h.tap("s:C1:slot:trigger:new_home")
    await h.tap("s:C1:photo_no")  # no photo during the questions
    await h.tap("s:C1:readback_ok")
    await h.tap("s:C1:options")


async def test_ghar_mein_dekho_asks_photo_then_draws(h):
    await to_options_without_photo(h)
    out = await h.tap("s:C1:tryon")
    assert button_ids(out) == ["s:C1:photo_yes", "s:C1:photo_no"]  # only the photo question
    await h.tap("s:C1:photo_yes")
    out = await h.photo("room_living.jpg")
    assert out[0].kind == "text" and not out[0].buttons  # one short note while the wall is read
    imgs = [x for x in out if x.image_bytes]
    assert len(imgs) == 1 and "s:C1:tmenu" in button_ids(imgs)  # then the picture; more choices one tap away
    out = await h.tap("s:C1:tmenu")
    r = rows(out[0])
    assert r[0] == "s:C1:tmode:best" and "s:C1:tmode:size" in r and "s:C1:tmode:all" in r


async def test_photo_during_questions_never_interrupts(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.voice("assess_1")
    await h.tap("s:C1:slot:trigger:new_home")
    await h.tap("s:C1:photo_yes")
    out = await h.photo("room_living.jpg")
    assert len(out) == 1 and not out[0].image_bytes and out[0].footer  # the next step + 'Photo saved'
    s = await h.rt.svc.sessions.by_code("919000000001", "C1")
    assert s.extra.get("room_read")  # read in the background: the picture is instant at the options


async def test_size_compare_one_picture_fast(h):
    await to_options_without_photo(h)
    await h.tap("s:C1:tryon")
    await h.tap("s:C1:photo_yes")
    await h.photo("room_living.jpg")
    t0 = time.perf_counter()
    out = await h.pick("s:C1:tmode:size")
    assert time.perf_counter() - t0 < 5
    check_floor(out)
    imgs = [m for m in out if m.image_bytes]
    assert len(imgs) == 1 and "55" in imgs[0].body and "65" in imgs[0].body
    assert button_ids(imgs)[:2] == ["s:C1:tpick:TV-MER-55-QL120", "s:C1:tpick:TV-MER-65-QL120"]
    assert "s:C1:tmenu" in button_ids(imgs)


async def test_all_options_one_by_one(h):
    await to_options_without_photo(h)
    await h.tap("s:C1:tryon")
    await h.tap("s:C1:photo_yes")
    await h.photo("room_living.jpg")
    out = await h.pick("s:C1:tmode:all")
    check_floor(out)
    imgs = [m for m in out if m.image_bytes]
    assert len(imgs) == 3
    picks = [b.id for m in imgs for b in m.buttons if ":tpick:" in b.id]
    assert picks == ["s:C1:tpick:TV-ARK-55-4K60", "s:C1:tpick:TV-MER-55-QL120", "s:C1:tpick:TV-MER-65-QL120"]


async def test_tv_and_ac_together(h):
    await to_options_without_photo(h)
    await h.photo(LABELS / "AC-MER-15-5S.jpg")  # the customer also likes an AC (label scanned)
    await h.tap("s:C1:tryon")
    await h.tap("s:C1:photo_yes")
    await h.photo("room_living.jpg")
    out = await h.tap("s:C1:tmenu")
    assert "s:C1:tmode:combo" in rows(out[0])
    out = await h.pick("s:C1:tmode:combo")
    check_floor(out)
    imgs = [m for m in out if m.image_bytes]
    assert len(imgs) == 1 and button_ids(imgs)[0] == "s:C1:tpick:TV-MER-55-QL120"
    s = await h.rt.svc.sessions.by_code("919000000001", "C1")
    assert set(s.extra["tryon_skus"]) == {"TV-MER-55-QL120", "AC-MER-15-5S"}
