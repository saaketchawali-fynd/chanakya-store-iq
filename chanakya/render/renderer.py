"""Card renderer (spec Section 16): Jinja2 HTML → JPEG via a Playwright page pool.

Falls back to a Pillow card if Chromium cannot start or a render fails, so the system never breaks.
"""

from __future__ import annotations

import asyncio
import base64
import io
import mimetypes
import time
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from chanakya.core.contracts import Card

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
STATIC_IMG = HERE / "static" / "img"
TEMPLATE_NAMES = ("home", "benefit", "options", "kharcha", "parivaar", "score", "brief", "digest")
WIDTH = 1080
MAX_HEIGHT = 1350

# ---------- fixed labels (hi for customer cards in Devanagari; associate cards in Roman Hinglish) ----------
LABELS: dict[str, dict[str, dict[str, str]]] = {
    "common": {
        "hi": {"test_data": "TEST DATA"},
        "mr": {"test_data": "TEST DATA"},
        "en": {"test_data": "TEST DATA"},
    },
    "home": {
        "hi": {
            "title": "Ghar Card",
            "facts": "Ghar ki baatein",
            "start_level": "Start level",
            "progress": "Ghar samjha",
            "missing": "Abhi poochna hai",
        },
        "mr": {
            "title": "घर कार्ड",
            "facts": "घराबद्दल",
            "start_level": "सुरुवातीचा स्तर",
            "progress": "घर समजलं",
            "missing": "अजून विचारायचं",
        },
        "en": {
            "title": "Home Card",
            "facts": "About the home",
            "start_level": "Start level",
            "progress": "Home understood",
            "missing": "Still to ask",
        },
    },
    "benefit": {
        "hi": {"title": "आपके घर के लिए", "benefits": "फ़ायदे", "ignore": "इस घर के लिए कम ज़रूरी"},
        "mr": {"title": "तुमच्या घरासाठी", "benefits": "फायदे", "ignore": "या घरासाठी कमी महत्त्वाचं"},
        "en": {"title": "For your home", "benefits": "Benefits", "ignore": "Less important for this home"},
    },
    "options": {
        "hi": {"title": "3 Options", "best": "Best fit", "fit": "Fit", "no_price": "Daam demo ke baad"},
        "mr": {"title": "3 पर्याय", "best": "सर्वोत्तम", "fit": "फिट", "no_price": "किंमत डेमोनंतर"},
        "en": {"title": "3 Options", "best": "Best fit", "fit": "Fit", "no_price": "Price after the demo"},
    },
    "kharcha": {
        "hi": {
            "title": "पूरा खर्चा",
            "product": "प्रोडक्ट",
            "zaroori": "ज़रूरी",
            "sujhav": "सुझाव",
            "optional": "वैकल्पिक",
            "zaroori_total": "ज़रूरी कुल",
            "with_suggestions_total": "सुझाव सहित कुल",
            "exchange": "एक्सचेंज",
            "lifetime": "चलाने का खर्च",
            "note": "बिल = कार्ड · कोई सरप्राइज़ नहीं",
        },
        "mr": {
            "title": "पूर्ण खर्च",
            "product": "उत्पादन",
            "zaroori": "आवश्यक",
            "sujhav": "सुचवलेलं",
            "optional": "पर्यायी",
            "zaroori_total": "आवश्यक एकूण",
            "with_suggestions_total": "सूचनांसह एकूण",
            "exchange": "एक्सचेंज",
            "lifetime": "चालवण्याचा खर्च",
            "note": "बिल = कार्ड · कोणतंही सरप्राइज नाही",
        },
        "en": {
            "title": "Full cost",
            "product": "Product",
            "zaroori": "Required",
            "sujhav": "Suggested",
            "optional": "Optional",
            "zaroori_total": "Required total",
            "with_suggestions_total": "Total with suggestions",
            "exchange": "Exchange",
            "lifetime": "Running cost",
            "note": "Bill = card · no surprises",
        },
    },
    "parivaar": {
        "hi": {
            "reasons": "आपके घर के लिए क्यों",
            "cost": "पूरा खर्चा",
            "offer": "ऑफ़र",
            "finance": "EMI",
            "delivery": "डिलीवरी",
            "from": "आपके साथी",
        },
        "mr": {
            "reasons": "तुमच्या घरासाठी का",
            "cost": "पूर्ण खर्च",
            "offer": "ऑफर",
            "finance": "EMI",
            "delivery": "डिलिव्हरी",
            "from": "तुमचे साथी",
        },
        "en": {
            "reasons": "Why it suits your home",
            "cost": "Full cost",
            "offer": "Offer",
            "finance": "EMI",
            "delivery": "Delivery",
            "from": "Your partner",
        },
    },
    "score": {
        "hi": {"title": "Aaj ki seekh", "steps": "7 steps", "praise": "Best", "tip": "Agli baar"},
        "mr": {"title": "आजचा धडा", "steps": "7 पायऱ्या", "praise": "सर्वोत्तम", "tip": "पुढच्या वेळी"},
        "en": {"title": "Today's lesson", "steps": "7 steps", "praise": "Best", "tip": "Next time"},
    },
    "brief": {
        "hi": {
            "title": "Taiyari",
            "changes": "Aaj ke badlav",
            "offers": "Offers",
            "followups": "Follow-up",
            "focus": "Aaj ka focus",
        },
        "mr": {"title": "तयारी", "changes": "आजचे बदल", "offers": "ऑफर्स", "followups": "फॉलो-अप", "focus": "आजचा फोकस"},
        "en": {
            "title": "Before the shift",
            "changes": "Today's changes",
            "offers": "Offers",
            "followups": "Follow-ups",
            "focus": "Today's focus",
        },
    },
    "digest": {
        "hi": {"title": "Aaj ka Digest", "kpis": "Aaj", "heatmap": "Steps (0–2)", "gaps": "Sabse bada gap"},
        "mr": {"title": "आजचा डायजेस्ट", "kpis": "आज", "heatmap": "पायऱ्या (0–2)", "gaps": "सर्वात मोठी उणीव"},
        "en": {"title": "Today's Digest", "kpis": "Today", "heatmap": "Steps (0–2)", "gaps": "Biggest gap"},
    },
}


