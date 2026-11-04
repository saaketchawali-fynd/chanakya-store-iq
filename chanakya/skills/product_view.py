"""Product facts for cards and answers, straight from the database (every number via the registry)."""

from __future__ import annotations

from chanakya.i18n.lang import pick

__all__ = ["pick"]
from chanakya.skills.base import SkillContext

YES = {"hi": "हाँ", "mr": "हो", "en": "Yes"}
NO = {"hi": "नहीं", "mr": "नाही", "en": "No"}
YES_A = {"hi": "Haan", "mr": "हो", "en": "Yes"}  # associate-facing Hindi is Roman
NO_A = {"hi": "Nahi", "mr": "नाही", "en": "No"}

# field id → (label {hi, mr, en}, unit)
# Spec names, units, door types: English in every language (the user's rule: units and technical terms are
# never translated). The dicts stay per language so a term can be localised later if ever needed.
LABELS: dict[str, tuple[dict, str]] = {
    "size_in": ({"hi": "Size", "mr": "Size", "en": "Size"}, "inch"),
    "panel": ({"hi": "Panel", "mr": "Panel", "en": "Panel"}, ""),
    "resolution": ({"hi": "Resolution", "mr": "Resolution", "en": "Resolution"}, ""),
    "refresh_hz": ({"hi": "Refresh rate", "mr": "Refresh rate", "en": "Refresh rate"}, "Hz"),
    "brightness_nits": ({"hi": "Brightness", "mr": "Brightness", "en": "Brightness"}, "nits"),
    "anti_glare": ({"hi": "Anti-glare", "mr": "Anti-glare", "en": "Anti-glare"}, ""),
    "speaker_w": ({"hi": "Speakers", "mr": "Speakers", "en": "Speakers"}, "W"),
    "dolby_atmos": ({"hi": "Dolby Atmos", "mr": "Dolby Atmos", "en": "Dolby Atmos"}, ""),
    "dialogue_enhance": ({"hi": "Dialogue enhance", "mr": "Dialogue enhance", "en": "Dialogue enhance"}, ""),
    "wide_angle": ({"hi": "Wide viewing angle", "mr": "Wide viewing angle", "en": "Wide viewing angle"}, ""),
    "apps": ({"hi": "Apps", "mr": "Apps", "en": "Apps"}, ""),
    "hdmi_ports": ({"hi": "HDMI ports", "mr": "HDMI ports", "en": "HDMI ports"}, ""),
    "hdmi_arc": ({"hi": "HDMI ARC", "mr": "HDMI ARC", "en": "HDMI ARC"}, ""),
    "usb_ports": ({"hi": "USB ports", "mr": "USB ports", "en": "USB ports"}, ""),
    "hdr_formats": ({"hi": "HDR", "mr": "HDR", "en": "HDR"}, ""),
    "os": ({"hi": "Operating system", "mr": "Operating system", "en": "Operating system"}, ""),
    "bluetooth": ({"hi": "Bluetooth", "mr": "Bluetooth", "en": "Bluetooth"}, ""),
    "wifi": ({"hi": "Wi-Fi", "mr": "Wi-Fi", "en": "Wi-Fi"}, ""),
    "voice_assistant": ({"hi": "Voice assistant", "mr": "Voice assistant", "en": "Voice assistant"}, ""),
    "ton": ({"hi": "Tonnage", "mr": "Tonnage", "en": "Tonnage"}, "ton"),
    "star": ({"hi": "Star rating", "mr": "Star rating", "en": "Star rating"}, "star"),
    "inverter": ({"hi": "Inverter", "mr": "Inverter", "en": "Inverter"}, ""),
    "noise_db": ({"hi": "Noise", "mr": "Noise", "en": "Noise"}, "dB"),
    "refrigerant": ({"hi": "Refrigerant", "mr": "Refrigerant", "en": "Refrigerant"}, ""),
    "cooling_capacity_w": ({"hi": "Cooling capacity", "mr": "Cooling capacity", "en": "Cooling capacity"}, "W"),
    "iseer": ({"hi": "ISEER", "mr": "ISEER", "en": "ISEER"}, ""),
    "copper_condenser": ({"hi": "Copper condenser", "mr": "Copper condenser", "en": "Copper condenser"}, ""),
    "capacity_kg": ({"hi": "Capacity", "mr": "Capacity", "en": "Capacity"}, "kg"),
    "load": ({"hi": "Loading", "mr": "Loading", "en": "Loading"}, ""),
    "heater": ({"hi": "Heater", "mr": "Heater", "en": "Heater"}, ""),
    "rpm": ({"hi": "Spin speed", "mr": "Spin speed", "en": "Spin speed"}, "RPM"),
    "quick_wash": ({"hi": "Quick wash", "mr": "Quick wash", "en": "Quick wash"}, ""),
    "steam": ({"hi": "Steam", "mr": "Steam", "en": "Steam"}, ""),
    "capacity_l": ({"hi": "Capacity", "mr": "Capacity", "en": "Capacity"}, "L"),
    "door": ({"hi": "Door type", "mr": "Door type", "en": "Door type"}, ""),
    "star_rating": ({"hi": "Star rating", "mr": "Star rating", "en": "Star rating"}, "star"),
    "inverter_compressor": ({"hi": "Inverter compressor", "mr": "Inverter compressor", "en": "Inverter compressor"}, ""),
    "stabiliser_free": ({"hi": "Stabiliser-free", "mr": "Stabiliser-free", "en": "Stabiliser-free"}, ""),
    "frost_free": ({"hi": "Frost-free", "mr": "Frost-free", "en": "Frost-free"}, ""),
    "convertible": ({"hi": "Convertible", "mr": "Convertible", "en": "Convertible"}, ""),
    "freezer_l": ({"hi": "Freezer", "mr": "Freezer", "en": "Freezer"}, "L"),
    "crisper_l": ({"hi": "Vegetable box", "mr": "Vegetable box", "en": "Vegetable box"}, "L"),
    "cooling_retention_h": ({"hi": "Cooling in a power cut", "mr": "Cooling in a power cut", "en": "Cooling in a power cut"}, "h"),
    "water_dispenser": ({"hi": "Water dispenser", "mr": "Water dispenser", "en": "Water dispenser"}, ""),
    "cpu": ({"hi": "Processor", "mr": "Processor", "en": "Processor"}, ""),
    "cpu_tier": ({"hi": "Processor class", "mr": "Processor class", "en": "Processor class"}, ""),
    "ram_gb": ({"hi": "RAM", "mr": "RAM", "en": "RAM"}, "GB"),
    "storage_gb": ({"hi": "Storage", "mr": "Storage", "en": "Storage"}, "GB"),
    "gpu": ({"hi": "Graphics", "mr": "Graphics", "en": "Graphics"}, ""),
    "gpu_model": ({"hi": "Graphics card", "mr": "Graphics card", "en": "Graphics card"}, ""),
    "battery_h": ({"hi": "Battery", "mr": "Battery", "en": "Battery"}, "h"),
    "screen_in": ({"hi": "Screen", "mr": "Screen", "en": "Screen"}, "inch"),
    "weight_kg": ({"hi": "Weight", "mr": "Weight", "en": "Weight"}, "kg"),
    "width_mm": ({"hi": "Width", "mr": "Width", "en": "Width"}, "mm"),
    "height_mm": ({"hi": "Height", "mr": "Height", "en": "Height"}, "mm"),
    "depth_mm": ({"hi": "Depth", "mr": "Depth", "en": "Depth"}, "mm"),
    "vesa": ({"hi": "VESA mount", "mr": "VESA mount", "en": "VESA mount"}, ""),
    "kwh_year": ({"hi": "Energy use", "mr": "Energy use", "en": "Energy use"}, "kWh/year"),
    "warranty": ({"hi": "Warranty", "mr": "Warranty", "en": "Warranty"}, ""),
    "colours": ({"hi": "Colours", "mr": "Colours", "en": "Colours"}, ""),
    "in_box": ({"hi": "In the box", "mr": "In the box", "en": "In the box"}, ""),
    "model_number": ({"hi": "Model number", "mr": "Model number", "en": "Model number"}, ""),
    "year": ({"hi": "Launch year", "mr": "Launch year", "en": "Launch year"}, ""),
    "series": ({"hi": "Series", "mr": "Series", "en": "Series"}, ""),
}
KEY_SPECS = {  # the ones the live backend carries are in each list too (rows with no data on either side are skipped)
    "tv": ["size_in", "panel", "resolution", "refresh_hz", "brightness_nits", "hdr_formats", "os", "speaker_w", "wifi",
           "warranty"],
    "ac": ["ton", "star", "inverter", "noise_db", "kwh_year", "wifi", "warranty"],
    "wm": ["capacity_kg", "load", "rpm", "heater", "kwh_year", "warranty"],
    "fridge": ["capacity_l", "door", "frost_free", "star_rating", "inverter_compressor", "freezer_l", "convertible",
               "stabiliser_free", "warranty"],
    "laptop": ["cpu", "ram_gb", "storage_gb", "battery_h", "weight_kg", "warranty"],
    "soundbar": ["speaker_w", "channels", "dialogue_enhance"],
}
UNIT_WORD = {
    "inch": {"hi": "inch", "mr": "inch", "en": "inch"},
    "ton": {"hi": "ton", "mr": "ton", "en": "ton"},
    "star": {"hi": "star", "mr": "star", "en": "star"},
    "h": {"hi": "h", "mr": "h", "en": "h"},
}
DOOR = {
    "single": {"hi": "Single door", "mr": "Single door", "en": "Single door"},
    "double": {"hi": "Double door", "mr": "Double door", "en": "Double door"},
    "bottom_freezer": {"hi": "Bottom freezer", "mr": "Bottom freezer", "en": "Bottom freezer"},
    "side_by_side": {"hi": "Side-by-side", "mr": "Side-by-side", "en": "Side-by-side"},
}
TIER = {
    "entry": {"hi": "Entry", "mr": "Entry", "en": "Entry"},
    "mid": {"hi": "Mid", "mr": "Mid", "en": "Mid"},
    "high": {"hi": "High", "mr": "High", "en": "High"},
}


