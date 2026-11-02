"""Jio Commerce product (getProducts item) → Chanakya product facts. Nothing is invented.

Every spec is copied from the product's own `attributes`, or read from a number stated in the product's own
`name` (e.g. "Sony 108 cm (43 inch) …" → size_in 43) when the attribute is absent. A spec that is in neither is
left out, so rules that need it count as "not met" and no benefit is claimed. `info.spec_source` records where
each spec came from. Prices are the listing's effective (selling) and marked (MRP) prices, as returned."""

from __future__ import annotations

import html
import re
from datetime import date

NUM = r"(\d+(?:\.\d+)?)"
PLACEHOLDER = "/l2_category/"  # the backend's shared placeholder picture (a generic icon sheet, not the product)
EMPTY = {"", "none", "null", "nan", "na", "n/a", "-"}


def attr(p: dict, *keys: str) -> str | None:
    a = p.get("attributes") or {}
    for k in keys:
        v = a.get(k)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x not in (None, ""))
        if v is None or isinstance(v, (dict, bool)):
            continue
        s = str(v).strip()
        if s.lower() not in EMPTY:
            return s
    return None


def _num(text: str | None, unit: str) -> float | None:
    if not text:
        return None
    m = re.search(rf"{NUM}\s*(?:{unit})(?![a-z])", text, re.I)
    return float(m.group(1)) if m else None


def _clean_num(x: float | None) -> float | int | None:
    if x is None:
        return None
    return int(x) if float(x).is_integer() else x


def _yes(v: str | None) -> bool | None:
    if v is None:
        return None
    s = v.strip().lower()
    return True if s in ("yes", "true", "y") else (False if s in ("no", "false", "n") else None)


def _strip_html(s: str) -> list[str]:
    s = html.unescape(s or "")
    parts = re.split(r"</?li>|<br\s*/?>|\n", s)
    out = []
    for x in parts:
        t = re.sub(r"<[^>]+>", " ", x)
        t = re.sub(r"\s+", " ", t).strip(" ,;")
        if t:
            out.append(t)
    return out


def _put(specs: dict, src: dict, key: str, value, source: str) -> None:
    if value is None or value == "" or key in specs:
        return
    specs[key] = value
    src[key] = source


def _inches(p: dict, *keys: str) -> tuple[float | None, str]:
    for k in keys:
        v = _num(attr(p, k), r"inch|in\b|\"")
        if v:
            return v, f"attributes.{k}"
    v = _num(p.get("name"), r"inch|in\b|\"")
    return (v, "name") if v else (None, "")


