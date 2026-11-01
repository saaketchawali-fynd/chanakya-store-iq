"""Card renderer: every template × hi/mr/en through Playwright, the Pillow fallback, and the image scripts."""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from PIL import Image

from chanakya.core.contracts import Card
from chanakya.render.renderer import (
    TEMPLATE_NAMES,
    Renderer,
    _cost_rows_from_text,
    _strip_label,
    dv_class,
    frac,
    pct,
    render_pillow,
    sample_data,
    stock_icon,
    to_data_uri,
)

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = os.environ.get("CARD_SAMPLE_DIR")  # optional: save rendered samples for eyeballing
CATEGORIES = ("tv", "ac", "wm", "laptop", "soundbar")


def _card(template: str, lang: str, data: dict) -> Card:
    # model_construct: product/compare may not be in the Card.template Literal yet (owner updates contracts.py)
    return Card.model_construct(template=template, lang=lang, data=data)


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


def test_catalog_is_a_template():
    for name in ("catalog", "product", "compare"):
        assert name in TEMPLATE_NAMES
        assert (ROOT / "chanakya" / "render" / "templates" / f"{name}.html").is_file()
    assert "catalog" in TEMPLATE_NAMES
    assert (ROOT / "chanakya" / "render" / "templates" / "catalog.html").is_file()
    assert sample_data("catalog", "hi")["items"]


@pytest.mark.parametrize("lang", ["hi", "mr", "en"])
@pytest.mark.parametrize("template", TEMPLATE_NAMES)
async def test_render_template(renderer, template, lang):
    card = _card(template=template, lang=lang, data=sample_data(template, lang))
    renderer.html(card)  # template errors must surface here, not hide behind the Pillow fallback
    t0 = time.perf_counter()
    b = await renderer._render_html(card)
    ms = (time.perf_counter() - t0) * 1000
    w, h = _check_jpeg(b)
    assert w == 1080
    assert 400 <= h <= 1350
    assert ms < 1500, f"{template}/{lang} took {ms:.0f} ms"
    if SAMPLE_DIR:
        Path(SAMPLE_DIR).mkdir(parents=True, exist_ok=True)
        (Path(SAMPLE_DIR) / f"{template}_{lang}.jpg").write_bytes(b)


async def test_empty_data_does_not_crash(renderer):
    for name in TEMPLATE_NAMES:
        renderer.html(_card(template=name, lang="en", data={}))
        b = await renderer.render(_card(template=name, lang="en", data={}))
        w, h = _check_jpeg(b)
        assert w == 1080 and h <= 1350


async def test_very_long_content_is_capped(renderer):
    data = sample_data("home", "hi")
    data["rows"] = [{"label": f"Row {i}", "value": "x" * 40} for i in range(60)]
    w, h = _check_jpeg(await renderer._render_html(_card(template="home", lang="hi", data=data)))
    assert (w, h) == (1080, 1350)


async def test_slightly_tall_content_is_scaled_to_fit(renderer):
    data = sample_data("kharcha", "mr")
    data["lines"] = data["lines"] + [dict(data["lines"][1], title=f"अतिरिक्त {i}") for i in range(4)]
    w, h = _check_jpeg(await renderer._render_html(_card(template="kharcha", lang="mr", data=data)))
    assert w == 1080 and h <= 1350


async def test_parivaar_with_tryon_photo(renderer):
    photo = io.BytesIO()
    Image.new("RGB", (1080, 810), (200, 190, 170)).save(photo, format="JPEG")
    data = sample_data("parivaar", "hi") | {"image": photo.getvalue()}
    html = renderer.html(_card(template="parivaar", lang="hi", data=data))
    assert 'class="photo"' in html and "data:image/jpeg;base64," in html
    w, h = _check_jpeg(await renderer._render_html(_card(template="parivaar", lang="hi", data=data)))
    assert w == 1080 and h <= 1350