def _l(lang: str, customer: bool) -> str:
    """Associate-facing Hindi is Roman Hinglish: product words stay English there."""
    return "en" if (lang == "hi" and not customer) else lang


def label(key: str, lang: str, customer: bool = True) -> str:
    lab = LABELS.get(key)
    return pick(lab[0], _l(lang, customer)) if lab else key.replace("_", " ").capitalize()


def raw_value(p, key: str):
    if key in ("warranty", "colours", "in_box", "model_number", "year", "series"):
        return (p.info or {}).get(key)
    return p.spec(key)


def fmt_value(ctx: SkillContext, p, key: str, lang: str, customer: bool = True) -> str | None:
    """A data value as display text; numbers registered with their data source. None if not in data."""
    v = raw_value(p, key)
    src = f"spec:{p.sku}:{key}"
    if v is None or v == "" or v == []:
        return None
    word_lang = _l(lang, customer)
    unit = LABELS.get(key, ({}, ""))[1]
    if isinstance(v, bool):
        return pick((YES if customer else YES_A) if v else (NO if customer else NO_A), lang)
    if key == "warranty" and isinstance(v, dict):
        parts = []
        if v.get("years") is not None:
            parts.append(ctx.reg.num(v["years"], src) + " " + "yr")
        for k, name in (
            ("panel_years", {"hi": "panel", "mr": "panel", "en": "panel"}),
            ("compressor_years", {"hi": "compressor", "mr": "compressor", "en": "compressor"}),
            ("motor_years", {"hi": "motor", "mr": "motor", "en": "motor"}),
            ("battery_years", {"hi": "battery", "mr": "battery", "en": "battery"}),
        ):
            if v.get(k) is not None:
                parts.append(f"{name[word_lang]} {ctx.reg.num(v[k], src)}")
        return " · ".join(parts) or None
    if key == "in_box" and isinstance(v, list):
        return ctx.reg.raw(", ".join(pick(x, word_lang) if isinstance(x, dict) else str(x) for x in v), src)
    if key == "cpu_tier":
        return pick(TIER.get(str(v), {"en": str(v)}), word_lang)
    if key == "door":
        return ctx.reg.static(pick(DOOR.get(str(v), {"en": str(v)}), word_lang))
    if isinstance(v, list):
        return ctx.reg.raw(", ".join(map(str, v)), src)
    if isinstance(v, (int, float)):
        u = pick(UNIT_WORD[unit], word_lang) if unit in UNIT_WORD else unit
        return ctx.reg.num(v, src, u)
    return ctx.reg.raw(str(v), src)


