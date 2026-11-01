"""Retail shelf labels / spec tags per SKU for testing the label-scan flow (TEST DATA).

For every SKU in data/seed/products.yaml: data/labels/<sku>.jpg (900×1300, portrait) showing brand + model
(exactly as ``model``), model number, category, 5–7 key specs with units, energy (AC/WM), warranty,
MRP (struck through) and offer price from data/seed/prices_stock.yaml (RD-JUHU row), an EAN-13 style barcode
and a small TEST DATA mark. Also data/labels/all_labels.pdf: A4 contact sheet, 6 labels per page.
Rendered with Playwright/Chromium (HTML + SVG).

    python scripts/make_labels.py [--out DIR] [--only SKU,SKU] [--store RD-JUHU] [--no-pdf]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import sys
import zlib
from html import escape
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed"
OUT = ROOT / "data" / "labels"
W, H = 900, 1300
BRAND_COL = {"arka": "#D9681E", "meru": "#1F6FD1", "novex": "#7B3FD6"}


# ---------- data ----------
def _load(name: str) -> list[dict]:
    p = SEED / name
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [x for x in data if isinstance(x, dict)]


def inr(v) -> str:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return ""
    s = str(abs(n))
    if len(s) > 3:  # Indian grouping: 1,23,456
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return "₹" + s


def yes(v) -> str:
    return "Yes" if v else "No"


def num(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def category_name(p: dict) -> str:
    cat = str(p.get("category") or "").lower()
    sp = p.get("specs") or {}
    if cat == "tv":
        return f"{sp.get('panel') or 'LED'} Smart TV"
    if cat == "ac":
        return "Split AC · Inverter" if sp.get("inverter") else "Split AC"
    if cat == "wm":
        return f"{str(sp.get('load') or 'front').title()} Load Washing Machine"
    if cat == "laptop":
        return "Gaming Laptop" if str(sp.get("gpu") or "").lower() == "dedicated" else "Laptop"
    if cat == "soundbar":
        return "Soundbar"
    return cat.upper()


def key_specs(p: dict) -> list[tuple[str, str]]:
    """5–7 label/value pairs with units, straight from the product row."""
    cat = str(p.get("category") or "").lower()
    sp = p.get("specs") or {}
    rows: list[tuple[str, str]] = []

    def add(label, value, cond=True):
        if cond and value not in (None, "", []):
            rows.append((label, str(value)))

    if cat == "tv":
        if sp.get("size_in"):
            add("Screen size", f"{num(sp['size_in'])} inch ({round(float(sp['size_in']) * 2.54)} cm)")
        add(
            "Display",
            " · ".join(
                str(x)
                for x in (
                    sp.get("panel"),
                    sp.get("resolution") and f"{sp['resolution']} UHD"
                    if sp.get("resolution") == "4K"
                    else sp.get("resolution"),
                )
                if x
            ),
        )
        add("Refresh rate", sp.get("refresh_hz") and f"{sp['refresh_hz']} Hz")
        add("Brightness", sp.get("brightness_nits") and f"{sp['brightness_nits']} nits")
        add(
            "Sound",
            sp.get("speaker_w") and f"{sp['speaker_w']} W" + (" · Dolby Atmos" if sp.get("dolby_atmos") else ""),
        )
        add("HDR", ", ".join(sp.get("hdr_formats") or []))
        add("Ports", sp.get("hdmi_ports") and f"{sp['hdmi_ports']} HDMI · {sp.get('usb_ports', 0)} USB")
    elif cat == "ac":
        add("Capacity", sp.get("ton") and f"{num(sp['ton'])} Ton")
        add("Cooling capacity", sp.get("cooling_capacity_w") and f"{sp['cooling_capacity_w']} W")
        add("ISEER", sp.get("iseer"))
        add("Inverter compressor", yes(sp.get("inverter")), "inverter" in sp)
        add("Noise level", sp.get("noise_db") and f"{sp['noise_db']} dB")
        add("Refrigerant", sp.get("refrigerant"))
        add("Wi-Fi control", yes(sp.get("wifi")), "wifi" in sp)
    elif cat == "wm":
        add("Capacity", sp.get("capacity_kg") and f"{num(sp['capacity_kg'])} kg")
        add("Spin speed", sp.get("rpm") and f"{sp['rpm']} RPM")
        add("Wash programmes", sp.get("programmes"))
        add("Wash technology", sp.get("wash_tech"))
        add("In-built heater", yes(sp.get("heater")), "heater" in sp)
        add("Inverter motor", yes(sp.get("inverter_motor")), "inverter_motor" in sp)
        add("Steam wash", yes(sp.get("steam")), "steam" in sp)
    elif cat == "laptop":
        add("Processor", sp.get("cpu"))
        add("Memory", sp.get("ram_gb") and f"{sp['ram_gb']} GB RAM")
        st = sp.get("storage_gb")
        add(
            "Storage", st and (f"{num(st / 1024)} TB" if st >= 1024 else f"{st} GB") + f" {sp.get('ssd_type') or 'SSD'}"
        )
        add(
            "Display",
            sp.get("screen_in")
            and f"{num(sp['screen_in'])} inch {sp.get('display_panel') or ''} · "
            f"{sp.get('refresh_hz') or 60} Hz".replace("  ", " "),
        )
        add("Graphics", "Dedicated GPU" if str(sp.get("gpu") or "").lower() == "dedicated" else "Integrated")
        add("Battery", sp.get("battery_h") and f"Up to {sp['battery_h']} hours")
        add("Weight", sp.get("weight_kg") and f"{num(sp['weight_kg'])} kg")
    elif cat == "soundbar":
        add("Output power", sp.get("speaker_w") and f"{sp['speaker_w']} W")
        add("Channels", sp.get("channels") and f"{sp['channels']} ch")
        add("Subwoofer", "Wireless subwoofer" if sp.get("subwoofer") else "Built-in bass", "subwoofer" in sp)
        add("Dolby Atmos", yes(sp.get("dolby_atmos")), "dolby_atmos" in sp)
        conn = [
            n for n, k in (("Bluetooth", "bluetooth"), ("HDMI ARC", "hdmi_arc"), ("Optical", "optical_in")) if sp.get(k)
        ]
        add("Connectivity", " · ".join(conn))
        add("Sound modes", sp.get("sound_modes"))
    else:
        for k, v in list(sp.items())[:6]:
            add(k.replace("_", " ").title(), v if not isinstance(v, list) else ", ".join(map(str, v)))
    return rows[:7]


def warranty_text(p: dict) -> str:
    w = ((p.get("info") or {}).get("warranty")) or {}
    if not isinstance(w, dict) or not w:
        return ""
    parts = []
    if w.get("years"):
        parts.append(f"{num(w['years'])} year{'s' if float(w['years']) != 1 else ''} product")
    for k, name in (
        ("compressor_years", "compressor"),
        ("motor_years", "motor"),
        ("panel_years", "panel"),
        ("battery_years", "battery"),
    ):
        if w.get(k):
            parts.append(f"{num(w[k])} yr {name}")
    return " · ".join(parts)


# ---------- barcode ----------
_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_PAR = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def ean13(sku: str) -> str:
    body = "890" + f"{zlib.crc32(sku.encode()) % 10**9:09d}"  # 890 = India prefix; fake but well-formed
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - s % 10) % 10)


def barcode_svg(code: str, width: float = 420, height: float = 110) -> str:
    first, left, right = int(code[0]), code[1:7], code[7:]
    bits = "101" + "".join((_L if _PAR[first][i] == "L" else _G)[int(d)] for i, d in enumerate(left)) + "01010"
    bits += "".join(_R[int(d)] for d in right) + "101"
    m = width / (len(bits) + 18)
    x0 = 9 * m
    guards = set(range(0, 3)) | set(range(45, 50)) | set(range(92, 95))
    bars = []
    for i, b in enumerate(bits):
        if b == "1":
            h = height if i in guards else height - 18
            bars.append(f'<rect x="{x0 + i * m:.2f}" y="0" width="{m + 0.05:.2f}" height="{h:.1f}"/>')
    digits = (
        f'<text x="{x0 - 4 * m:.1f}" y="{height}" font-size="20">{code[0]}</text>'
        f'<text x="{x0 + 24 * m:.1f}" y="{height}" font-size="20" text-anchor="middle" letter-spacing="3">{left}</text>'
        f'<text x="{x0 + 71 * m:.1f}" y="{height}" font-size="20" text-anchor="middle" letter-spacing="3">{right}</text>'
    )
    return (
        f'<svg width="{width:.0f}" height="{height + 4:.0f}" viewBox="0 0 {width:.0f} {height + 4:.0f}" '
        f'font-family="Menlo, \'DejaVu Sans Mono\', monospace" fill="#111">{"".join(bars)}{digits}</svg>'
    )


# ---------- label HTML ----------
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:900px;height:1300px;background:#fff}
body{font-family:"Helvetica Neue",Arial,"Noto Sans","Noto Sans Devanagari",sans-serif;color:#111;-webkit-font-smoothing:antialiased}
.lab{width:900px;height:1300px;padding:44px 52px 36px;display:flex;flex-direction:column;border:10px solid #111}
.top{display:flex;justify-content:space-between;align-items:center}
.brand{font-size:40px;font-weight:900;letter-spacing:6px;text-transform:uppercase}
.cat{font-size:24px;font-weight:700;color:#fff;background:#111;padding:8px 16px;border-radius:8px}
.bar{height:10px;margin:20px 0 22px;border-radius:5px}
.model{font-size:70px;line-height:1.05;font-weight:900;letter-spacing:-1px}
.mno{margin-top:12px;font-size:28px;font-family:Menlo,"DejaVu Sans Mono",monospace;color:#333}
.mno b{color:#111}
table{width:100%;border-collapse:collapse;margin-top:26px}
td{font-size:29px;padding:11px 0;border-bottom:2px solid #e2e2e2;vertical-align:top}
td.k{color:#555;width:42%}
td.v{font-weight:700;text-align:right}
.row{display:flex;gap:18px;margin-top:20px}
.box{flex:1;border:3px solid #111;border-radius:14px;padding:14px 18px}
.box .k{font-size:20px;font-weight:800;letter-spacing:2px;text-transform:uppercase;color:#555}
.box .v{font-size:27px;font-weight:700;margin-top:4px;line-height:1.25}
.stars{font-size:34px;letter-spacing:4px;color:#111;line-height:1}
.stars .off{color:#cfcfcf}
.price{margin-top:auto;display:flex;align-items:flex-end;justify-content:space-between;gap:20px;padding-top:18px}
.mrp{font-size:30px;color:#555}
.mrp s{color:#333;text-decoration-thickness:3px}
.offer .k{font-size:24px;font-weight:800;letter-spacing:2px;text-transform:uppercase}
.offer .v{font-size:86px;font-weight:900;line-height:1;letter-spacing:-2px}
.tax{font-size:20px;color:#666;margin-top:4px}
.foot{display:flex;justify-content:space-between;align-items:flex-end;margin-top:22px;padding-top:18px;border-top:3px dashed #bbb}
.sku{font-size:22px;font-family:Menlo,"DejaVu Sans Mono",monospace;color:#333;text-align:right;line-height:1.5}
.test{display:inline-block;font-size:18px;font-weight:800;letter-spacing:2px;color:#b4365a;border:2px solid #b4365a;border-radius:6px;padding:2px 8px}
"""