def labels_for(template: str, lang: str, override: dict | None = None) -> dict[str, str]:
    lang = lang if lang in ("hi", "mr", "en") else "hi"
    out = dict(LABELS["common"][lang])
    out.update(LABELS.get(template, {}).get(lang, {}))
    if override:
        out.update({k: v for k, v in override.items() if v is not None})
    return out


# ---------- images ----------
def _sniff_mime(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def to_data_uri(img: Any) -> str | None:
    """bytes | data: URI | path relative to render/static/img → data URI (None if unusable)."""
    if not img:
        return None
    try:
        if isinstance(img, (bytes, bytearray)):
            b = bytes(img)
            return f"data:{_sniff_mime(b)};base64," + base64.b64encode(b).decode()
        if isinstance(img, str):
            if img.startswith("data:"):
                return img
            p = Path(img)
            if not p.is_absolute():
                p = STATIC_IMG / img
            if p.is_file():
                b = p.read_bytes()
                mime = mimetypes.guess_type(p.name)[0] or _sniff_mime(b)
                return f"data:{mime};base64," + base64.b64encode(b).decode()
    except Exception:
        return None
    return None


def _prepare(card: Card) -> dict[str, Any]:
    data = dict(card.data or {})
    for key in ("product_image", "image"):
        if key in data:
            data[key] = to_data_uri(data[key])
    if isinstance(data.get("options"), list):
        opts = []
        for o in data["options"]:
            o = dict(o or {})
            o["image"] = to_data_uri(o.get("image"))
            opts.append(o)
        data["options"] = opts
    return data


# ---------- sample data (warm-up, tests) ----------
def sample_data(template: str, lang: str = "hi") -> dict[str, Any]:
    dev = lang in ("hi", "mr")
    if template == "home":
        return {
            "name": "Shah ji",
            "category_label": "TV",
            "rows": [
                {"label": "Doori" if lang == "hi" else ("अंतर" if lang == "mr" else "Distance"), "value": "8-10 ft"},
                {
                    "label": "Roshni" if lang == "hi" else ("प्रकाश" if lang == "mr" else "Light"),
                    "value": "Dhoop" if lang == "hi" else ("ऊन" if lang == "mr" else "Sunny"),
                },
                {
                    "label": "Kaun dekhta" if lang == "hi" else ("कोण पाहतं" if lang == "mr" else "Who watches"),
                    "value": "Papa: cricket · bachche: cartoon",
                },
                {"label": "Deewar" if lang == "hi" else ("भिंत" if lang == "mr" else "Mounting"), "value": "Wall"},
            ],
            "start_level_text": "₹50,000 (set by you)",
            "known": 4,
            "total": 7,
            "missing": ["Elders", "Seating", "Decider"],
        }
    if template == "benefit":
        return {
            "product_title": 'Meru 55" QLED Pro',
            "benefits": [
                {
                    "icon": "🏏",
                    "text": "क्रिकेटमध्ये बॉल स्पष्ट"
                    if lang == "mr"
                    else ("क्रिकेट में बॉल साफ़" if dev else "Sharp ball in cricket"),
                },
                {
                    "icon": "☀️",
                    "text": "उन्हातही स्पष्ट चित्र"
                    if lang == "mr"
                    else ("धूप में भी साफ़ तस्वीर" if dev else "Clear picture in sunlight"),
                },
                {
                    "icon": "📺",
                    "text": "अ‍ॅप्स थेट टीव्हीवर" if lang == "mr" else ("ऐप्स सीधे टीवी पर" if dev else "Apps built in"),
                },
            ],
            "ignore_text": "HDMI ports, OS, speaker watts",
            "product_image": "products/TV-MER-55-QL120.png",
        }
    if template == "options":
        return {
            "options": [
                {
                    "label": "A",
                    "title": "Arka 55 4K",
                    "reason": "Ghar ke liye kaafi, kam kharcha",
                    "fit_str": "63%",
                    "stock_line": "4 in store",
                    "image": "products/TV-ARK-55-4K60.png",
                    "is_best": False,
                },
                {
                    "label": "B",
                    "title": "Meru 55 QLED Pro",
                    "reason": "उन्हातही स्पष्ट" if lang == "mr" else "Dhoop mein bhi saaf",
                    "fit_str": "100%",
                    "stock_line": "3 in store",
                    "image": "products/TV-MER-55-QL120.png",
                    "is_best": True,
                },
                {
                    "label": "C",
                    "title": "Meru 65 QLED Pro",
                    "reason": "Bada screen, 5 saal aage ka",
                    "fit_str": "100%",
                    "stock_line": "2 in store",
                    "image": "products/TV-MER-65-QL120.png",
                    "is_best": False,
                },
            ]
        }
    if template == "kharcha":
        return {
            "customer_name": "Shah ji",
            "product_title": 'Meru 55" QLED Pro',
            "lines": [
                {"tag": "product", "title": 'Meru 55" QLED Pro', "amount_str": "₹62,990", "reason": None},
                {
                    "tag": "zaroori",
                    "title": "भिंतीचा ब्रॅकेट" if lang == "mr" else ("दीवार ब्रैकेट" if dev else "Wall bracket"),
                    "amount_str": "₹1,499",
                    "reason": None,
                },
                {
                    "tag": "zaroori",
                    "title": "इन्स्टॉलेशन (भिंत)"
                    if lang == "mr"
                    else ("इंस्टॉलेशन (दीवार)" if dev else "Installation (wall)"),
                    "amount_str": "₹799",
                    "reason": None,
                },
                {
                    "tag": "sujhav",
                    "title": "resQ 2-year extended warranty",
                    "amount_str": "₹3,499",
                    "reason": "घरात मुलं आहेत, रोज चालेल" if lang == "mr" else "kids at home, daily use",
                },
            ],
            "zaroori_total_str": "₹65,288",
            "with_suggestions_total_str": "₹68,787",
            "exchange_text": None,
            "lifetime_text": None,
            "stamp": "Bill = card · TEST DATA · 1 Nov 18:40",
        }
    if template == "parivaar":
        return {
            "title": "शाह परिवारासाठी" if lang == "mr" else ("शाह परिवार के लिए" if dev else "For the Shah family"),
            "image": "products/TV-MER-55-QL120.png",
            "product_title": 'Meru 55" QLED Pro',
            "reasons": ["क्रिकेटमध्ये बॉल स्पष्ट", "उन्हातही स्पष्ट चित्र", "तुमच्या अंतरासाठी योग्य साइज"]
            if lang == "mr"
            else (
                ["क्रिकेट में बॉल साफ़", "धूप में भी साफ़ तस्वीर", "आपकी दूरी के लिए सही साइज़"]
                if dev
                else ["Sharp ball in cricket", "Clear picture in sunlight", "Right size for your distance"]
            ),
            "cost_text": "₹65,288 (₹68,787 with warranty)",
            "offer_text": "HDFC −₹5,000 (15 Nov)",
            "finance_text": "₹4,833 × 12 · fee ₹299 · total ₹58,295",
            "delivery_text": "Tuesday",
            "associate_name": "Ravi",
            "store_name": "Reliance Digital Juhu (TEST)",
        }
    if template == "score":
        names = ["Validate", "Assess", "Translate", "Demonstrate", "Complete", "Close", "Follow through"]
        return {
            "score_str": "14/14",
            "steps": [{"name": n, "points": 2} for n in names],
            "level_text": "Guide level! 👏",
            "praise": "5 ghar ke sawaal pehle",
            "tip": "Har feature ke saath 'kyunki aapke ghar mein…' jodiye.",
        }
    if template == "brief":
        return {
            "greeting": "Namaste Ravi ji!" if lang != "mr" else "नमस्कार रवी!",
            "niti_line": "दिवसाची सुरुवात तयारीने."
            if lang == "mr"
            else ("दिन की शुरुआत, तैयारी के साथ।" if dev else "Start the day prepared."),
            "changes": [
                {"icon": "✅", "text": "Meru 55 QLED Pro back in stock (3)"},
                {"icon": "⚠️", "text": "Novex 55 OLED out of stock"},
            ],
            "offers": [{"text": "HDFC 10% off · till 15 Nov"}],
            "followups_text": "2 customers may come back (Mehta ji)",
            "focus_text": "Translate (kal 1/2)",
        }
    if template == "digest":
        return {
            "kpis": [
                {"label": "Conversations", "value_str": "6"},
                {"label": "Guide level", "value_str": "2/6"},
                {"label": "Median", "value_str": "9/14"},
                {"label": "Price first: customer", "value_str": "4"},
            ],
            "heatmap": {
                "associates": ["Ravi", "Pooja", "Amit"],
                "steps": ["Val", "Ass", "Tra", "Dem", "Com", "Clo", "Fol"],
                "cells": [[2, 2, 1, 2, 2, 2, 1], [1, 2, 0, 1, 2, 1, 2], [0, 1, 1, None, 1, 0, 0]],
            },
            "gaps_text": "Ravi: Translate 3 din se kam. 45-sec clip bhejein?",
        }
    return {}


# ---------- Pillow fallback ----------
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
    "/System/Library/Fonts/Kohinoor.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int):
    from PIL import ImageFont

    for p in _FONT_CANDIDATES:
        try:
            if Path(p).exists():
                return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _flatten(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):
        return " · ".join(_flatten(x) for x in v.values() if x not in (None, "", [], {}))
    if isinstance(v, (list, tuple)):
        return " · ".join(_flatten(x) for x in v if x not in (None, "", [], {}))
    return str(v)


def _fallback_rows(card: Card) -> tuple[str, list[str]]:
    d = card.data or {}
    tpl = card.template
    lb = labels_for(tpl, card.lang, d.get("labels"))
    title = lb.get("title", tpl.title())
    rows: list[str] = []
    if tpl == "home":
        title = f"{d.get('name') or ''} · {d.get('category_label') or ''}".strip(" ·") or title
        rows += [f"{r.get('label')}: {r.get('value')}" for r in d.get("rows") or []]
        if d.get("start_level_text"):
            rows.append(f"{lb['start_level']}: {d['start_level_text']}")
        rows.append(f"{lb['progress']}: {d.get('known', 0)}/{d.get('total', 0)}")
        if d.get("missing"):
            rows.append(f"{lb['missing']}: " + ", ".join(d["missing"]))
    elif tpl == "benefit":
        title = d.get("product_title") or title
        rows += [f"{b.get('icon', '•')} {b.get('text')}" for b in d.get("benefits") or []]
        if d.get("ignore_text"):
            rows.append(f"{lb['ignore']}: {d['ignore_text']}")
    elif tpl == "options":
        for o in d.get("options") or []:
            star = " ⭐" if o.get("is_best") else ""
            rows.append(f"{o.get('label')}: {o.get('title')}{star} — {o.get('reason')} · {o.get('fit_str') or ''}")
            if o.get("stock_line"):
                rows.append(f"    {o['stock_line']}")
    elif tpl == "kharcha":
        title = f"{lb['title']} · {d.get('product_title') or ''}"
        for ln in d.get("lines") or []:
            tag = lb.get(ln.get("tag") or "", ln.get("tag") or "")
            reason = f" ({ln['reason']})" if ln.get("reason") else ""
            rows.append(f"[{tag}] {ln.get('title')}: {ln.get('amount_str')}{reason}")
        rows.append(f"{lb['zaroori_total']}: {d.get('zaroori_total_str') or ''}")
        if d.get("with_suggestions_total_str"):
            rows.append(f"{lb['with_suggestions_total']}: {d['with_suggestions_total_str']}")
        for k in ("exchange_text", "lifetime_text", "stamp"):
            if d.get(k):
                rows.append(str(d[k]))
    elif tpl == "parivaar":
        title = d.get("title") or title
        rows.append(str(d.get("product_title") or ""))
        rows += [f"✓ {r}" for r in d.get("reasons") or []]
        for k in ("cost_text", "offer_text", "finance_text", "delivery_text"):
            if d.get(k):
                rows.append(str(d[k]))
        rows.append(f"{d.get('associate_name') or ''} · {d.get('store_name') or ''}")
    elif tpl == "score":
        title = f"{lb['title']} · {d.get('score_str') or ''}"
        rows += [
            f"{s.get('name')}: {'●' * int(s.get('points') or 0)}{'○' * (2 - int(s.get('points') or 0))}"
            for s in d.get("steps") or []
        ]
        for k, lab in (("level_text", None), ("praise", lb["praise"]), ("tip", lb["tip"])):
            if d.get(k):
                rows.append(f"{lab}: {d[k]}" if lab else str(d[k]))
    elif tpl == "brief":
        title = d.get("greeting") or title
        if d.get("niti_line"):
            rows.append(str(d["niti_line"]))
        rows += [f"{c.get('icon', '•')} {c.get('text')}" for c in d.get("changes") or []]
        rows += [f"🏷 {o.get('text')}" for o in d.get("offers") or []]
        for k in ("followups_text", "focus_text"):
            if d.get(k):
                rows.append(str(d[k]))
    elif tpl == "digest":
        rows += [f"{k.get('label')}: {k.get('value_str')}" for k in d.get("kpis") or []]
        hm = d.get("heatmap") or {}
        for i, a in enumerate(hm.get("associates") or []):
            cells = (hm.get("cells") or [])[i] if i < len(hm.get("cells") or []) else []
            rows.append(f"{a}: " + " ".join("-" if c is None else str(c) for c in cells))
        if d.get("gaps_text"):
            rows.append(str(d["gaps_text"]))
    else:
        rows += [f"{k}: {_flatten(v)}" for k, v in d.items() if k != "labels"]
    return title, [r for r in rows if r and r.strip()]


_GLYPH_MAP = str.maketrans(
    {
        "·": "-",
        "●": "*",
        "○": "o",
        "⭐": "*",
        "✓": "+",
        "↳": ">",
        "–": "-",
        "—": "-",
        "−": "-",
        "✅": "+",
        "⚠": "!",
        "👏": "",
        "🎯": "",
        "🏷": "",
        "🔁": "",
        "⚡": "",
        "🔒": "",
    }
)


def _sanitise(text: str) -> str:
    """Pillow without libraqm has no emoji/symbol coverage: map common symbols, drop the rest (non-BMP)."""
    text = text.translate(_GLYPH_MAP)
    return "".join(ch for ch in text if ord(ch) < 0x10000 and not (0x2600 <= ord(ch) <= 0x27BF) and ord(ch) != 0xFE0F)


def _wrap(draw, text: str, font, width: int) -> list[str]:
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_pillow(card: Card) -> bytes:
    """Plain but clean Pillow card: header band, title, rows, TEST DATA badge. Never raises on data."""
    from PIL import Image, ImageDraw

    title, rows = _fallback_rows(card)
    title, rows = _sanitise(title), [_sanitise(r) for r in rows]
    pad, w = 56, WIDTH
    f_title, f_body, f_small = _font(52), _font(34), _font(26)
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    body_lines: list[str] = []
    for r in rows:
        body_lines += _wrap(scratch, r, f_body, w - 2 * pad - 20)
    title_lines = _wrap(scratch, title, f_title, w - 2 * pad - 260)
    h = 120 + 70 * len(title_lines) + 52 * len(body_lines) + 2 * pad
    h = max(400, min(MAX_HEIGHT, h))
    img = Image.new("RGB", (w, h), "#FAF8F4")
    d = ImageDraw.Draw(img)
    band_h = 40 + 70 * len(title_lines)
    d.rounded_rectangle([0, 0, w, band_h + 28], radius=28, fill="#1B2A4A")
    d.rectangle([0, band_h, w, band_h + 28], fill="#FAF8F4")
    y = 30
    for tl in title_lines:
        d.text((pad, y), tl, font=f_title, fill="white")
        y += 70
    bw = int(d.textlength("TEST DATA", font=f_small)) + 36
    d.rounded_rectangle([w - pad - bw, 24, w - pad, 24 + 46], radius=23, fill="#FDECEC")
    d.text((w - pad - bw + 18, 30), "TEST DATA", font=f_small, fill="#B03A3A")
    y = band_h + 40
    for ln in body_lines:
        if y > h - pad - 40:
            d.text((pad, y), "…", font=f_body, fill="#6E6E6E")
            break
        d.text((pad, y), ln, font=f_body, fill="#333333")
        y += 52
    d.rectangle([pad, h - 14, w - pad, h - 8], fill="#E8833A")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()


# ---------- Playwright renderer ----------
class Renderer:
    def __init__(self, pool_size: int = 2):
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))
        self.pool_size = pool_size
        self.ready = False
        self.pw = None
        self.browser = None
        self.ctx = None
        self.pages: asyncio.Queue | None = None
        self.last_ms: dict[str, int] = {}
        self.start_attempted = False

    async def start(self, warm_up: bool = True) -> None:
        self.start_attempted = True
        try:
            from playwright.async_api import async_playwright

            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            self.ctx = await self.browser.new_context(
                viewport={"width": WIDTH, "height": MAX_HEIGHT}, device_scale_factor=1
            )
            self.pages = asyncio.Queue()
            for _ in range(self.pool_size):
                await self.pages.put(await self.ctx.new_page())
            self.ready = True
        except Exception:
            self.ready = False
            await self._close_quietly()
            return
        if warm_up:
            for name in TEMPLATE_NAMES:
                try:
                    await self._render_html(Card(template=name, lang="hi", data=sample_data(name, "hi")))
                except Exception:
                    pass

    async def _close_quietly(self) -> None:
        for obj in (self.ctx, self.browser):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:
                pass
        try:
            if self.pw is not None:
                await self.pw.stop()
        except Exception:
            pass
        self.pw = self.browser = self.ctx = None

    async def stop(self) -> None:
        self.ready = False
        await self._close_quietly()

    def html(self, card: Card) -> str:
        data = _prepare(card)
        lb = labels_for(card.template, card.lang, data.pop("labels", None))
        from chanakya.i18n.lang import t_for

        return self.env.get_template(f"{card.template}.html").render(
            **data, d=data, lang=card.lang, L=lb, t=t_for(card.lang), template=card.template
        )

    async def _render_html(self, card: Card) -> bytes:
        html = self.html(card)
        page = await self.pages.get()
        try:
            await page.set_content(html, wait_until="load")
            await page.evaluate("document.fonts.ready")
            el = await page.query_selector("#card")  # CSS caps #card at 1350 px (overflow hidden)
            return await el.screenshot(type="jpeg", quality=88)
        finally:
            await self.pages.put(page)

    async def render(self, card: Card) -> bytes:
        t0 = time.perf_counter()
        try:
            if not self.ready:
                return render_pillow(card)
            try:
                return await asyncio.wait_for(self._render_html(card), timeout=8)
            except Exception:
                return render_pillow(card)
        finally:
            self.last_ms[card.template] = int((time.perf_counter() - t0) * 1000)


_singleton: Renderer | None = None
_lock = asyncio.Lock()


def get_renderer() -> Renderer:
    global _singleton
    if _singleton is None:
        _singleton = Renderer()
    return _singleton


async def render_card(card: Card) -> bytes:
    r = get_renderer()
    if not r.start_attempted:
        async with _lock:
            if not r.start_attempted:
                await r.start(warm_up=False)
    return await r.render(card)