def test_html_escapes_and_inlines_images():
    r = Renderer()
    data = sample_data("benefit", "en")
    data["product_title"] = "<script>x</script>"
    html = r.html(_card(template="benefit", lang="en", data=data))
    assert "<script>x</script>" not in html
    assert "data:image/png;base64," in html or "data:image/webp;base64," in html  # products/<sku>.png inlined


def test_options_never_show_prices():
    r = Renderer()
    for lang in ("hi", "mr", "en"):
        html = r.html(_card(template="options", lang=lang, data=sample_data("options", lang)))
        assert "₹" not in html
        assert 'class="ribbon' in html  # the best pick is the hero


def test_catalog_hides_price_row_when_missing():
    r = Renderer()
    data = sample_data("catalog", "en")
    items = data["items"]
    html = r.html(_card(template="catalog", lang="en", data=data))
    assert html.count('class="price num"') == sum(1 for it in items if it.get("price_str"))
    data["items"] = items[:3]  # tile layout
    html = r.html(_card(template="catalog", lang="en", data=data))
    assert html.count('class="price num"') == sum(1 for it in items[:3] if it.get("price_str"))


def test_parivaar_accepts_legacy_cost_text():
    r = Renderer()
    data = {k: v for k, v in sample_data("parivaar", "hi").items() if k != "cost_rows"}
    data["cost_text"] = "पूरा खर्च ₹65,288 · सुझाव सहित ₹68,787"
    data["offer_text"] = "ऑफ़र: HDFC −₹5,000 (15 Nov तक)"
    html = r.html(_card(template="parivaar", lang="hi", data=data))
    assert "₹65,288" in html and "₹68,787" in html
    assert "ऑफ़र: HDFC" not in html and "HDFC −₹5,000" in html


EMOJI_RE = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]")


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_cards_have_no_emoji_and_no_truncation(template):
    r = Renderer()
    data = sample_data(template, "en")
    # producers may still send emoji in text/icons: they must never reach the card
    if template == "benefit":
        data["benefits"][0] = {"icon": "🏏", "text": "Sharp ball 🏏 in cricket"}
    if template == "score":
        data["level_text"] = "Guide level! 👏"
    for lang in ("hi", "mr", "en"):
        html = r.html(_card(template, lang, data if lang == "en" else sample_data(template, lang)))
        assert not EMOJI_RE.search(html), f"emoji in {template}/{lang}"
        assert "line-clamp" not in html and "text-overflow" not in html


def test_icon_keywords_render_as_svg_not_text():
    r = Renderer()
    kws = ["motion", "sun", "size", "voice", "angle", "film", "apps", "check", "mystery"]
    html = ""
    for i in range(0, len(kws), 4):  # the card shows up to 4 benefits
        data = {"product_title": "X", "benefits": [{"icon": k, "text": f"benefit {k[:2]}"} for k in kws[i : i + 4]]}
        html += r.html(_card("benefit", "en", data))
    for k in kws[:-1]:
        assert f"i-{k} " in html and f">{k}<" not in html
    assert "i-dot " in html and "mystery" not in html  # unknown keyword → neutral dot
    html = ""
    for ks in (("up", "down", "zzz"), ("low",)):  # the brief shows up to 3 stock changes
        data = {"changes": [{"icon": k, "text": f"change {i}"} for i, k in enumerate(ks)]}
        html += r.html(_card("brief", "en", data))
    assert all(f"k-{k}" in html for k in ("up", "down", "low", "dot")) and "zzz" not in html


def test_product_and_compare_optional_keys():
    r = Renderer()
    html = r.html(_card("product", "en", sample_data("product", "en") | {"price_str": None, "offer_text": None}))
    assert "₹62,990" not in html and 'class="price' not in html
    html = r.html(_card("product", "en", sample_data("product", "en")))
    assert html.count('class="pc"') == 5 and "₹62,990" in html
    html = r.html(_card("compare", "en", sample_data("compare", "en") | {"price_a": None, "price_b": None}))
    assert "tr price" not in html
    html = r.html(_card("compare", "en", sample_data("compare", "en")))
    assert "tr price" in html and html.count("c num best") + html.count("c av num best") == 6