def _tv(p: dict, name: str, specs: dict, src: dict) -> None:
    size, s = _inches(p, "screen_size_diagonal", "screen-size")
    _put(specs, src, "size_in", _clean_num(size), s)
    res_text = " ".join(x for x in (attr(p, "screen_resolution"), attr(p, "display_type")) if x)
    for label, rx in (("8K", r"\b8k\b"), ("4K", r"\b4k\b|ultra hd|uhd|3840"), ("Full HD", r"full hd|fhd|1920"),
                      ("HD", r"\bhd ready\b|\bhd\b|1366")):
        if re.search(rx, res_text, re.I):
            _put(specs, src, "resolution", label, "attributes.screen_resolution/display_type")
            break
        if re.search(rx, name, re.I):
            _put(specs, src, "resolution", label, "name")
            break
    tech = attr(p, "display_technology")
    for label, rx in (("OLED", r"\boled\b"), ("MiniLED", r"mini\s*-?led"), ("QLED", r"\bqled\b"),
                      ("Nanocell", r"nano\s*cell"), ("LED", r"\bled\b")):
        if tech and re.search(rx, tech, re.I):
            _put(specs, src, "panel", label, "attributes.display_technology")
            break
        if re.search(rx, name, re.I):
            _put(specs, src, "panel", label, "name")
            break
    _put(specs, src, "os", attr(p, "tv_operating_system"), "attributes.tv_operating_system")
    hdmi = _num(attr(p, "no-of-hdmi-ports", "hdmi_ports"), r"") if attr(p, "no-of-hdmi-ports", "hdmi_ports") else None
    _put(specs, src, "hdmi_ports", _clean_num(hdmi), "attributes.no-of-hdmi-ports")
    _put(specs, src, "refresh_hz", _clean_num(_num(attr(p, "refresh_rate", "refresh-rate"), "hz")), "attributes.refresh_rate")
    _put(specs, src, "brightness_nits", _clean_num(_num(attr(p, "brightness", "peak_brightness"), "nits?")),
         "attributes.brightness")
    _put(specs, src, "speaker_w", _clean_num(_num(attr(p, "speaker_output", "audio_output", "sound_output",
                                                       "speaker-output"), "w\\b|watts?")), "attributes.speaker_output")
    _put(specs, src, "wifi", _yes(attr(p, "wi-fi", "wifi")), "attributes.wi-fi")
    hdr = attr(p, "hdr")
    if hdr and _yes(hdr) is not False:
        _put(specs, src, "hdr_formats", hdr if _yes(hdr) is None else "HDR", "attributes.hdr")
    if re.search(r"dolby\s*atmos", name, re.I):
        _put(specs, src, "dolby_atmos", True, "name")


def _ac(p: dict, name: str, specs: dict, src: dict) -> None:
    for k in ("capacity-in-tons", "capacity_in_tons", "tonnage", "capacity"):
        t = _num(attr(p, k), r"tons?\b|tr\b")
        if t:
            _put(specs, src, "ton", _clean_num(t), f"attributes.{k}")
    _put(specs, src, "ton", _clean_num(_num(name, r"tons?\b")), "name")
    star = _num(attr(p, "bee-star-rating", "star-rating", "energy-rating"), r"star")
    _put(specs, src, "star", _clean_num(star), "attributes.bee-star-rating")
    _put(specs, src, "star", _clean_num(_num(name, r"star")), "name")
    comp = attr(p, "compressor-type", "compressor_type") or ""
    if re.search(r"inverter", comp, re.I):
        _put(specs, src, "inverter", True, "attributes.compressor-type")
    elif re.search(r"\binverter\b", name, re.I):
        _put(specs, src, "inverter", True, "name")
    _put(specs, src, "wifi", _yes(attr(p, "wi-fi", "wifi")), "attributes.wi-fi")
    _put(specs, src, "room_size_text", attr(p, "approx-room-size"), "attributes.approx-room-size")


def _wm(p: dict, name: str, specs: dict, src: dict) -> None:
    kg = _num(attr(p, "capacity", "washing-capacity", "washing_capacity"), r"kg\b")
    _put(specs, src, "capacity_kg", _clean_num(kg), "attributes.capacity")
    _put(specs, src, "capacity_kg", _clean_num(_num(name, r"kg\b")), "name")
    lt = attr(p, "loading-type", "loading_type") or ""
    for label in ("front", "top"):
        if re.search(label, lt, re.I):
            _put(specs, src, "load", label, "attributes.loading-type")
        elif re.search(rf"\b{label}[\s-]*load", name, re.I):
            _put(specs, src, "load", label, "name")
    _put(specs, src, "automatic", attr(p, "washing-machine-type"), "attributes.washing-machine-type")
    _put(specs, src, "star", _clean_num(_num(attr(p, "bee-star-rating"), r"star")), "attributes.bee-star-rating")
    _put(specs, src, "star", _clean_num(_num(name, r"star")), "name")
    _put(specs, src, "rpm", _clean_num(_num(attr(p, "spin-speed", "spin_speed", "max-spin-speed"), r"rpm")),
         "attributes.spin-speed")
    if re.search(r"inverter", name, re.I):
        _put(specs, src, "inverter_motor", True, "name")