def label_html(p: dict, price: dict | None) -> str:
    sku = str(p.get("sku") or "")
    brand = str(p.get("brand") or "")
    model = str(p.get("model") or sku)
    info = p.get("info") or {}
    sp = p.get("specs") or {}
    energy = p.get("energy") or {}
    cat = str(p.get("category") or "").lower()
    accent = BRAND_COL.get(brand.lower(), "#111")
    rows = "".join(f'<tr><td class="k">{escape(k)}</td><td class="v">{escape(v)}</td></tr>' for k, v in key_specs(p))
    boxes = []
    if cat in ("ac", "wm"):
        star = energy.get("star") or sp.get("star")
        inner = ""
        if star:
            n = max(0, min(5, int(star)))
            inner += f'<div class="stars">{"★" * n}<span class="off">{"★" * (5 - n)}</span></div><div class="v">{n} Star (BEE)</div>'
        if energy.get("kwh_year"):
            inner += f'<div class="v">{energy["kwh_year"]} kWh / year</div>'
        if inner:
            boxes.append(f'<div class="box"><div class="k">Energy</div>{inner}</div>')
    w = warranty_text(p)
    if w:
        boxes.append(f'<div class="box"><div class="k">Warranty</div><div class="v">{escape(w)}</div></div>')
    price_html = ""
    if price:
        mrp, offer = price.get("mrp"), price.get("price")
        mrp_html = (
            f'<div class="mrp">MRP <s>{inr(mrp)}</s></div>'
            if mrp and offer and mrp != offer
            else (f'<div class="mrp">MRP {inr(mrp)}</div>' if mrp else "")
        )
        price_html = (
            f'<div class="price"><div>{mrp_html}<div class="tax">Incl. of all taxes</div></div>'
            f'<div class="offer"><div class="k">Offer price</div><div class="v">{inr(offer)}</div></div></div>'
        )
    year = f" · {info['year']}" if info.get("year") else ""
    series = f"{escape(str(info['series']))}{year}" if info.get("series") else ""
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='lab'>"
        f"<div class='top'><span class='brand'>{escape(brand)}</span><span class='cat'>{escape(category_name(p))}</span></div>"
        f"<div class='bar' style='background:{accent}'></div>"
        f"<div class='model'>{escape(model)}</div>"
        f"<div class='mno'>Model No. <b>{escape(str(info.get('model_number') or sku))}</b>"
        f"{' &nbsp;·&nbsp; ' + series if series else ''}</div>"
        f"<table>{rows}</table>"
        f"{'<div class=row>' + ''.join(boxes) + '</div>' if boxes else ''}"
        f"{price_html}"
        f"<div class='foot'>{barcode_svg(ean13(sku))}<div class='sku'>SKU {escape(sku)}<br>"
        f"<span class='test'>TEST DATA</span></div></div>"
        f"</div></body></html>"
    )