def key_specs(ctx: SkillContext, p, lang: str, n: int = 4) -> list[dict]:
    out = []
    for key in KEY_SPECS.get(p.category, []):
        val = fmt_value(ctx, p, key, lang)
        if val:
            out.append({"label": ctx.reg.static(label(key, lang)), "value": val})
        if len(out) >= n:
            break
    return out


def spec_strings(ctx: SkillContext, p, lang: str, n: int = 3) -> list[str]:
    out = []
    for key in KEY_SPECS.get(p.category, []):
        v = raw_value(p, key)
        if isinstance(v, bool):
            if v:
                out.append(ctx.reg.static(label(key, lang, customer=False)))
        else:
            val = fmt_value(ctx, p, key, lang, customer=False)
            if val:
                out.append(val)
        if len(out) >= n:
            break
    return out


def dims_text(ctx: SkillContext, p) -> str | None:
    w, h = p.spec("width_mm"), p.spec("height_mm")
    if not w or not h:
        return None
    src = f"spec:{p.sku}:dims"
    return f"{ctx.reg.num(round(w / 10), src)} × {ctx.reg.num(round(h / 10), src)} cm"


def features(ctx: SkillContext, p, lang: str, n: int = 3) -> list[str]:
    out = [ctx.reg.static(pick(f, lang)) for f in (p.info or {}).get("features", [])[:n] if f]
    return out or spec_strings(ctx, p, lang, n)