DOOR = (("side_by_side", r"side[\s-]*by[\s-]*side|\bsbs\b"), ("bottom_freezer", r"bottom[\s-]*(mount|freezer)"),
        ("double", r"double[\s-]*door"), ("single", r"single[\s-]*door"), ("multi", r"multi[\s-]*door|french[\s-]*door|triple[\s-]*door"))


def _fridge(p: dict, name: str, specs: dict, src: dict) -> None:
    lit = _num(attr(p, "total-capacity", "capacity"), r"l\b|litres?|liters?|ltrs?")
    _put(specs, src, "capacity_l", _clean_num(lit), "attributes.total-capacity")
    _put(specs, src, "capacity_l", _clean_num(_num(name, r"l\b|litres?|liters?|ltrs?")), "name")
    _put(specs, src, "star_rating", _clean_num(_num(attr(p, "bee-star-rating"), r"star")), "attributes.bee-star-rating")
    _put(specs, src, "star_rating", _clean_num(_num(name, r"star")), "name")
    style = " ".join(x for x in (attr(p, "refrigerator-style"), attr(p, "freezer-style")) if x)
    for label, rx in DOOR:
        if style and re.search(rx, style, re.I):
            _put(specs, src, "door", label, "attributes.refrigerator-style")
            break
        if re.search(rx, name, re.I):
            _put(specs, src, "door", label, "name")
            break
    ft = attr(p, "refrigerator-type") or ""
    if re.search(r"frost\s*free", ft, re.I):
        _put(specs, src, "frost_free", True, "attributes.refrigerator-type")
    elif re.search(r"direct\s*cool", ft, re.I):
        _put(specs, src, "frost_free", False, "attributes.refrigerator-type")
    elif re.search(r"frost\s*free", name, re.I):
        _put(specs, src, "frost_free", True, "name")
    elif re.search(r"direct\s*cool", name, re.I):
        _put(specs, src, "frost_free", False, "name")
    if re.search(r"convertible", name, re.I):
        _put(specs, src, "convertible", True, "name")
    if re.search(r"inverter", name, re.I):
        _put(specs, src, "inverter_compressor", True, "name")


def _laptop(p: dict, name: str, specs: dict, src: dict) -> None:
    _put(specs, src, "ram_gb", _clean_num(_num(attr(p, "ram", "ram_size"), r"gb")), "attributes.ram")
    for k in ("internal-storage", "hard_drive", "storage", "ssd_capacity"):
        v = attr(p, k)
        gb = _num(v, r"gb") or ((_num(v, r"tb") or 0) * 1024 or None)
        _put(specs, src, "storage_gb", _clean_num(gb), f"attributes.{k}")
    _put(specs, src, "cpu", attr(p, "processor", "processor_name"), "attributes.processor")
    size, s = _inches(p, "screen_size_diagonal", "screen-size")
    _put(specs, src, "screen_in", _clean_num(size), s)
    _put(specs, src, "os", attr(p, "operating_system"), "attributes.operating_system")
    gb, gs, gm = attr(p, "graphics_card_brand"), attr(p, "graphics_card_sub_brand"), attr(p, "graphics_card_model")
    if gb or gm:
        dedicated = bool(re.search(r"nvidia|geforce|rtx|gtx|radeon rx|arc a", f"{gb} {gm}", re.I))
        _put(specs, src, "gpu", "dedicated" if dedicated else "none", "attributes.graphics_card_brand/model")
        parts = [x for x in (gb, gs) if x and x.lower() not in (gm or "").lower()]  # no "Intel Intel Arc"
        _put(specs, src, "gpu_model", " ".join(parts + ([gm] if gm else [])), "attributes.graphics_card_*")
    _put(specs, src, "battery_h", _clean_num(_num(attr(p, "battery_run_time", "battery_life"), r"hours?|hrs?")),
         "attributes.battery_run_time")
    w = _num(attr(p, "weight", "item_weight", "product-weight"), r"kg")
    _put(specs, src, "weight_kg", _clean_num(w), "attributes.weight")
    _put(specs, src, "laptop_type", attr(p, "laptop_type"), "attributes.laptop_type")