def sheet_html(jpegs: list[bytes]) -> str:
    cells = []
    for b in jpegs:
        uri = "data:image/jpeg;base64," + base64.b64encode(b).decode()
        cells.append(f'<div class="c"><img src="{uri}"></div>')
    pages = ["".join(cells[i : i + 6]) for i in range(0, len(cells), 6)]
    css = (
        "@page{size:A4;margin:0}*{margin:0;padding:0;box-sizing:border-box}"
        ".pg{width:210mm;height:297mm;padding:9mm 12mm;display:grid;grid-template-columns:1fr 1fr;"
        "grid-template-rows:repeat(3,1fr);gap:5mm;page-break-after:always}"
        ".c{display:flex;align-items:center;justify-content:center}.c img{height:88mm;width:auto;display:block}"
    )
    return (
        f"<!doctype html><html><head><style>{css}</style></head><body>"
        + "".join(f'<div class="pg">{p}</div>' for p in pages)
        + "</body></html>"
    )


async def render(products: list[dict], prices: dict[str, dict], out: Path, pdf: bool) -> int:
    from PIL import Image
    from playwright.async_api import async_playwright

    small: list[bytes] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = await browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            for p in products:
                await page.set_content(label_html(p, prices.get(str(p["sku"]))), wait_until="load")
                jpg = await page.screenshot(type="jpeg", quality=92, clip={"x": 0, "y": 0, "width": W, "height": H})
                (out / f"{p['sku']}.jpg").write_bytes(jpg)
                if pdf:
                    im = Image.open(io.BytesIO(jpg))
                    im.thumbnail((620, 900))
                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=86)
                    small.append(buf.getvalue())
            if pdf and small:
                await page.set_content(sheet_html(small), wait_until="load")
                await page.pdf(path=str(out / "all_labels.pdf"), format="A4", print_background=True)
        finally:
            await browser.close()
    return len(products)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render retail shelf labels per SKU (TEST DATA).")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--only", default="")
    ap.add_argument("--store", default="RD-JUHU", help="store row in prices_stock.yaml for MRP / offer price")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args(argv)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    products = [p for p in _load("products.yaml") if p.get("sku") and (not only or p["sku"] in only)]
    prices = {str(r["sku"]): r for r in _load("prices_stock.yaml") if r.get("store_id") == args.store and r.get("sku")}
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        n = asyncio.run(render(products, prices, args.out, not args.no_pdf))
    except Exception as e:  # Chromium missing
        print(f"error: Chromium render failed ({e.__class__.__name__}: {e})", file=sys.stderr)
        return 1
    print(f"wrote {n} labels to {args.out}" + ("" if args.no_pdf else " + all_labels.pdf"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
