"""Shelf labels of real catalogue products, to test the label scan (user, 6 Nov 2026: "to test give a couple labels
as well; all real data; no mocks or hallucination").

Every word on a label comes from the Jio backend's own record of the product (tests/fixtures/jio/products.json,
recorded from the Reliance Digital sales channel): brand, full name, model number, the specs the sync maps from it,
the backend's own highlights and warranty. No price is printed (the bot says the live price from today's
catalogue), and no barcode or QR code is added, so a scan exercises the real read: the printed model number or name.

    python scripts/make_real_labels.py [--out DIR] [--only ITEM_CODE,ITEM_CODE]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chanakya.truth import jio_map  # noqa: E402

ITEMS = ROOT / "tests" / "fixtures" / "jio" / "products.json"
OUT = ROOT / "cards_preview" / "labels_real"
# item code → category (as the sync files them): two washing machines (the brief's kg line), a fridge, a TV
DEFAULT = {"491538905": "wm", "491666469": "wm", "491604593": "fridge", "491297651": "tv"}


def _yes(v) -> bool:
    return v is True or str(v).lower() in ("true", "yes")


def spec_rows(cat: str, sp: dict) -> list[tuple[str, str]]:
    """The mapped specs, worded plainly, values exactly as in the data."""
    out: list[tuple[str, str]] = []

    def num(v) -> str:
        return str(int(v)) if float(v).is_integer() else str(v)

    if cat == "wm":
        if sp.get("capacity_kg") is not None:
            out.append(("Capacity", f"{num(sp['capacity_kg'])} kg"))
        if sp.get("load"):
            out.append(("Loading", f"{str(sp['load']).title()} load"))
        if sp.get("automatic"):
            out.append(("Type", str(sp["automatic"])))
        if _yes(sp.get("inverter_motor")):
            out.append(("Motor", "Inverter"))
    elif cat == "fridge":
        if sp.get("capacity_l") is not None:
            out.append(("Capacity", f"{num(sp['capacity_l'])} L"))
        if sp.get("star_rating") is not None:
            out.append(("Energy", f"{num(sp['star_rating'])} Star"))
        if sp.get("door"):
            out.append(("Door", str(sp["door"]).replace("_", " ").title()))
        for key, label in (("frost_free", "Frost free"), ("convertible", "Convertible"),
                           ("inverter_compressor", "Inverter compressor"), ("stabiliser_free", "Stabiliser free")):
            if _yes(sp.get(key)):
                out.append(("Feature", label))
    elif cat == "tv":
        if sp.get("size_in") is not None:
            out.append(("Screen", f"{num(sp['size_in'])} inch"))
        for key, label in (("resolution", "Resolution"), ("panel", "Panel"), ("os", "OS"), ("hdr_formats", "HDR")):
            if sp.get(key):
                out.append((label, str(sp[key])))
        for key, label in (("wifi", "Wi-Fi"), ("hdmi_arc", "HDMI ARC")):
            if _yes(sp.get(key)):
                out.append(("Connectivity", label))
    return out


def label_html(row: dict) -> str:
    info = row["info"]
    specs = "".join(f"<tr><td class='k'>{escape(k)}</td><td class='v'>{escape(v)}</td></tr>"
                    for k, v in spec_rows(row["category"], row["specs"]))
    feats = "".join(f"<li>{escape(f['en'])}</li>" for f in (info.get("features") or [])[:3])
    warranty = info.get("warranty_text")
    if warranty and not re.search(r"[A-Za-z]", str(warranty)):
        warranty = None  # a bare number ('1') has no unit in the data: not printed rather than guessed
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; background: #fff; }}
  .l {{ width: 900px; box-sizing: border-box; border: 10px solid #E42529; padding: 46px 52px; display: flex;
        flex-direction: column; }}
  .top {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 3px solid #eee; padding-bottom: 22px; }}
  .rd {{ font-size: 30px; font-weight: 800; color: #E42529; letter-spacing: .5px; }}
  .brand {{ font-size: 54px; font-weight: 800; color: #111; }}
  .name {{ font-size: 36px; line-height: 1.3; color: #222; margin-top: 30px; font-weight: 600; }}
  .mn {{ margin-top: 26px; font-size: 40px; font-weight: 800; color: #111; letter-spacing: 1px; }}
  .mn span {{ font-size: 26px; font-weight: 600; color: #666; letter-spacing: 0; margin-right: 12px; }}
  table {{ margin-top: 34px; width: 100%; border-collapse: collapse; font-size: 32px; }}
  td {{ padding: 14px 0; border-bottom: 2px solid #f0f0f0; }}
  td.k {{ color: #666; width: 38%; }} td.v {{ color: #111; font-weight: 700; }}
  ul {{ margin: 30px 0 0; padding-left: 34px; font-size: 28px; color: #333; line-height: 1.4; }}
  .w {{ margin-top: 26px; font-size: 28px; color: #111; }}
  .foot {{ margin-top: 56px; font-size: 22px; color: #999; display: flex; justify-content: space-between; }}
</style></head><body><div class="l">
  <div class="top"><div class="brand">{escape(row["brand"])}</div><div class="rd">Reliance Digital</div></div>
  <div class="name">{escape(info["name"])}</div>
  {f'<div class="mn"><span>Model</span>{escape(info["model_number"])}</div>' if info.get("model_number") else ""}
  <table>{specs}</table>
  {f"<ul>{feats}</ul>" if feats else ""}
  {f'<div class="w">Warranty: {escape(str(warranty))}</div>' if warranty else ""}
  <div class="foot"><span>TEST DATA · Jio Commerce UAT catalogue</span><span>Item {escape(row["sku"])}</span></div>
</div></body></html>"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = json.loads(ITEMS.read_text())
    items = {str(it.get("item_code")): it for it in (raw.get("items", raw) if isinstance(raw, dict) else raw)}
    want = {k: v for k, v in DEFAULT.items() if not a.only or k in a.only.split(",")}
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 900, "height": 1300})
        for code, cat in want.items():
            row = jio_map.map_product(items[code], cat)
            if row is None:
                print("no price in the data, skipped:", code)
                continue
            await page.set_content(label_html(row), wait_until="load")
            name = f"{row['brand']}_{(row['info'].get('model_number') or code).replace('/', '-')}.jpg"
            await page.locator(".l").screenshot(path=str(out / name), type="jpeg", quality=90)
            print(out / name, "|", row["info"]["name"])
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
