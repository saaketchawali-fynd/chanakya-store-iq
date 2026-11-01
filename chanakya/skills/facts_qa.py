"""Product questions answered ONLY from the database ("Meru 55 mein Dolby Atmos hai?", "warranty kitni hai?").

The model (when live) only maps the question to (sku, field); the answer text is built from that data field by
code. If the field or the product is not in the data, the caller falls back to Ask an expert (not_in_data)."""

from __future__ import annotations

import re

from chanakya.core.buttons import encode
from chanakya.core.coach import info_reply
from chanakya.core.contracts import Reply
from chanakya.skills import product_view as PV
from chanakya.skills.base import SkillContext

FIELD_WORDS: dict[str, list[str]] = {
    "warranty": ["warranty", "वारंटी", "वॉरंटी", "guarantee", "गारंटी", "हमी"],
    "weight_kg": ["weight", "wazan", "vajan", "वज़न", "वजन"],
    "hdmi_ports": ["hdmi"],
    "usb_ports": ["usb"],
    "bluetooth": ["bluetooth", "ब्लूटूथ"],
    "wifi": ["wifi", "wi-fi", "वाईफाई", "वायफाय"],
    "dolby_atmos": ["atmos"],
    "hdr_formats": ["hdr", "dolby vision", "hdr10"],
    "os": ["operating system", "google tv", "webos", "tizen", " os "],
    "refresh_hz": ["hz", "refresh", "रिफ्रेश"],
    "brightness_nits": ["nits", "brightness", "ब्राइटनेस"],
    "speaker_w": ["speaker", "watt", "वॉट", "आवाज़ कितनी"],
    "apps": ["apps", "netflix", "youtube", "prime video", "hotstar", "ऐप", "अ‍ॅप"],
    "colours": ["colour", "color", "रंग"],
    "in_box": ["in the box", "box mein", "बॉक्स", "saath mein kya", "साथ में क्या", "सोबत काय"],
    "panel": ["panel", "पैनल", "पॅनल"],
    "resolution": ["resolution", "4k hai", "8k"],
    "noise_db": ["noise", "awaaz", "आवाज़", "आवाज", " db"],
    "star": ["star rating", "kitne star", "किती स्टार", "कितने स्टार"],
    "inverter": ["inverter", "इन्वर्टर", "इन्व्हर्टर"],
    "refrigerant": ["refrigerant", "gas kaunsi", "r32"],
    "cooling_capacity_w": ["cooling capacity"],
    "capacity_kg": ["capacity", "kitne kg", "kitne kilo", "किती किलो", "कितने किलो", "क्षमता"],
    "rpm": ["rpm", "spin"],
    "heater": ["heater", "हीटर", "garam pani", "गरम पानी", "गरम पाणी"],
    "steam": ["steam", "स्टीम"],
    "ram_gb": ["ram"],
    "storage_gb": ["storage", "ssd", "स्टोरेज"],
    "battery_h": ["battery", "बैटरी", "बॅटरी"],
    "cpu": ["processor", "cpu", "प्रोसेसर"],
    "gpu_model": ["graphics", "gpu", "ग्राफ़िक्स", "ग्राफिक्स"],
    "width_mm": ["width", "chaudai", "chaudaai", "चौड़ाई", "रुंदी"],
    "height_mm": ["height", "oonchai", "ऊँचाई", "उंची"],
    "depth_mm": ["depth", "गहराई", "खोली किती"],
    "model_number": ["model number", "मॉडल नंबर", "मॉडेल नंबर"],
    "year": ["launch", "kab aaya", "कब आया", "kis saal"],
    "kwh_year": ["unit", "बिजली", "वीज", "kwh", "electricity"],
}
QUESTION = re.compile(
    r"\?|\b(kya|kitna|kitni|kitne|kaun|kaunsa|kaisa|does|is it|what|how much|which|hai kya)\b|"
    r"क्या|कितना|कितनी|कितने|कौन|किती|कोणत|आहे का|का\?"
)
PRICE = re.compile(r"price|daam|दाम|किंमत|kitne ka|कितने का|किती रुपये|discount|offer|छूट|सूट|emi", re.I)
LIST_ITEMS = {
    "hdr_formats": ["dolby vision", "hdr10+", "hdr10", "hlg"],
    "apps": ["netflix", "youtube", "prime video", "hotstar", "jiocinema"],
}


def _context_sku(ctx: SkillContext, text: str) -> str | None:
    s = ctx.session
    cat = s.home.category if s else None
    sku = ctx.facts.find_in_text(text, cat) or ctx.facts.find_in_text(text)
    if sku:
        return sku
    if s:
        m = re.search(r"\b(?:option\s*)?([ABC])\b", text)
        if m and s.option_labels.get(m.group(1)):
            return s.option_labels[m.group(1)]
        return s.extra.get("label_sku") or s.chosen_sku or s.option_labels.get("B")
    return None


def _rules_field(text: str) -> str | None:
    low = f" {text.lower()} "
    best = None
    for field, words in FIELD_WORDS.items():
        for w in words:
            if w in low and (best is None or len(w) > best[1]):
                best = (field, len(w))
    return best[0] if best else None


def looks_like_product_question(text: str) -> bool:
    return bool(text) and bool(QUESTION.search(text.lower())) and not PRICE.search(text) and bool(_rules_field(text))


async def answer(ctx: SkillContext, question: str) -> Reply | None:
    """A data-grounded answer, or None when the data does not have it."""
    if not question or PRICE.search(question):
        return None
    sku, field = None, None
    if ctx.ai.live:
        cat = ctx.session.home.category if ctx.session else (ctx.staff.dept if ctx.staff else None)
        prods = [{"sku": p.sku, "model": p.model} for p in ctx.facts.products_sync(cat or "tv")] if cat else []
        prods += [{"sku": s, "model": p.model} for s, p in ctx.facts.snap.products.items() if not cat]
        fields = [{"id": k, "means": PV.label(k, "en")} for k in PV.LABELS]
        fq = await ctx.ai.map_fact_question(question, prods, fields)
        if fq:
            sku = fq.sku if fq.sku in ctx.facts.snap.products else None
            field = fq.field if fq.field in PV.LABELS else None
    field = field or _rules_field(question)
    sku = sku or _context_sku(ctx, question)
    if not sku or not field:
        return None
    p = ctx.facts.product_sync(sku)
    if p is None:
        return None
    value = PV.fmt_value(ctx, p, field, ctx.lang, customer=False)
    if value is None:
        return None
    low = question.lower()
    raw = PV.raw_value(p, field)
    for item in LIST_ITEMS.get(field, []):
        if item in low and isinstance(raw, list):
            has = any(item == str(x).lower() for x in raw)
            yes = PV.pick(PV.YES_A if has else PV.NO_A, ctx.lang)
            value = f"{ctx.reg.raw(item.title(), 'question')}: {yes} ({value})"
            break
    name = ctx.reg.raw(p.model, f"product:{p.sku}")
    ctx.ev("fact_answered", sources=[f"spec:{p.sku}:{field}"], sku=p.sku, field=field)
    body = (
        f"*{name}* · {ctx.reg.static(PV.label(field, ctx.lang, customer=False))}: {value}\n{ctx.t('fact_from_data')}"
    )
    s = ctx.session
    btns = ([ctx.b("b_go", ctx.sid("go")), ctx.b("b_options", ctx.sid("options"))] if s else []) + [
        ctx.b("b_status", encode("g", "status"))
    ]
    return ctx.reply(info_reply(ctx, body, btns))