BY_CATEGORY = {"tv": _tv, "ac": _ac, "wm": _wm, "fridge": _fridge, "laptop": _laptop}


def _dims(p: dict) -> tuple[dict, dict]:
    """width/height/depth in mm from the product's item-width / item-height / item-length (cm)."""
    dims, src = {}, {}
    for key, a in (("width_mm", "item-width"), ("height_mm", "item-height"), ("depth_mm", "item-length")):
        v = attr(p, a)
        try:
            cm = float(v) if v is not None else None
        except ValueError:
            cm = None
        if cm and cm > 0:
            dims[key] = round(cm * 10)
            src[key] = f"attributes.{a}"
    return dims, src


def model_number(p: dict) -> str | None:
    m = attr(p, "model", "model_number", "model-number")
    if m:
        return m
    tail = (p.get("name") or "").rsplit(",", 1)
    if len(tail) == 2:
        t = tail[1].strip()
        if re.fullmatch(r"[A-Z0-9][A-Z0-9/\-.]{4,}", t) and re.search(r"\d", t):
            return t
    return None


def short_name(brand: str, specs: dict, mn: str | None, name: str) -> str:
    """A short display name from backend fields only: brand + stated size + model number (or the name itself)."""
    size = specs.get("size_in") or specs.get("screen_in")
    if not mn:
        return name  # nothing short and specific in the data: the full name
    parts = [brand] + ([f'{_clean_num(size)}"'] if size else []) + [mn]
    return " ".join(parts)


def price(p: dict, kind: str) -> int | None:
    v = ((p.get("price") or {}).get(kind) or {}).get("min")
    if v is None:
        return None
    return int(v) if float(v).is_integer() else int(round(float(v)))


def map_product(p: dict, category: str) -> dict | None:
    """Rows for Product (+ price fields) from one getProducts item; None without a selling price."""
    eff, mrp = price(p, "effective"), price(p, "marked")
    if eff is None:
        return None
    name = (p.get("name") or "").strip()
    brand = ((p.get("brand") or {}).get("name") or "").strip()
    specs, src = {}, {}
    BY_CATEGORY[category](p, name, specs, src)
    dims, dsrc = _dims(p)
    src.update(dsrc)
    mn = model_number(p)
    feats = list(p.get("highlights") or []) or _strip_html(attr(p, "features") or attr(p, "key-features") or "")
    sku = str(p.get("item_code") or p.get("uid"))
    medias = [m.get("url") for m in p.get("medias") or [] if (m.get("type") or "image") == "image" and m.get("url")
              and PLACEHOLDER not in m.get("url")]  # the generic category picture is not this product
    info = {
        "source": "jio", "slug": p.get("slug"), "uid": p.get("uid"), "name": name, "model_number": mn,
        "warranty_text": attr(p, "warranty"), "colours": [c for c in [attr(p, "primary_color")] if c],
        "features": [{"hi": f, "mr": f, "en": f} for f in feats[:8]], "spec_source": src,
        "categories": [c.get("name") for c in p.get("categories") or []], "sellable": bool(p.get("sellable")),
        "teaser_tag": p.get("teaser_tag") if isinstance(p.get("teaser_tag"), str) else None,
    }
    return {
        "sku": sku, "category": category, "brand": brand, "model": short_name(brand, specs, mn, name),
        "title": {"hi": name, "mr": name, "en": name}, "specs": specs, "dims": dims, "energy": {},
        "image": medias[0] if medias else None, "info": info, "price": eff, "mrp": mrp if mrp is not None else eff,
        "sellable": bool(p.get("sellable")),
    }


