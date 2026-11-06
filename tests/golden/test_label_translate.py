"""Translate from a shelf label, on the real catalogue (contest brief: "The associate sends a photo of a label. Store
IQ gives a line to say for this home, for example: '8 kg: two pairs of jeans and two bedsheets in one wash.'"; user,
6 Nov 2026: "all from the real data ... no mocks or hallucination").

The products are the Jio backend's own (tests/fixtures/jio/products.json, recorded from the Reliance Digital sales
channel). The photo read is given as the vision model returns it (brand, model text, specs), so what is tested is
the match to the catalogue and the line, both from the data."""

from __future__ import annotations

import re

import pytest

from chanakya.ai.schemas import ImageRead, Spec
from chanakya.truth import jio_sync
from chanakya.truth.facts import _codes, _label_specs
from chanakya.truth.jio import JioClient
from tests.helpers import check_floor
from tests.unit import test_jio as TJ

SAMSUNG_65 = "491666469"  # Samsung 6.5 Kg Top Load Fully Automatic Washing Machine ..., WA65N4261SS
SAMSUNG_75 = "491538905"  # Samsung 7.5 Kg Top Loading Semi-Automatic Washing Machine ..., WT75M3200HB/TL
SAMSUNG_393 = "491604593"  # Samsung 393 L 3 Star Inverter Frost Free Double Door Refrigerator (RT39R545ESL ...)
SONY_43 = "491297651"  # Sony 108 cm (43 inch) Ultra HD 4K LED Smart TV, KD-43X7002E


@pytest.fixture
async def live(tmp_path, monkeypatch):
    monkeypatch.setattr(JioClient, "transport", TJ.backend())
    h = await TJ.live(tmp_path)
    await jio_sync.sync(h.rt.settings, h.rt.facts)
    yield h
    await h.close()


async def scan(h, read: ImageRead):
    async def reads(img, filename=None, caption=None, hint=None):
        return read

    h.rt.ai.read_image = reads
    out = await h.photo("room_living.jpg")
    check_floor(out)
    return out


def bolo(out) -> str:
    m = re.search(r'\*Bolo\*: "(.*?)"', out[0].body or "")
    return m.group(1) if m else ""


def test_model_numbers_and_sizes_are_read_off_the_label():
    assert {"kd43x7002e", "wa65n4261ss"} <= _codes("Sony KD-43X7002E · Samsung WA65N4261SS 108 cm")
    assert "108" not in _codes("108 cm (43 inch)")  # a number alone is not a model
    r = ImageRead(kind="label", specs=[Spec(name="capacity", value="7.5", unit="kg")])
    assert _label_specs(r, "") == {"capacity_kg": 7.5}
    assert _label_specs(ImageRead(kind="label", size_in=43), "393 L") == {"size_in": 43.0, "capacity_l": 393.0}


async def test_a_label_scanned_first_gives_the_spec_in_daily_life_words(live):
    """Nothing known of the home yet: the line is the label's spec in daily-life words, true for any home, and the
    price is the backend's own."""
    await live.tap("g:new")
    out = await scan(live, ImageRead(kind="label", brand="Samsung", model_text="WA65N4261SS", category="wm"))
    assert out[0].card is not None and out[0].card.template == "product"
    assert bolo(out) == "6.5 kg matlab do logon ke roz ke kapde ek baar mein."
    assert "Label ki spec" in out[0].body and "₹21,990" in out[0].body  # the selling price in the data
    s = await live.rt.svc.sessions.by_code("919000000001", "C1")
    assert s.extra["label_sku"] == SAMSUNG_65 and s.home.category == "wm"


async def test_the_printed_name_finds_the_same_product(live):
    await live.tap("g:new")
    read = ImageRead(kind="label", brand="Samsung", model_text="6.5 Kg Top Load Fully Automatic Washing Machine")
    await scan(live, read)
    s = await live.rt.svc.sessions.by_code("919000000001", "C1")
    assert s.extra["label_sku"] == SAMSUNG_65


async def test_a_label_for_this_home(live):
    """The home told first (five people, daily washing): the line is for this home, and says why."""
    await live.tap("g:new")
    await live.pick("s:C1:cat:wm")
    await live.say("5 log hain, roz kapde dhulte hain")
    out = await scan(live, ImageRead(kind="label", brand="Samsung", model_text="WT75M3200HB/TL", category="wm",
                                     specs=[Spec(name="capacity", value="7.5", unit="kg")]))
    assert bolo(out).startswith("7.5 kg matlab ek baar mein 1 bedsheet aur 6 shirt.")
    assert "5" in out[0].body.split("\n")[1]  # Kyun: the home fact it answers (five people)


async def test_a_line_never_has_a_hole(live):
    """The fridge has no power-cut hours in the data: that line is never said ('stays cold for  hours')."""
    await live.tap("g:new")
    out = await scan(live, ImageRead(kind="label", brand="Samsung", model_text="RT39R545ESL", category="fridge"))
    line = bolo(out)
    assert line and "freezer" in line.lower() and not re.search(r"\s(h|hours?)\s+tak", line)


async def test_a_tv_label_uses_the_products_own_highlight(live):
    await live.tap("g:new")
    out = await scan(live, ImageRead(kind="label", brand="Sony", model_text="KD-43X7002E", category="tv", size_in=43))
    sony = live.rt.facts.product_sync(SONY_43)
    feats = " ".join(f["en"] for f in sony.info["features"])
    assert "X-Reality PRO" in bolo(out) and "X-Reality PRO" in feats  # quoted from the backend's own highlights
    assert not bolo(out).startswith("Saath hi")


async def test_a_label_that_disagrees_with_the_data_is_not_guessed(live):
    """The printed name says 6.5 kg Samsung top load but the label says 8 kg: no product in the data is both."""
    await live.tap("g:new")
    read = ImageRead(kind="label", brand="Samsung", model_text="Top Load Fully Automatic Washing Machine",
                     category="wm", specs=[Spec(name="capacity", value="8", unit="kg")])
    out = await scan(live, read)
    s = await live.rt.svc.sessions.by_code("919000000001", "C1")
    assert s.extra.get("label_sku") is None
    assert out[0].kind == "list" or "Label saaf nahi" in (out[0].body or "")