def test_helpers():
    assert pct("63%") == 63 and pct("६३%") == 63 and pct(None) is None and pct("x") is None and pct(250) == 100
    assert frac("12/14") == pytest.approx(12 / 14) and frac("—") is None
    assert dv_class("घर") == " dv" and dv_class("Ghar") == ""
    assert stock_icon("4 in store") == "store" and stock_icon("Delivery in 3 days") == "truck"
    assert stock_icon("Novex 55 OLED stock khatam") == "alert"
    assert _strip_label("Delivery: Tue 4 Nov") == "Tue 4 Nov" and _strip_label("HDFC: −₹5,000") == "HDFC: −₹5,000"
    rows = _cost_rows_from_text("पूरा खर्च ₹65,288 · सुझाव सहित ₹68,787")
    assert rows[0] == {"label": "पूरा खर्च", "value": "₹65,288", "strong": True} and rows[1]["value"] == "₹68,787"


def test_to_data_uri_variants():
    assert to_data_uri(None) is None
    assert to_data_uri("products/DOES-NOT-EXIST.png") is None
    assert to_data_uri("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"
    png = io.BytesIO()
    Image.new("RGB", (4, 4)).save(png, format="PNG")
    assert to_data_uri(png.getvalue()).startswith("data:image/png;base64,")
    big = io.BytesIO()
    Image.new("RGBA", (1200, 600), (10, 20, 30, 128)).save(big, format="PNG")
    small = to_data_uri(big.getvalue(), max_px=300)
    assert small.startswith("data:image/") and len(small) < len(to_data_uri(big.getvalue()))


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_pillow_fallback(template):
    for lang in ("hi", "mr", "en"):
        w, h = _check_jpeg(render_pillow(_card(template=template, lang=lang, data=sample_data(template, lang))))
        assert w == 1080 and h <= 1350
    _check_jpeg(render_pillow(_card(template=template, lang="hi", data={})))


async def test_not_started_renderer_uses_fallback():
    r = Renderer()  # never started → ready False
    w, _ = _check_jpeg(await r.render(_card(template="score", lang="hi", data=sample_data("score"))))
    assert w == 1080


# ---------- image scripts ----------
def _one_sku_per_category() -> list[str]:
    seed = ROOT / "data" / "seed" / "products.yaml"
    products = yaml.safe_load(seed.read_text(encoding="utf-8")) if seed.exists() else []
    if isinstance(products, dict):
        products = products.get("products") or []
    picked: dict[str, str] = {}
    for p in products or []:
        cat = str(p.get("category") or "").lower()
        key = cat
        if cat == "wm":  # exercise both washer drawings
            key = "wm-top" if "top" in str((p.get("specs") or {}).get("load") or "").lower() else "wm-front"
        if cat in CATEGORIES and p.get("sku") and key not in picked:
            picked[key] = str(p["sku"])
    return list(picked.values())


def test_product_image_script_makes_transparent_pngs(tmp_path):
    skus = _one_sku_per_category()
    assert skus, "no SKUs in data/seed/products.yaml"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "make_product_images.py"),
            "--out",
            str(tmp_path),
            "--only",
            ",".join(skus),
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert proc.returncode == 0, proc.stderr
    for sku in skus:
        f = tmp_path / f"{sku}.png"
        assert f.is_file(), f"missing {f.name}: {proc.stdout} {proc.stderr}"
        im = Image.open(f)
        assert im.format == "PNG" and im.mode == "RGBA", (sku, im.mode)
        lo, hi = im.getchannel("A").getextrema()
        assert lo == 0 and hi == 255, f"{sku}: needs transparent background and an opaque product"
        assert 500 <= max(im.size) <= 1000, (sku, im.size)


def test_screen_image_script(tmp_path):
    out = tmp_path / "screen.jpg"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_screen_image.py"), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    im = Image.open(out)
    assert im.format == "JPEG" and im.size == (1600, 900)
    lo, hi = zip(*im.convert("RGB").getextrema(), strict=True)
    assert max(hi) - min(lo) > 120, "wallpaper should be vivid, not flat"