# ---------------------------------------------------------------- resQ plans (catalog items) → protection plans
def map_protection(p: dict, category_by_label: dict[str, str]) -> dict | None:
    """A resQ plan: its own price, the product category it covers and its years, from the item's own fields."""
    eff = price(p, "effective")
    name = (p.get("name") or "").strip()
    covered = (attr(p, "fg-product-category") or "").strip().lower()
    cat = category_by_label.get(covered)
    years = _num(name, r"years?")
    if eff is None or not cat or not years:
        return None
    if re.search(r"renewal", name, re.I):
        return None  # renewals are for existing plans, not a new purchase
    def rupees(v: str | None) -> float | None:
        m = re.search(r"\d[\d,]*(?:\.\d+)?", v or "")
        return float(m.group(0).replace(",", "")) if m else None

    lo, hi = rupees(attr(p, "from-price")), rupees(attr(p, "to-price"))
    band = re.search(r"from\s*rs\.?\s*([\d,]+)\s*to\s*rs\.?\s*([\d,]+)", name, re.I)  # stated in the plan's name
    if band and lo is None and hi is None:
        lo, hi = rupees(band.group(1)), rupees(band.group(2))
    flt = {}
    if lo is not None:
        flt["price_min"] = lo
    if hi is not None:
        flt["price_max"] = hi
    return {"code": str(p.get("item_code") or p.get("uid")), "category": cat, "years": int(years), "price": eff,
            "title": {"hi": name, "mr": name, "en": name}, "filter": flt, "suggest_if": {}, "reason": {}}


# ---------------------------------------------------------------- getPromotionOffers → offers
def promo_amount(promo: dict) -> tuple[float | None, int | None, int | None]:
    """(percent, flat amount, cap) only when a discount rule states it plainly; else (None, None, None)."""
    for rule in promo.get("discount_rules") or []:
        off = rule.get("offer") or {}
        dtype = str(rule.get("discount_type") or rule.get("type") or "").lower()
        pct, amt, cap = off.get("discount_percentage"), off.get("discount_amount"), off.get("max_discount_amount")
        if dtype in ("percentage", "percent") and isinstance(pct, (int, float)) and pct > 0:
            return float(pct), None, int(cap) if isinstance(cap, (int, float)) and cap > 0 else None
        if dtype in ("amount", "flat", "absolute") and isinstance(amt, (int, float)) and amt > 0:
            return None, int(amt), None
    return None, None, None


def map_promotions(promos_by_sku: dict[str, list[dict]], category_by_sku: dict[str, str], today: date) -> list[dict]:
    """One Offer row per promotion id, applying to the SKUs the backend returned it for."""
    rows: dict[str, dict] = {}
    for sku, promos in promos_by_sku.items():
        for pr in promos:
            pid = str(pr.get("id") or "")
            text = (pr.get("offer_text") or pr.get("promotion_name") or "").strip()
            if not pid or not text:
                continue
            till = pr.get("valid_till")
            try:
                valid_to = date.fromisoformat(str(till)[:10]) if till else None
            except ValueError:
                valid_to = None
            if valid_to is None or valid_to < today:
                continue
            row = rows.get(pid)
            if row is None:
                pct, flat, cap = promo_amount(pr)
                row = rows[pid] = {
                    "id": f"JIO-{pid}", "kind": "instant", "title": {"hi": text, "mr": text, "en": text},
                    "bank": None, "pct": pct, "flat": flat, "max_discount": cap, "min_amount": 0, "categories": [],
                    "filter": {"skus": []}, "requires": {}, "stack_group": f"jio:{pr.get('promotion_group') or 'promo'}",
                    "valid_from": today, "valid_to": valid_to, "with_emi": True,
                }
            row["filter"]["skus"].append(sku)
            if category_by_sku.get(sku) and category_by_sku[sku] not in row["categories"]:
                row["categories"].append(category_by_sku[sku])
    return list(rows.values())
