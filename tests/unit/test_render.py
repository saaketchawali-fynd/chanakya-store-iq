"""Card renderer: every template × hi/mr/en through Playwright, plus the Pillow fallback."""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from chanakya.core.contracts import Card
from chanakya.render.renderer import TEMPLATE_NAMES, Renderer, render_pillow, sample_data, to_data_uri

SAMPLE_DIR = os.environ.get("CARD_SAMPLE_DIR")  # optional: save rendered samples for eyeballing


def _check_jpeg(b: bytes) -> tuple[int, int]:
    assert b[:3] == b"\xff\xd8\xff", "not a JPEG"
    im = Image.open(io.BytesIO(b))
    assert im.format == "JPEG"
    return im.size


@pytest.fixture(scope="module")
async def renderer():
    r = Renderer()
    await r.start(warm_up=True)
    if not r.ready:
        pytest.skip("Chromium not available")
    yield r
    await r.stop()


@pytest.mark.parametrize("lang", ["hi", "mr", "en"])
@pytest.mark.parametrize("template", TEMPLATE_NAMES)
async def test_render_template(renderer, template, lang):
    card = Card(template=template, lang=lang, data=sample_data(template, lang))
    t0 = time.perf_counter()
    b = await renderer.render(card)
    ms = (time.perf_counter() - t0) * 1000
    w, h = _check_jpeg(b)
    assert w == 1080
    assert h <= 1350
    assert ms < 1500, f"{template}/{lang} took {ms:.0f} ms"
    if SAMPLE_DIR:
        Path(SAMPLE_DIR).mkdir(parents=True, exist_ok=True)
        (Path(SAMPLE_DIR) / f"{template}_{lang}.jpg").write_bytes(b)


async def test_empty_data_does_not_crash(renderer):
    for name in TEMPLATE_NAMES:
        b = await renderer.render(Card(template=name, lang="en", data={}))
        w, _ = _check_jpeg(b)
        assert w == 1080


async def test_very_long_content_is_capped(renderer):
    data = sample_data("home", "hi")
    data["rows"] = [{"label": f"Row {i}", "value": "x" * 40} for i in range(60)]
    w, h = _check_jpeg(await renderer.render(Card(template="home", lang="hi", data=data)))
    assert (w, h) == (1080, 1350)


def test_html_escapes_and_inlines_images():
    r = Renderer()
    data = sample_data("benefit", "en")
    data["product_title"] = "<script>x</script>"
    html = r.html(Card(template="benefit", lang="en", data=data))
    assert "<script>x</script>" not in html
    assert "data:image/png;base64," in html  # products/<sku>.png path inlined


def test_to_data_uri_variants():
    assert to_data_uri(None) is None
    assert to_data_uri("products/DOES-NOT-EXIST.png") is None
    assert to_data_uri("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"
    png = io.BytesIO()
    Image.new("RGB", (4, 4)).save(png, format="PNG")
    assert to_data_uri(png.getvalue()).startswith("data:image/png;base64,")


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_pillow_fallback(template):
    for lang in ("hi", "mr", "en"):
        w, h = _check_jpeg(render_pillow(Card(template=template, lang=lang, data=sample_data(template, lang))))
        assert w == 1080 and h <= 1350
    _check_jpeg(render_pillow(Card(template=template, lang="hi", data={})))


async def test_not_started_renderer_uses_fallback():
    r = Renderer()  # never started → ready False
    w, _ = _check_jpeg(await r.render(Card(template="score", lang="hi", data=sample_data("score"))))
    assert w == 1080
