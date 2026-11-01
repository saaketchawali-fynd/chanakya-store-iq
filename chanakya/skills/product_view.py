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
LABELS: dict[str, tuple[dict, str]] = {
    "size_in": ({"hi": "साइज़", "mr": "साइज", "en": "Size"}, "inch"),
    "panel": ({"hi": "पैनल", "mr": "पॅनल", "en": "Panel"}, ""),
    "resolution": ({"hi": "रेज़ोल्यूशन", "mr": "रिझोल्यूशन", "en": "Resolution"}, ""),
    "refresh_hz": ({"hi": "रिफ्रेश रेट", "mr": "रिफ्रेश रेट", "en": "Refresh rate"}, "Hz"),
    "brightness_nits": ({"hi": "ब्राइटनेस", "mr": "ब्राइटनेस", "en": "Brightness"}, "nits"),
    "anti_glare": ({"hi": "एंटी-ग्लेयर", "mr": "अँटी-ग्लेअर", "en": "Anti-glare"}, ""),
    "speaker_w": ({"hi": "स्पीकर", "mr": "स्पीकर", "en": "Speakers"}, "W"),
    "dolby_atmos": ({"hi": "Dolby Atmos", "mr": "Dolby Atmos", "en": "Dolby Atmos"}, ""),
    "dialogue_enhance": ({"hi": "डायलॉग एन्हांस", "mr": "डायलॉग एन्हान्स", "en": "Dialogue enhance"}, ""),
    "wide_angle": ({"hi": "वाइड व्यूइंग एंगल", "mr": "वाइड व्ह्यूइंग अँगल", "en": "Wide viewing angle"}, ""),
    "apps": ({"hi": "ऐप्स", "mr": "अ‍ॅप्स", "en": "Apps"}, ""),
    "hdmi_ports": ({"hi": "HDMI पोर्ट", "mr": "HDMI पोर्ट", "en": "HDMI ports"}, ""),
    "usb_ports": ({"hi": "USB पोर्ट", "mr": "USB पोर्ट", "en": "USB ports"}, ""),
    "hdr_formats": ({"hi": "HDR", "mr": "HDR", "en": "HDR"}, ""),
    "os": ({"hi": "ऑपरेटिंग सिस्टम", "mr": "ऑपरेटिंग सिस्टम", "en": "Operating system"}, ""),
    "bluetooth": ({"hi": "ब्लूटूथ", "mr": "ब्लूटूथ", "en": "Bluetooth"}, ""),
    "wifi": ({"hi": "Wi-Fi", "mr": "Wi-Fi", "en": "Wi-Fi"}, ""),
    "voice_assistant": ({"hi": "वॉइस असिस्टेंट", "mr": "व्हॉइस असिस्टंट", "en": "Voice assistant"}, ""),
    "ton": ({"hi": "टन", "mr": "टन", "en": "Tonnage"}, "ton"),
    "star": ({"hi": "स्टार रेटिंग", "mr": "स्टार रेटिंग", "en": "Star rating"}, "star"),
    "inverter": ({"hi": "इन्वर्टर", "mr": "इन्व्हर्टर", "en": "Inverter"}, ""),
    "noise_db": ({"hi": "आवाज़", "mr": "आवाज", "en": "Noise"}, "dB"),
    "refrigerant": ({"hi": "रेफ्रिजरेंट", "mr": "रेफ्रिजरंट", "en": "Refrigerant"}, ""),
    "cooling_capacity_w": ({"hi": "कूलिंग क्षमता", "mr": "कूलिंग क्षमता", "en": "Cooling capacity"}, "W"),
    "iseer": ({"hi": "ISEER", "mr": "ISEER", "en": "ISEER"}, ""),
    "copper_condenser": ({"hi": "कॉपर कंडेंसर", "mr": "कॉपर कंडेन्सर", "en": "Copper condenser"}, ""),
    "capacity_kg": ({"hi": "क्षमता", "mr": "क्षमता", "en": "Capacity"}, "kg"),
    "load": ({"hi": "लोडिंग", "mr": "लोडिंग", "en": "Loading"}, ""),
    "heater": ({"hi": "हीटर", "mr": "हीटर", "en": "Heater"}, ""),
    "rpm": ({"hi": "स्पिन स्पीड", "mr": "स्पिन स्पीड", "en": "Spin speed"}, "RPM"),
    "quick_wash": ({"hi": "क्विक वॉश", "mr": "क्विक वॉश", "en": "Quick wash"}, ""),
    "steam": ({"hi": "स्टीम", "mr": "स्टीम", "en": "Steam"}, ""),
    "cpu": ({"hi": "प्रोसेसर", "mr": "प्रोसेसर", "en": "Processor"}, ""),
    "cpu_tier": ({"hi": "प्रोसेसर श्रेणी", "mr": "प्रोसेसर श्रेणी", "en": "Processor class"}, ""),
    "ram_gb": ({"hi": "RAM", "mr": "RAM", "en": "RAM"}, "GB"),
    "storage_gb": ({"hi": "स्टोरेज", "mr": "स्टोरेज", "en": "Storage"}, "GB"),
    "gpu": ({"hi": "ग्राफ़िक्स", "mr": "ग्राफिक्स", "en": "Graphics"}, ""),
    "gpu_model": ({"hi": "ग्राफ़िक्स कार्ड", "mr": "ग्राफिक्स कार्ड", "en": "Graphics card"}, ""),
    "battery_h": ({"hi": "बैटरी", "mr": "बॅटरी", "en": "Battery"}, "h"),
    "screen_in": ({"hi": "स्क्रीन", "mr": "स्क्रीन", "en": "Screen"}, "inch"),
    "weight_kg": ({"hi": "वज़न", "mr": "वजन", "en": "Weight"}, "kg"),
    "width_mm": ({"hi": "चौड़ाई", "mr": "रुंदी", "en": "Width"}, "mm"),
    "height_mm": ({"hi": "ऊँचाई", "mr": "उंची", "en": "Height"}, "mm"),
    "depth_mm": ({"hi": "गहराई", "mr": "खोली", "en": "Depth"}, "mm"),
    "vesa": ({"hi": "VESA माउंट", "mr": "VESA माउंट", "en": "VESA mount"}, ""),
    "kwh_year": ({"hi": "बिजली खपत", "mr": "वीज वापर", "en": "Energy use"}, "kWh/year"),
    "warranty": ({"hi": "वारंटी", "mr": "वॉरंटी", "en": "Warranty"}, ""),
    "colours": ({"hi": "रंग", "mr": "रंग", "en": "Colours"}, ""),
    "in_box": ({"hi": "बॉक्स में", "mr": "बॉक्समध्ये", "en": "In the box"}, ""),
    "model_number": ({"hi": "मॉडल नंबर", "mr": "मॉडेल नंबर", "en": "Model number"}, ""),
    "year": ({"hi": "लॉन्च वर्ष", "mr": "लॉन्च वर्ष", "en": "Launch year"}, ""),
    "series": ({"hi": "सीरीज़", "mr": "सिरीज", "en": "Series"}, ""),
}
KEY_SPECS = {
    "tv": ["size_in", "panel", "refresh_hz", "brightness_nits", "speaker_w", "warranty"],
    "ac": ["ton", "star", "noise_db", "kwh_year", "wifi", "warranty"],
    "wm": ["capacity_kg", "load", "rpm", "heater", "kwh_year", "warranty"],
    "laptop": ["cpu", "ram_gb", "storage_gb", "battery_h", "weight_kg", "warranty"],
    "soundbar": ["speaker_w", "channels", "dialogue_enhance"],
}
UNIT_WORD = {
    "inch": {"hi": "इंच", "mr": "इंच", "en": "inch"},
    "ton": {"hi": "टन", "mr": "टन", "en": "ton"},
    "star": {"hi": "स्टार", "mr": "स्टार", "en": "star"},
    "h": {"hi": "घंटे", "mr": "तास", "en": "h"},
}
TIER = {
    "entry": {"hi": "एंट्री", "mr": "एंट्री", "en": "Entry"},
    "mid": {"hi": "मिड", "mr": "मिड", "en": "Mid"},
    "high": {"hi": "हाई", "mr": "हाय", "en": "High"},
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
            parts.append(ctx.reg.num(v["years"], src) + " " + {"hi": "साल", "mr": "वर्षे", "en": "yr"}[word_lang])
        for k, name in (
            ("panel_years", {"hi": "पैनल", "mr": "पॅनल", "en": "panel"}),
            ("compressor_years", {"hi": "कंप्रेसर", "mr": "कॉम्प्रेसर", "en": "compressor"}),
            ("motor_years", {"hi": "मोटर", "mr": "मोटर", "en": "motor"}),
            ("battery_years", {"hi": "बैटरी", "mr": "बॅटरी", "en": "battery"}),
        ):
            if v.get(k) is not None:
                parts.append(f"{name[word_lang]} {ctx.reg.num(v[k], src)}")
        return " · ".join(parts) or None
    if key == "in_box" and isinstance(v, list):
        return ctx.reg.raw(", ".join(pick(x, word_lang) if isinstance(x, dict) else str(x) for x in v), src)
    if key == "cpu_tier":
        return pick(TIER.get(str(v), {"en": str(v)}), word_lang)
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


def offer_badge(ctx: SkillContext, p, lang: str) -> str | None:
    """The best offer that needs no particular bank card (instant / brand / festive), if any applies today."""
    from chanakya.core.clock import today
    from chanakya.truth.offers import discount

    best, amt = None, 0
    for o in ctx.facts.snap.offers:
        if o.bank or o.kind in ("bank", "cashback"):
            continue
        if not (o.valid_from <= today() <= o.valid_to) or p.category not in (o.categories or []):
            continue
        flt = o.filter or {}
        if flt.get("skus") and p.sku not in flt["skus"]:
            continue
        if flt.get("brands") and p.brand not in flt["brands"]:
            continue
        if any(k.startswith("with_tv") for k in flt) or (o.requires or {}).get("slot"):
            continue
        size = p.spec("size_in")
        if ("size_in_min" in flt and (size is None or size < flt["size_in_min"])) or (
            "size_in_max" in flt and (size is None or size > flt["size_in_max"])
        ):
            continue
        if (o.min_amount or 0) > p.price:
            continue
        d = discount(o, p.price)
        if d > amt:
            best, amt = o, d
    return ctx.reg.static(pick(best.title, lang)) if best else None