def live_offers_for(ctx: SkillContext, p) -> list[tuple]:
    """Every live offer from the data that applies to this product (bank, cashback and exchange offers included;
    bundles that need a second product are not), the biggest saving first. [(offer, saving in ₹)]"""
    from chanakya.core.clock import today
    from chanakya.truth.offers import discount

    d = today()
    out = []
    for o in ctx.facts.snap.offers:
        if not (o.valid_from <= d <= o.valid_to) or p.category not in (o.categories or []):
            continue
        flt = o.filter or {}
        if any(k.startswith("with_tv") for k in flt):
            continue
        if (flt.get("skus") and p.sku not in flt["skus"]) or (flt.get("brands") and p.brand not in flt["brands"]):
            continue
        size = p.spec("size_in")
        if ("size_in_min" in flt and (size is None or size < flt["size_in_min"])) or (
            "size_in_max" in flt and (size is None or size > flt["size_in_max"])
        ):
            continue
        if (o.min_amount or 0) > p.price:
            continue
        out.append((o, discount(o, p.price)))
    return sorted(out, key=lambda x: (-x[1], x[0].valid_to, x[0].id))


def offer_line(ctx: SkillContext, p, lang: str) -> dict:
    """The offer shown with a product, always: the biggest live offer and its last day (+N more), or plainly that
    none is live today. From the offer rows only."""
    from chanakya.truth import fmt

    offs = live_offers_for(ctx, p)
    if not offs:
        return {"text": ctx.t("offer_none", lang), "none": True, "more": None}
    o = offs[0][0]
    till = ctx.t("brief_offer_till", lang, date=ctx.reg.raw(fmt.date_short(o.valid_to, lang), f"offer:{o.id}:valid_to"))
    more = ctx.t("offer_more", lang, n=ctx.reg.num(len(offs) - 1, f"offers:{p.sku}:count")) if len(offs) > 1 else None
    return {"text": f"{ctx.pick(o.title, lang)} · {till}", "none": False, "more": more}
