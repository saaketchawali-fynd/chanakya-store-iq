"""Card renderer (spec Section 16): Jinja2 HTML → JPEG via a Playwright page pool.

Cards are 1080 px wide, as tall as their content (≤ 1350 px; slightly taller content is scaled to fit,
anything beyond that is clipped). Falls back to a Pillow card if Chromium cannot start or a render fails,
so the system never breaks.
"""

from __future__ import annotations

import asyncio
import base64
import io
import mimetypes
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from chanakya.core.contracts import Card

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
STATIC_IMG = HERE / "static" / "img"
TEMPLATE_NAMES = (
    "home",
    "benefit",
    "options",
    "catalog",
    "product",
    "compare",
    "kharcha",
    "parivaar",
    "score",
    "brief",
    "digest",
)
WIDTH = 1080
MAX_HEIGHT = 1350
MIN_FIT_SCALE = 0.78  # content up to ~1730 px is scaled to fit 1350 (nothing is cut); taller content is clipped

# ---------- fixed labels (hi for customer cards in Devanagari; associate cards in Roman Hinglish) ----------
LABELS: dict[str, dict[str, dict[str, str]]] = {
    "common": {
        "hi": {"test_data": "TEST DATA", "brand": "Chanakya", "brand_sub": "Store IQ"},
        "mr": {"test_data": "TEST DATA", "brand": "Chanakya", "brand_sub": "Store IQ"},
        "en": {"test_data": "TEST DATA", "brand": "Chanakya", "brand_sub": "Store IQ"},
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
        "hi": {"title": "आपके घर के लिए", "benefits": "आपको क्या फ़ायदा", "ignore": "इस घर के लिए कम ज़रूरी"},
        "mr": {"title": "तुमच्या घरासाठी", "benefits": "तुम्हाला काय फायदा", "ignore": "या घरासाठी कमी महत्त्वाचं"},
        "en": {"title": "For your home", "benefits": "What you gain", "ignore": "Less important for this home"},
    },
    "options": {
        "hi": {
            "kicker": "आपके घर के लिए",
            "title": "{n} सही विकल्प",
            "best": "सबसे सही",
            "fit": "घर से मेल",
            "no_price": "कीमत डेमो के बाद",
        },
        "mr": {
            "kicker": "तुमच्या घरासाठी",
            "title": "{n} योग्य पर्याय",
            "best": "सर्वोत्तम",
            "fit": "घराशी जुळणी",
            "no_price": "किंमत डेमोनंतर",
        },
        "en": {
            "kicker": "For your home",
            "title": "{n} picks that fit",
            "best": "Best fit",
            "fit": "home match",
            "no_price": "Price after the demo",
        },
    },
    "catalog": {
        "hi": {
            "kicker": "Catalogue",
            "title": "Products",
            "empty": "Abhi koi product nahi",
            "ask_price": "Daam poochein",
        },
        "mr": {"kicker": "कॅटलॉग", "title": "उत्पादनं", "empty": "सध्या कोणतंही उत्पादन नाही", "ask_price": "किंमत विचारा"},
        "en": {"kicker": "Catalogue", "title": "Products", "empty": "No products yet", "ask_price": "Ask for price"},
    },
    "product": {
        "hi": {
            "kicker": "Product",
            "specs": "Mukhya specs",
            "features": "Khaas baatein",
            "pros": "Achhi baatein",
            "cons": "Dhyan dein",
            "fit": "ghar se match",
        },
        "mr": {
            "kicker": "उत्पादन",
            "specs": "मुख्य स्पेक्स",
            "features": "खास गोष्टी",
            "pros": "चांगल्या गोष्टी",
            "cons": "लक्षात ठेवा",
            "fit": "घराशी जुळणी",
        },
        "en": {
            "kicker": "Product",
            "specs": "Key specs",
            "features": "Highlights",
            "pros": "Good for this home",
            "cons": "Keep in mind",
            "fit": "home match",
        },
    },
    "compare": {
        "hi": {
            "kicker": "Tulna",
            "title": "Kaun behtar?",
            "pros": "Kyun chunein",
            "verdict": "Faisla",
            "price": "Daam",
        },
        "mr": {"kicker": "तुलना", "title": "कोणता चांगला?", "pros": "का निवडावा", "verdict": "निष्कर्ष", "price": "किंमत"},
        "en": {
            "kicker": "Compare",
            "title": "Which one fits?",
            "pros": "Why pick it",
            "verdict": "Verdict",
            "price": "Price",
        },
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
            "kicker": "आपके लिए चुना गया",
            "reasons": "आपके घर के लिए क्यों",
            "specs": "मुख्य बातें",
            "cost": "पूरा खर्चा",
            "offer": "ऑफ़र",
            "finance": "EMI",
            "delivery": "डिलीवरी",
            "from": "आपके साथी",
        },
        "mr": {
            "kicker": "तुमच्यासाठी निवडलेलं",
            "reasons": "तुमच्या घरासाठी का",
            "specs": "मुख्य गोष्टी",
            "cost": "पूर्ण खर्च",
            "offer": "ऑफर",
            "finance": "EMI",
            "delivery": "डिलिव्हरी",
            "from": "तुमचे साथी",
        },
        "en": {
            "kicker": "Picked for you",
            "reasons": "Why it suits your home",
            "specs": "Key specs",
            "cost": "Full cost",
            "offer": "Offer",
            "finance": "EMI",
            "delivery": "Delivery",
            "from": "Your partner",
        },
    },
    "score": {
        "hi": {
            "kicker": "Chanakya · Seekh",
            "title": "Aaj ki seekh",
            "steps": "Steps",
            "praise": "Sabse achha",
            "tip": "Agli baar",
        },
        "mr": {
            "kicker": "चाणक्य · धडा",
            "title": "आजचा धडा",
            "steps": "पायऱ्या",
            "praise": "सर्वोत्तम",
            "tip": "पुढच्या वेळी",
        },
        "en": {
            "kicker": "Chanakya · Review",
            "title": "Today's lesson",
            "steps": "Steps",
            "praise": "Best moment",
            "tip": "Next time",
        },
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
        "hi": {
            "kicker": "Sabha",
            "title": "Aaj ka Digest",
            "kpis": "Aaj",
            "heatmap": "Steps (0–2)",
            "gaps": "Sabse bada gap",
        },
        "mr": {
            "kicker": "सभा",
            "title": "आजचा डायजेस्ट",
            "kpis": "आज",
            "heatmap": "पायऱ्या (0–2)",
            "gaps": "सर्वात मोठी उणीव",
        },
        "en": {
            "kicker": "Huddle",
            "title": "Today's Digest",
            "kpis": "Today",
            "heatmap": "Steps (0–2)",
            "gaps": "Biggest gap",
        },
    },
}


def labels_for(template: str, lang: str, override: dict | None = None) -> dict[str, str]:
    lang = lang if lang in ("hi", "mr", "en") else "hi"
    out = dict(LABELS["common"][lang])
    out.update(LABELS.get(template, {}).get(lang, {}))
    if override:
        out.update({k: v for k, v in override.items() if v is not None})
    return out


# ---------- template helpers (icons, script detection, numbers) ----------
ICONS: dict[str, str] = {
    "check": '<path d="M5 12.5l4.2 4.2L19 7"/>',
    "check-circle": '<circle cx="12" cy="12" r="9.5"/><path d="M7.8 12.3l2.8 2.8 5.6-5.8"/>',
    "star": '<path d="M12 3.2l2.7 5.6 6.1.8-4.5 4.2 1.1 6.1L12 17l-5.4 2.9 1.1-6.1-4.5-4.2 6.1-.8z" fill="currentColor" stroke="none"/>',
    "sparkle": '<path d="M11 3.5l1.9 5.4 5.4 1.9-5.4 1.9L11 18.1l-1.9-5.4-5.4-1.9 5.4-1.9z" fill="currentColor" stroke="none"/>'
    '<path d="M18.5 14.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" fill="currentColor" stroke="none"/>',
    "tag": '<path d="M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3.5 13.5V3.5h10l7.1 7.1a2 2 0 0 1 0 2.8z"/>'
    '<circle cx="8" cy="8" r="1.5" fill="currentColor" stroke="none"/>',
    "card": '<rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M2.5 10h19M6.5 15h4"/>',
    "truck": '<path d="M2.5 6.5h11v9.5h-11zM13.5 9.5h4.2l2.8 3.3V16h-7"/><circle cx="6.5" cy="17.5" r="1.8"/>'
    '<circle cx="17" cy="17.5" r="1.8"/>',
    "shield": '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z"/><path d="M8.8 12l2.2 2.2 4.2-4.4"/>',
    "ruler": '<path d="M3.5 15.5L15.5 3.5l5 5-12 12z"/><path d="M7.5 11.5l2 2M10.5 8.5l2 2M13.5 5.5l2 2"/>',
    "box": '<path d="M3.5 7.5L12 3.2l8.5 4.3v9L12 20.8l-8.5-4.3z"/><path d="M3.5 7.5L12 12l8.5-4.5M12 12v8.8"/>',
    "store": '<path d="M4.5 10.5V20h15v-9.5"/><path d="M3 10l1.8-5.5h14.4L21 10"/>'
    '<path d="M3 10a3 3 0 0 0 6 0 3 3 0 0 0 6 0 3 3 0 0 0 6 0"/><path d="M10 20v-5h4v5"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2.2"/><path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3"/>',
    "bolt": '<path d="M13 2.5L5 13.5h6l-1 8 8-11h-6z"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4v4.5h-4.5"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c0-4 3.4-6.5 7.5-6.5s7.5 2.5 7.5 6.5"/>',
    "users": '<circle cx="9" cy="8.5" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/>'
    '<path d="M15.5 5.2a3.5 3.5 0 0 1 0 6.6M17.5 14.3c2.4.6 4 2.6 4 5.7"/>',
    "home": '<path d="M3.5 11L12 4l8.5 7"/><path d="M5.5 9.5V20h13V9.5"/><path d="M10 20v-5.5h4V20"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5.2l3.2 2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.4"/>',
    "alert": '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.4v.3"/>',
    "x": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "minus-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "rupee": '<path d="M7 4.5h10M7 9h10M7 4.5h3a4.5 4.5 0 0 1 0 9H7l7.5 6.5"/>',
    "trend": '<path d="M3.5 16.5l5.5-5.5 4 4 7.5-7.5"/><path d="M15 7.5h5.5V13"/>',
    "arrow-right": '<path d="M4.5 12h15M13.5 6l6 6-6 6"/>',
    "percent": '<path d="M18.5 5.5l-13 13"/><circle cx="7.5" cy="7.5" r="2.3"/><circle cx="16.5" cy="16.5" r="2.3"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6M12 17.2v.3"/>',
    "list": '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.8" cy="6.5" r="1" fill="currentColor"/>'
    '<circle cx="4.8" cy="12" r="1" fill="currentColor"/><circle cx="4.8" cy="17.5" r="1" fill="currentColor"/>',
    "gift": '<rect x="3.5" y="8" width="17" height="4" rx="1"/><path d="M5 12v8.5h14V12M12 8v12.5"/>'
    '<path d="M12 8C10.5 4.5 6.5 4.5 6.5 6.8 6.5 8 9 8 12 8zM12 8c1.5-3.5 5.5-3.5 5.5-1.2C17.5 8 15 8 12 8z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6'
    'M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/>',
    "speaker": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    "tv": '<rect x="2.5" y="4.5" width="19" height="12.5" rx="2"/><path d="M8 20.5h8M12 17v3.5"/>',
    "snow": '<path d="M12 2.5v19M3.8 7.2l16.4 9.6M3.8 16.8l16.4-9.6"/><path d="M9.5 4.5L12 6.5l2.5-2M9.5 19.5l2.5-2 2.5 2"/>',
    "wifi": '<path d="M2.5 9a14 14 0 0 1 19 0M5.8 12.5a9 9 0 0 1 12.4 0M9 16a4.5 4.5 0 0 1 6 0"/>'
    '<circle cx="12" cy="19.2" r="1.1" fill="currentColor"/>',
    "drop": '<path d="M12 3s6.5 7 6.5 11.3A6.5 6.5 0 0 1 5.5 14.3C5.5 10 12 3 12 3z"/>',
    "motion": '<path d="M3 8h7M2 12h9M3 16h7"/><circle cx="16.5" cy="12" r="4.5"/>',
    "size": '<path d="M14.5 3.5h6v6M9.5 20.5h-6v-6M20.5 3.5l-7 7M3.5 20.5l7-7"/>',
    "voice": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5v3.5"/>',
    "angle": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "film": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    "apps": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.8"/>'
    '<rect x="3.5" y="13.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.8"/>',
    "up": '<path d="M12 19.5v-15M5.5 11L12 4.5l6.5 6.5"/>',
    "down": '<path d="M12 4.5v15M5.5 13l6.5 6.5 6.5-6.5"/>',
    "low": '<path d="M3.5 17.5a8.5 8.5 0 1 1 17 0"/><path d="M12 17.5l-4-4.5"/><circle cx="12" cy="17.5" r="1.3" fill="currentColor"/>',
    "dot": '<circle cx="12" cy="12" r="3.2" fill="currentColor" stroke="none"/>',
    "chat": '<path d="M20.5 12a8.5 8.5 0 0 1-12.3 7.6L3.5 20.5l1-4.4A8.5 8.5 0 1 1 20.5 12z"/>',
}


def icon(name: str, size: int = 28, cls: str = "", stroke: float = 2.0) -> Markup:
    body = ICONS.get(name) or ICONS["info"]
    return Markup(
        f'<svg class="ic i-{name} {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


_DEV = re.compile(r"[ऀ-ॿ]")
_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def is_dev(s: Any) -> bool:
    return bool(_DEV.search(str(s or "")))


def dv_class(s: Any) -> str:
    """' dv' when the text is Devanagari (no letter-spacing/uppercase, taller line-height)."""
    return " dv" if is_dev(s) else ""


def pct(s: Any) -> float | None:
    """'63%' / '६३%' / 63 → 63.0 (clamped 0–100); None when there is no number."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        v = float(s)
    else:
        m = re.search(r"\d+(?:\.\d+)?", str(s).translate(_DIGITS))
        if not m:
            return None
        v = float(m.group())
    return max(0.0, min(100.0, v))


def frac(s: Any) -> float | None:
    """'12/14' → 0.857 (clamped 0–1); None when there is no 'a/b'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", str(s or "").translate(_DIGITS))
    if not m:
        return None
    num, den = float(m.group(1)), float(m.group(2))
    return max(0.0, min(1.0, num / den)) if den > 0 else None


_NO_STOCK = ("out of stock", "khatam", "संपला", "खत्म", "नहीं है", "nahi hai", "not available", "उपलब्ध नाही")
_DELIVERY = ("deliver", "डिलीवरी", "डिलिव्हरी", "दिवसांत", "दिन में", "din mein", "days")


def stock_icon(s: Any) -> str:
    """Icon name for a stock line: in store → store, delivery → truck, none → alert."""
    t = str(s or "").lower()
    if any(w in t for w in _NO_STOCK):
        return "alert"
    if any(w in t for w in _DELIVERY):
        return "truck"
    return "store"


def aslist(v: Any) -> list:
    """Template-safe list: None → [], a lone string/dict → [it], None items dropped."""
    if v is None:
        return []
    if isinstance(v, (str, dict)):
        return [v]
    try:
        return [x for x in v if x is not None]
    except TypeError:
        return []


_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U00002b00-\U00002bff\U0000231a-\U000023ff"
    "\U0000fe0e\U0000fe0f\U0000200d\U000020e3\U000e0020-\U000e007f]"
)
# data icons arrive as emoji (or as icon names); cards draw clean line icons instead
EMOJI_ICON = {
    "✅": "check-circle",
    "✔": "check",
    "☑": "check",
    "⚠": "alert",
    "❗": "alert",
    "❌": "x",
    "🆕": "sparkle",
    "✨": "sparkle",
    "⭐": "star",
    "🌟": "star",
    "🏏": "target",
    "⚽": "target",
    "🎯": "target",
    "☀": "sun",
    "🌞": "sun",
    "🔆": "sun",
    "🔊": "speaker",
    "🔉": "speaker",
    "🎵": "speaker",
    "🎬": "tv",
    "📺": "tv",
    "📱": "wifi",
    "📶": "wifi",
    "🛜": "wifi",
    "❄": "snow",
    "🌡": "snow",
    "💧": "drop",
    "🌊": "drop",
    "👕": "drop",
    "⚡": "bolt",
    "💡": "bolt",
    "🔋": "bolt",
    "🏷": "tag",
    "💰": "rupee",
    "💸": "rupee",
    "₹": "rupee",
    "🔒": "lock",
    "🛡": "shield",
    "🚚": "truck",
    "📦": "box",
    "🏠": "home",
    "🏡": "home",
    "👨": "users",
    "👪": "users",
    "👶": "users",
    "👴": "users",
    "👵": "users",
    "🧒": "users",
    "🕒": "clock",
    "⏱": "clock",
    "⏰": "clock",
    "📅": "calendar",
    "🔁": "refresh",
    "🔄": "refresh",
    "📉": "trend",
    "📈": "trend",
}
# icon keywords the skills send (benefit + brief cards); anything unknown draws a neutral dot
ICON_KEYWORDS = {"motion", "sun", "size", "voice", "angle", "film", "apps", "check", "up", "down", "low"}


def strip_emoji(s: Any) -> Any:
    """Remove emoji / pictographs (cards use SVG line icons); collapses the leftover double spaces."""
    if not isinstance(s, str) or s.startswith("data:"):
        return s
    out = _EMOJI.sub("", s)
    return re.sub(r"[ \t]{2,}", " ", out).strip() if out != s else s


def icon_name(v: Any, default: str = "dot") -> str:
    """Emoji or icon name from data → one of ICONS."""
    t = str(v or "").strip()
    if t.lower() in ICONS:
        return t.lower()
    for ch in t:
        name = EMOJI_ICON.get(ch)
        if name:
            return name
    return default


def _clean(v: Any, key: str = "") -> Any:
    """Recursively strip emoji from card data (icon fields become icon names)."""
    if key == "icon":
        return icon_name(v)
    if isinstance(v, str):
        return strip_emoji(v)
    if isinstance(v, dict):
        return {k: _clean(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_clean(x) for x in v)
    return v


def initial(s: Any) -> str:
    s = str(s or "").strip()
    return s[:1].upper() if s else "·"


# ---------- images ----------
def _sniff_mime(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _downscale(b: bytes, max_px: int) -> tuple[bytes, str] | None:
    """Shrink to max_px on the longest side (WebP keeps alpha and is ~10× smaller than PNG)."""
    try:
        from PIL import Image, features

        im = Image.open(io.BytesIO(b))
        if max(im.size) <= max_px:
            return None
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        out = io.BytesIO()
        if features.check("webp"):
            im.save(out, format="WEBP", quality=90, method=4)
            return out.getvalue(), "image/webp"
        if im.mode in ("RGBA", "LA", "P"):
            im.save(out, format="PNG", compress_level=6)
            return out.getvalue(), "image/png"
        im.convert("RGB").save(out, format="JPEG", quality=88)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return None


@lru_cache(maxsize=192)
def _file_data_uri(path: str, mtime_ns: int, max_px: int | None) -> str:
    p = Path(path)
    b = p.read_bytes()
    mime = mimetypes.guess_type(p.name)[0] or _sniff_mime(b)
    if max_px:
        small = _downscale(b, max_px)
        if small:
            b, mime = small
    return f"data:{mime};base64," + base64.b64encode(b).decode()


def to_data_uri(img: Any, max_px: int | None = None) -> str | None:
    """bytes | data: URI | path relative to render/static/img → data URI (None if unusable).

    Files are cached (keyed by path + mtime) and optionally shrunk to ``max_px`` on the longest side."""
    if not img:
        return None
    try:
        if isinstance(img, (bytes, bytearray)):
            b = bytes(img)
            mime = _sniff_mime(b)
            if max_px:
                small = _downscale(b, max_px)
                if small:
                    b, mime = small
            return f"data:{mime};base64," + base64.b64encode(b).decode()
        if isinstance(img, str):
            if img.startswith("data:"):
                return img
            p = Path(img)
            if not p.is_absolute():
                p = STATIC_IMG / img
            if p.is_file():
                return _file_data_uri(str(p), p.stat().st_mtime_ns, max_px)
    except Exception:
        return None
    return None


_FIELD_LABELS = ("ऑफ़र", "ऑफर", "offer", "डिलीवरी", "डिलिव्हरी", "delivery", "emi", "ईएमआई")


def _strip_label(text: Any) -> Any:
    """'ऑफ़र: HDFC −₹5,000' → 'HDFC −₹5,000' (the card prints its own label). Only known field words are stripped."""
    if not isinstance(text, str):
        return text
    m = re.match(r"^\s*([^:：]{1,16})[:：]\s+", text)
    if m and m.group(1).strip().lower() in _FIELD_LABELS:
        return text[m.end() :] or text
    return text


def _cost_rows_from_text(text: str) -> list[dict[str, Any]]:
    """Legacy cost_text 'पूरा खर्च ₹65,288 · सुझाव सहित ₹68,787' → label/value rows (first one strong)."""
    rows = []
    parts = [p.strip() for p in re.split(r"\s+·\s+|\s*\(|\)\s*", text) if p and p.strip()]
    for i, part in enumerate(parts):
        m = re.search(r"[₹\d]", part)
        label, value = (part[: m.start()].strip(" :"), part[m.start() :].strip()) if m else (part, "")
        rows.append({"label": label, "value": value, "strong": i == 0})
    return rows


def _raw_is_photo(raw: Any) -> bool:
    if isinstance(raw, (bytes, bytearray)):
        return _sniff_mime(bytes(raw[:16])) == "image/jpeg"
    if isinstance(raw, str):
        r = raw.lower()
        return r.startswith("data:image/jpeg") or r.startswith("data:image/jpg") or r.endswith((".jpg", ".jpeg"))
    return False


def _prepare(card: Card) -> dict[str, Any]:
    """Inline images as (cached, right-sized) data URIs and normalise legacy keys."""
    tpl = card.template
    data = _clean(dict(card.data or {}))
    if "product_image" in data:
        data["product_image"] = to_data_uri(data["product_image"], 420 if tpl == "kharcha" else 900)
    if "image" in data:
        raw = data["image"]
        photo = bool(data.get("image_is_photo")) or _raw_is_photo(raw)
        data["image"] = to_data_uri(raw, None if photo else (700 if tpl == "product" else 900))
        data["image_is_photo"] = photo
    if tpl == "parivaar":
        if not data.get("cost_rows") and isinstance(data.get("cost_text"), str) and data["cost_text"].strip():
            data["cost_rows"] = _cost_rows_from_text(data["cost_text"])
        for k in ("offer_text", "finance_text", "delivery_text"):
            data[k] = _strip_label(data.get(k))
    if isinstance(data.get("options"), list):
        opts = []
        for o in data["options"]:
            o = dict(o or {})
            o["image"] = to_data_uri(o.get("image"), 900 if o.get("is_best") else 560)
            opts.append(o)
        data["options"] = opts
    for side in ("a", "b"):
        if isinstance(data.get(side), dict):
            data[side] = dict(data[side])
            data[side]["image"] = to_data_uri(data[side].get("image"), 560)
    if isinstance(data.get("items"), list):
        items = []
        for it in data["items"][:6]:
            it = dict(it or {})
            it["image"] = to_data_uri(it.get("image"), 520)
            items.append(it)
        data["items"] = items
    return data


# ---------- sample data (warm-up, tests, eyeballing) ----------
def _pick(lang: str, hi: Any, mr: Any, en: Any) -> Any:
    return {"hi": hi, "mr": mr}.get(lang, en)


def sample_data(template: str, lang: str = "hi") -> dict[str, Any]:
    L = lang if lang in ("hi", "mr", "en") else "hi"

    def p(hi, mr, en):
        return _pick(L, hi, mr, en)

    if template == "home":
        return {
            "name": p("Shah ji", "शहा साहेब", "Shah ji"),
            "category_label": p("TV", "टीव्ही", "TV"),
            "rows": [
                {"label": p("Doori", "अंतर", "Distance"), "value": p("8–10 ft", "8–10 फूट", "8–10 ft")},
                {"label": p("Roshni", "प्रकाश", "Light"), "value": p("Dhoop aati hai", "ऊन येतं", "Sunny room")},
                {
                    "label": p("Kaun dekhta", "कोण पाहतं", "Who watches"),
                    "value": p(
                        "Papa: cricket · bachche: cartoon", "बाबा: क्रिकेट · मुलं: कार्टून", "Dad: cricket · kids: cartoons"
                    ),
                },
                {
                    "label": p("Deewar", "भिंत", "Mounting"),
                    "value": p("Wall par lagega", "भिंतीवर लावणार", "On the wall"),
                },
            ],
            "start_level_text": p("₹50,000 (aapne set kiya)", "₹50,000 (तुम्ही ठरवलं)", "₹50,000 (set by you)"),
            "known": 4,
            "total": 7,
            "known_str": "4",
            "total_str": "7",
            "missing": p(
                ["Bade-buzurg", "Baithne ki jagah", "Faisla kaun lega"],
                ["घरातील ज्येष्ठ", "बसण्याची जागा", "निर्णय कोण घेणार"],
                ["Elders at home", "Seating", "Who decides"],
            ),
        }
    if template == "benefit":
        return {
            "product_title": 'Meru 55" QLED Pro',
            "benefits": [
                {
                    "icon": "motion",
                    "text": p(
                        "क्रिकेट में तेज़ बॉल भी साफ़ दिखेगी", "क्रिकेटमध्ये वेगवान बॉलही स्पष्ट दिसेल", "Fast cricket balls stay sharp"
                    ),
                },
                {
                    "icon": "sun",
                    "text": p(
                        "धूप वाले कमरे में भी चमकदार तस्वीर",
                        "उन्हाच्या खोलीतही उजळ चित्र",
                        "Bright picture even in a sunny room",
                    ),
                },
                {
                    "icon": "voice",
                    "text": p(
                        "आवाज़ बढ़ाए बिना साफ़ डायलॉग", "आवाज न वाढवता स्पष्ट संवाद", "Clear dialogue without turning it up"
                    ),
                },
            ],
            "ignore_text": p("HDMI पोर्ट, OS, स्पीकर वॉट", "HDMI पोर्ट, OS, स्पीकर वॅट", "HDMI ports, OS, speaker watts"),
            "product_image": "products/TV-MER-55-QL120.png",
        }
    if template == "options":
        stock = p("Store mein {n}", "स्टोअरमध्ये {n}", "{n} in store")
        return {
            "options": [
                {
                    "label": "A",
                    "title": 'Arka 55" 4K',
                    "reason": p("घर के लिए काफ़ी, कम खर्च", "घरासाठी पुरेसा, कमी खर्च", "Enough for your home, lower cost"),
                    "fit_str": "63%",
                    "stock_line": stock.format(n=4),
                    "image": "products/TV-ARK-55-4K60.png",
                    "is_best": False,
                    "features": ["4K", "60 Hz", "20 W"],
                    "dims_text": "124 × 72 cm",
                },
                {
                    "label": "B",
                    "title": 'Meru 55" QLED Pro',
                    "reason": p(
                        "धूप में भी साफ़ तस्वीर, क्रिकेट के लिए 120 Hz",
                        "उन्हातही स्पष्ट चित्र, क्रिकेटसाठी 120 Hz",
                        "Clear even in sunlight, 120 Hz for cricket",
                    ),
                    "fit_str": "100%",
                    "stock_line": stock.format(n=3),
                    "image": "products/TV-MER-55-QL120.png",
                    "is_best": True,
                    "features": p(
                        ["600 nits चमक", "120 Hz", "Dolby Atmos"],
                        ["600 nits उजळपणा", "120 Hz", "Dolby Atmos"],
                        ["600-nit bright", "120 Hz motion", "Dolby Atmos"],
                    ),
                    "dims_text": "123 × 71 cm",
                },
                {
                    "label": "C",
                    "title": 'Meru 65" QLED Pro',
                    "reason": p(
                        "बड़ी स्क्रीन, अगले 5 साल के लिए", "मोठी स्क्रीन, पुढच्या 5 वर्षांसाठी", "Bigger screen, ready for 5 years"
                    ),
                    "fit_str": "100%",
                    "stock_line": stock.format(n=2),
                    "image": "products/TV-MER-65-QL120.png",
                    "is_best": False,
                    "features": ["QLED", "120 Hz", "Dolby Atmos"],
                    "dims_text": "145 × 84 cm",
                },
            ]
        }
    if template == "catalog":
        stock = p("Store mein {n}", "स्टोअरमध्ये {n}", "{n} in store")
        return {
            "title": p("55 inch TV", "55 इंच टीव्ही", "55-inch TVs"),
            "subtitle": p("6 models · Juhu store", "6 मॉडेल · जुहू स्टोअर", "6 models · Juhu store"),
            "items": [
                {
                    "title": 'Arka 55" 4K',
                    "image": "products/TV-ARK-55-4K60.png",
                    "specs": ["4K LED", "60 Hz", "20 W"],
                    "stock_line": stock.format(n=4),
                    "offer_badge": None,
                    "price_str": "₹38,990",
                },
                {
                    "title": 'Meru 55" QLED',
                    "image": "products/TV-MER-55-QL60.png",
                    "specs": ["QLED 4K", "60 Hz", "30 W"],
                    "stock_line": stock.format(n=2),
                    "offer_badge": "HDFC −₹3,000",
                    "price_str": "₹49,990",
                },
                {
                    "title": 'Meru 55" QLED Pro',
                    "image": "products/TV-MER-55-QL120.png",
                    "specs": ["QLED 4K", "120 Hz", "Dolby Atmos"],
                    "stock_line": stock.format(n=3),
                    "offer_badge": p("Exchange +₹4,000", "एक्सचेंज +₹4,000", "Exchange +₹4,000"),
                    "price_str": "₹62,990",
                },
                {
                    "title": 'Novex 55" OLED',
                    "image": "products/TV-NOV-55-OL120.png",
                    "specs": ["OLED 4K", "120 Hz", "Dolby Atmos"],
                    "stock_line": p("3 din mein delivery", "3 दिवसांत डिलिव्हरी", "Delivery in 3 days"),
                    "offer_badge": None,
                    "price_str": "₹89,990",
                },
                {
                    "title": "Meru Soundbar 2.1",
                    "image": "products/SB-MER-21.png",
                    "specs": ["120 W", "2.1 ch", p("Dialogue boost", "संवाद स्पष्ट", "Dialogue boost")],
                    "stock_line": stock.format(n=6),
                    "offer_badge": p("Combo −₹2,000", "कॉम्बो −₹2,000", "Combo −₹2,000"),
                    "price_str": "₹12,990",
                },
                {
                    "title": 'Arka 65" 4K',
                    "image": "products/TV-ARK-65-4K60.png",
                    "specs": ["4K LED", "60 Hz", "20 W"],
                    "stock_line": stock.format(n=1),
                    "offer_badge": None,
                    "price_str": None,
                },
            ],
        }
    if template == "product":
        return {
            "title": 'Meru 55" QLED Pro',
            "image": "products/TV-MER-55-QL120.png",
            "badge": p("Is ghar ke liye sahi", "या घरासाठी योग्य", "Fits this home"),
            "badge_kind": "good",
            "key_specs": [
                {"label": p("Screen", "स्क्रीन", "Screen"), "value": '55" QLED 4K'},
                {"label": p("Chamak", "उजळपणा", "Brightness"), "value": "600 nits"},
                {"label": p("Motion", "मोशन", "Motion"), "value": "120 Hz"},
                {"label": p("Awaaz", "आवाज", "Sound"), "value": "40 W · Dolby Atmos"},
                {"label": "HDMI", "value": p("3 port", "3 पोर्ट", "3 ports")},
                {
                    "label": p("Warranty", "वॉरंटी", "Warranty"),
                    "value": p("1 saal + panel 2 saal", "1 वर्ष + पॅनल 2 वर्षं", "1 yr + panel 2 yrs"),
                },
            ],
            "features": p(
                ["Anti-glare screen", "Dialogue boost", "Netflix, Prime, YouTube", "Wide viewing angle"],
                ["अँटी-ग्लेअर स्क्रीन", "संवाद स्पष्ट", "Netflix, Prime, YouTube", "रुंद व्ह्यूइंग अँगल"],
                ["Anti-glare screen", "Dialogue boost", "Netflix, Prime, YouTube", "Wide viewing angle"],
            ),
            "pros": p(
                ["Dhoop wale kamre mein bhi saaf", "Cricket mein 120 Hz smooth", "9 ft doori ke liye sahi size"],
                ["उन्हाच्या खोलीतही स्पष्ट", "क्रिकेटसाठी 120 Hz स्मूथ", "9 फूट अंतरासाठी योग्य साइज"],
                ["Clear even in a sunny room", "120 Hz keeps cricket smooth", "Right size for a 9 ft distance"],
            ),
            "cons": p(
                ["Budget se ₹12,990 upar", "Wall bracket alag se"],
                ["बजेटपेक्षा ₹12,990 जास्त", "भिंतीचा ब्रॅकेट वेगळा"],
                ["₹12,990 above the start level", "Wall bracket sold separately"],
            ),
            "fit_str": "92%",
            "stock_line": p("Store mein 3", "स्टोअरमध्ये 3", "3 in store"),
            "offer_text": p(
                "HDFC card −₹5,000 (15 Nov tak)", "HDFC कार्ड −₹5,000 (15 Nov पर्यंत)", "HDFC card −₹5,000 (until 15 Nov)"
            ),
            "price_str": "₹62,990",
            "note": p(
                "Label se pehchana · model TV-MER-55-QL120",
                "लेबलवरून ओळखलं · मॉडेल TV-MER-55-QL120",
                "Read from the label · model TV-MER-55-QL120",
            ),
        }
    if template == "compare":
        return {
            "title": p("Arka 55 vs Meru 55 Pro", "Arka 55 वि. Meru 55 Pro", "Arka 55 vs Meru 55 Pro"),
            "a": {
                "title": 'Arka 55" 4K',
                "image": "products/TV-ARK-55-4K60.png",
                "fit_str": "63%",
                "badge_kind": "partial",
            },
            "b": {
                "title": 'Meru 55" QLED Pro',
                "image": "products/TV-MER-55-QL120.png",
                "fit_str": "100%",
                "badge_kind": "good",
            },
            "rows": [
                {"label": p("Panel", "पॅनल", "Panel"), "a": "4K LED", "b": "QLED 4K", "better": "b"},
                {"label": p("Chamak", "उजळपणा", "Brightness"), "a": "350 nits", "b": "600 nits", "better": "b"},
                {"label": p("Motion", "मोशन", "Motion"), "a": "60 Hz", "b": "120 Hz", "better": "b"},
                {"label": p("Awaaz", "आवाज", "Sound"), "a": "20 W", "b": "40 W · Atmos", "better": "b"},
                {"label": "Anti-glare", "a": p("Nahi", "नाही", "No"), "b": p("Haan", "हो", "Yes"), "better": "b"},
                {"label": "HDMI", "a": "2", "b": "3", "better": None},
                {
                    "label": p("Warranty", "वॉरंटी", "Warranty"),
                    "a": p("1 saal", "1 वर्ष", "1 yr"),
                    "b": p("1 saal + panel 2", "1 वर्ष + पॅनल 2", "1 yr + panel 2"),
                    "better": "b",
                },
            ],
            "a_pros": p(
                ["Kam kharcha", "Andhere kamre ke liye kaafi"],
                ["कमी खर्च", "अंधाऱ्या खोलीसाठी पुरेसा"],
                ["Lower cost", "Enough for a dim room"],
            ),
            "b_pros": p(
                ["Dhoop mein bhi saaf", "Cricket smooth", "Dialogue saaf"],
                ["उन्हातही स्पष्ट", "क्रिकेट स्मूथ", "संवाद स्पष्ट"],
                ["Clear in sunlight", "Smooth cricket", "Clear dialogue"],
            ),
            "verdict": p(
                "Dhoop wale kamre aur cricket ke liye B behtar hai.",
                "उन्हाची खोली आणि क्रिकेटसाठी B चांगला आहे.",
                "For a sunny room and cricket, B is the better fit.",
            ),
            "price_a": "₹38,990",
            "price_b": "₹62,990",
        }
    if template == "kharcha":
        return {
            "customer_name": p("शाह जी", "शहा साहेब", "Shah ji"),
            "product_title": 'Meru 55" QLED Pro',
            "product_image": "products/TV-MER-55-QL120.png",
            "lines": [
                {"tag": "product", "title": 'Meru 55" QLED Pro', "amount_str": "₹62,990", "reason": None},
                {
                    "tag": "zaroori",
                    "title": p("दीवार ब्रैकेट", "भिंतीचा ब्रॅकेट", "Wall bracket"),
                    "amount_str": "₹1,499",
                    "reason": None,
                },
                {
                    "tag": "zaroori",
                    "title": p("इंस्टॉलेशन (दीवार)", "इन्स्टॉलेशन (भिंत)", "Installation (wall)"),
                    "amount_str": "₹799",
                    "reason": None,
                },
                {
                    "tag": "sujhav",
                    "title": p(
                        "resQ 2 साल की एक्सटेंडेड वारंटी", "resQ 2 वर्षांची एक्स्टेंडेड वॉरंटी", "resQ 2-year extended warranty"
                    ),
                    "amount_str": "₹3,499",
                    "reason": p("घर में बच्चे हैं, रोज़ चलेगा", "घरात मुलं आहेत, रोज चालेल", "Kids at home, daily use"),
                },
                {
                    "tag": "optional",
                    "title": p("HDMI 2.1 केबल", "HDMI 2.1 केबल", "HDMI 2.1 cable"),
                    "amount_str": "₹899",
                    "reason": None,
                },
            ],
            "zaroori_total_str": "₹65,288",
            "with_suggestions_total_str": "₹68,787",
            "exchange_text": p(
                "पुराने टीवी का एक्सचेंज: ₹4,000 – ₹6,500 (बिल से अलग)",
                "जुन्या टीव्हीचा एक्सचेंज: ₹4,000 – ₹6,500 (बिलापासून वेगळं)",
                "Exchange for the old TV: ₹4,000 – ₹6,500 (separate from the bill)",
            ),
            "lifetime_text": p(
                "बिजली: लगभग ₹210/महीना · 5 साल ₹12,600",
                "वीज: सुमारे ₹210/महिना · 5 वर्षं ₹12,600",
                "Power: about ₹210/month · 5 years ₹12,600",
            ),
            "stamp": "1 Nov 18:40",
        }
    if template == "parivaar":
        return {
            "title": p("शाह परिवार के लिए", "शहा कुटुंबासाठी", "For the Shah family"),
            "image": "products/TV-MER-55-QL120.png",
            "product_title": 'Meru 55" QLED Pro',
            "reasons": p(
                ["क्रिकेट में तेज़ बॉल भी साफ़", "धूप वाले कमरे में भी चमकदार तस्वीर", "आपकी 9 फ़ुट की दूरी के लिए सही साइज़"],
                ["क्रिकेटमध्ये वेगवान बॉलही स्पष्ट", "उन्हाच्या खोलीतही उजळ चित्र", "तुमच्या 9 फुटांच्या अंतरासाठी योग्य साइज"],
                ["Fast cricket balls stay sharp", "Bright even in a sunny room", "Right size for your 9 ft distance"],
            ),
            "key_specs": [
                {"label": p("स्क्रीन", "स्क्रीन", "Screen"), "value": '55" QLED 4K'},
                {"label": p("चमक", "उजळपणा", "Brightness"), "value": "600 nits"},
                {"label": p("मोशन", "मोशन", "Motion"), "value": "120 Hz"},
                {"label": p("आवाज़", "आवाज", "Sound"), "value": "40 W Atmos"},
            ],
            "cost_rows": [
                {
                    "label": p("प्रोडक्ट + ज़रूरी", "उत्पादन + आवश्यक", "Product + required"),
                    "value": "₹65,288",
                    "strong": True,
                },
                {
                    "label": p("2 साल की वारंटी (सुझाव)", "2 वर्षांची वॉरंटी (सूचना)", "2-year warranty (suggested)"),
                    "value": "+₹3,499",
                    "strong": False,
                },
                {"label": p("ऑफ़र के बाद देय", "ऑफरनंतर देय", "Payable after offer"), "value": "₹60,288", "strong": True},
            ],
            "offer_text": p(
                "HDFC कार्ड −₹5,000 (15 Nov तक)", "HDFC कार्ड −₹5,000 (15 Nov पर्यंत)", "HDFC card −₹5,000 (until 15 Nov)"
            ),
            "finance_text": p(
                "₹5,024 महीना, 12 महीने; फ़ीस ₹299",
                "दरमहा ₹5,024, 12 महिने; फी ₹299",
                "₹5,024 a month for 12 months, ₹299 fee",
            ),
            "delivery_text": p("मंगलवार, 4 Nov", "मंगळवार, 4 Nov", "Tuesday, 4 Nov"),
            "associate_name": p("रवि", "रवी", "Ravi"),
            "store_name": "Reliance Digital Juhu (TEST)",
        }
    if template == "score":
        names = p(
            ["Maanna", "Ghar samajhna", "Fayda batana", "Demo", "Poora kharcha", "Close", "Follow-up"],
            ["मान्य करणं", "घर समजणं", "फायदा सांगणं", "डेमो", "पूर्ण खर्च", "क्लोज", "फॉलो-अप"],
            ["Validate", "Assess", "Translate", "Demonstrate", "Complete", "Close", "Follow through"],
        )
        pts = [2, 2, 1, 2, 2, 1, 2]
        return {
            "score_str": "12/14",
            "steps": [{"name": n, "points": v} for n, v in zip(names, pts, strict=True)],
            "level_text": p("Guide level!", "गाइड लेव्हल!", "Guide level!"),
            "praise": p(
                "Product se pehle 5 ghar ke sawaal poochhe",
                "उत्पादनाआधी घराचे 5 प्रश्न विचारले",
                "Asked 5 home questions before any product",
            ),
            "tip": p(
                "Har feature ke saath 'kyunki aapke ghar mein…' jodiye",
                "प्रत्येक फीचरसोबत 'कारण तुमच्या घरात…' जोडा",
                "Add 'because in your home…' to every feature",
            ),
        }
    if template == "brief":
        return {
            "greeting": p("Namaste Ravi ji", "नमस्कार रवी", "Namaste Ravi"),
            "niti_line": p("दिन की शुरुआत, तैयारी के साथ।", "दिवसाची सुरुवात तयारीने.", "Start the day prepared."),
            "changes": [
                {
                    "icon": "up",
                    "text": p(
                        "Meru 55 QLED Pro wapas stock mein (3)",
                        "Meru 55 QLED Pro परत स्टॉकमध्ये (3)",
                        "Meru 55 QLED Pro back in stock (3)",
                    ),
                },
                {
                    "icon": "down",
                    "text": p("Novex 55 OLED stock khatam", "Novex 55 OLED स्टॉक संपला", "Novex 55 OLED out of stock"),
                },
                {
                    "icon": "low",
                    "text": p("Arka 43 4K: sirf 1 bacha", "Arka 43 4K: फक्त 1 उरला", "Arka 43 4K: only 1 left"),
                },
            ],
            "offers": [
                {"text": p("HDFC 10% off — 15 Nov tak", "HDFC 10% सूट — 15 Nov पर्यंत", "HDFC 10% off: until 15 Nov")},
                {"text": p("Naya: Exchange bonus ₹2,000", "नवीन: एक्सचेंज बोनस ₹2,000", "New: exchange bonus ₹2,000")},
            ],
            "followups_text": p(
                "2 customer aaj wapas aa sakte hain (Mehta ji)",
                "2 ग्राहक आज परत येऊ शकतात (मेहता जी)",
                "2 customers may come back today (Mehta ji)",
            ),
            "focus_text": p("Fayda batana (kal 1/2)", "फायदा सांगणं (काल 1/2)", "Translate (yesterday 1/2)"),
        }
    if template == "digest":
        return {
            "kpis": [
                {"label": p("Baatcheet", "संवाद", "Conversations"), "value_str": "6"},
                {"label": p("Guide level", "गाइड लेव्हल", "Guide level"), "value_str": "2/6"},
                {"label": p("Median score", "मध्य स्कोअर", "Median score"), "value_str": "9/14"},
                {"label": p("Pehle daam poocha", "आधी किंमत विचारली", "Asked price first"), "value_str": "4"},
            ],
            "heatmap": {
                "associates": ["Ravi", "Pooja", "Amit"],
                "steps": p(
                    ["Maan", "Ghar", "Fayda", "Demo", "Kharcha", "Close", "Follow"],
                    ["मान्य", "घर", "फायदा", "डेमो", "खर्च", "क्लोज", "फॉलो"],
                    ["Val", "Ass", "Tra", "Dem", "Com", "Clo", "Fol"],
                ),
                "cells": [[2, 2, 1, 2, 2, 2, 1], [1, 2, 0, 1, 2, 1.5, 2], [0, 1, 1, None, 1, 0, 0.5]],
            },
            "gaps_text": p(
                "Ravi: Fayda batana sabse kam. 45-sec clip bhejein?",
                "रवी: फायदा सांगणं सर्वात कमी. 45 सेकंदांची क्लिप पाठवू?",
                "Ravi: Translate is lowest. Send the 45-sec clip?",
            ),
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
    d = _clean(dict(card.data or {}))
    tpl = card.template
    lb = labels_for(tpl, card.lang, d.get("labels"))
    title = lb.get("title", tpl.title())
    rows: list[str] = []
    if tpl == "home":
        title = f"{d.get('name') or ''} · {d.get('category_label') or ''}".strip(" ·") or title
        rows += [f"{r.get('label')}: {r.get('value')}" for r in d.get("rows") or []]
        if d.get("start_level_text"):
            rows.append(f"{lb['start_level']}: {d['start_level_text']}")
        rows.append(
            f"{lb['progress']}: {d.get('known_str') or d.get('known', 0)}/{d.get('total_str') or d.get('total', 0)}"
        )
        if d.get("missing"):
            rows.append(f"{lb['missing']}: " + ", ".join(str(m) for m in d["missing"]))
    elif tpl == "benefit":
        title = d.get("product_title") or title
        rows += [f"• {b.get('text')}" for b in d.get("benefits") or []]
        if d.get("ignore_text"):
            rows.append(f"{lb['ignore']}: {d['ignore_text']}")
    elif tpl == "options":
        opts = d.get("options") or []
        title = lb["title"].replace("{n}", str(len(opts) or 3))
        for o in opts:
            star = f" * {lb['best']}" if o.get("is_best") else ""
            rows.append(
                f"{o.get('label')}: {o.get('title')}{star} — {o.get('reason') or ''} · {o.get('fit_str') or ''}"
            )
            extra = " · ".join(
                str(x) for x in [*(o.get("features") or [])[:3], o.get("stock_line"), o.get("dims_text")] if x
            )
            if extra:
                rows.append(f"    {extra}")
        rows.append(lb["no_price"])
    elif tpl == "catalog":
        title = d.get("title") or lb["title"]
        if d.get("subtitle"):
            rows.append(str(d["subtitle"]))
        for it in (d.get("items") or [])[:6]:
            price = f" — {it['price_str']}" if it.get("price_str") else ""
            rows.append(f"{it.get('title') or ''}{price}")
            extra = " · ".join(
                str(x) for x in [*(it.get("specs") or [])[:3], it.get("stock_line"), it.get("offer_badge")] if x
            )
            if extra:
                rows.append(f"    {extra}")
        if not d.get("items"):
            rows.append(lb["empty"])
    elif tpl == "product":
        title = d.get("title") or lb["kicker"]
        head = " · ".join(str(x) for x in (d.get("badge"), d.get("fit_str")) if x)
        if head:
            rows.append(head)
        rows += [f"{s.get('label')}: {s.get('value')}" for s in aslist(d.get("key_specs"))[:6] if isinstance(s, dict)]
        rows += [f"• {f}" for f in aslist(d.get("features"))[:4]]
        rows += [f"+ {x}" for x in aslist(d.get("pros"))[:4]]
        rows += [f"! {x}" for x in aslist(d.get("cons"))[:3]]
        for k in ("stock_line", "offer_text", "price_str", "note"):
            if d.get(k):
                rows.append(str(d[k]))
    elif tpl == "compare":
        a, b = d.get("a") or {}, d.get("b") or {}
        title = d.get("title") or f"{a.get('title') or 'A'} vs {b.get('title') or 'B'}"
        rows.append(
            f"A: {a.get('title') or ''} {a.get('fit_str') or ''}   B: {b.get('title') or ''} {b.get('fit_str') or ''}"
        )
        for r in aslist(d.get("rows"))[:8]:
            if isinstance(r, dict):
                star_a = " *" if r.get("better") == "a" else ""
                star_b = " *" if r.get("better") == "b" else ""
                rows.append(f"{r.get('label')}: A {r.get('a')}{star_a} | B {r.get('b')}{star_b}")
        if d.get("price_a") or d.get("price_b"):
            rows.append(f"{lb['price']}: A {d.get('price_a') or '-'} | B {d.get('price_b') or '-'}")
        rows += [f"A + {x}" for x in aslist(d.get("a_pros"))[:3]] + [f"B + {x}" for x in aslist(d.get("b_pros"))[:3]]
        if d.get("verdict"):
            rows.append(f"{lb['verdict']}: {d['verdict']}")
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
        rows += [f"✓ {r}" for r in (d.get("reasons") or [])[:3]]
        specs = " · ".join(
            f"{s.get('label')}: {s.get('value')}" for s in (d.get("key_specs") or [])[:4] if isinstance(s, dict)
        )
        if specs:
            rows.append(specs)
        cost_rows = d.get("cost_rows") or []
        if cost_rows:
            rows += [f"{c.get('label')}: {c.get('value')}" for c in cost_rows if isinstance(c, dict)]
        elif d.get("cost_text"):
            rows.append(str(d["cost_text"]))
        for k, lab in (("offer_text", "offer"), ("finance_text", "finance"), ("delivery_text", "delivery")):
            if d.get(k):
                rows.append(f"{lb[lab]}: {_strip_label(d[k])}")
        who = " · ".join(str(x) for x in (d.get("associate_name"), d.get("store_name")) if x)
        if who:
            rows.append(f"{lb['from']}: {who}")
    elif tpl == "score":
        title = f"{lb['title']} · {d.get('score_str') or ''}"
        for s in d.get("steps") or []:
            pv = max(0, min(2, int(s.get("points") or 0)))
            rows.append(f"{s.get('name')}: {'●' * pv}{'○' * (2 - pv)}")
        for k, lab in (("level_text", None), ("praise", lb["praise"]), ("tip", lb["tip"])):
            if d.get(k):
                rows.append(f"{lab}: {d[k]}" if lab else str(d[k]))
    elif tpl == "brief":
        title = d.get("greeting") or title
        if d.get("niti_line"):
            rows.append(str(d["niti_line"]))
        rows += [f"• {c.get('text')}" for c in d.get("changes") or []]
        rows += [f"• {o.get('text')}" for o in d.get("offers") or []]
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
        "×": "x",
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
    """Plain but clean Pillow card: indigo header, title, rows, TEST DATA badge. Never raises on data."""
    from PIL import Image, ImageDraw

    title, rows = _fallback_rows(card)
    title, rows = _sanitise(title), [_sanitise(r) for r in rows]
    pad, w = 56, WIDTH
    f_title, f_body, f_small = _font(52), _font(34), _font(24)
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    body_lines: list[str] = []
    for r in rows:
        body_lines += _wrap(scratch, r, f_body, w - 2 * pad - 40)
    title_lines = _wrap(scratch, title, f_title, w - 2 * pad - 220)
    band_h = 110 + 70 * len(title_lines)
    h = band_h + 60 + 54 * len(body_lines) + 90
    h = max(420, min(MAX_HEIGHT, h))
    img = Image.new("RGB", (w, h), "#F5F2EC")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, band_h], fill="#1B2A4A")
    d.rounded_rectangle([pad, 34, pad + 30, 64], radius=8, fill="#E8833A")
    d.text((pad + 44, 32), "Chanakya", font=f_small, fill="#C6CFE2")
    bw = int(d.textlength("TEST DATA", font=f_small)) + 30
    d.rounded_rectangle([w - pad - bw, 30, w - pad, 68], radius=19, fill="#FCE7EC")
    d.text((w - pad - bw + 15, 34), "TEST DATA", font=f_small, fill="#B4365A")
    y = 92
    for tl in title_lines:
        d.text((pad, y), tl, font=f_title, fill="white")
        y += 70
    box_top = band_h + 30
    d.rounded_rectangle([pad - 16, box_top, w - pad + 16, h - 50], radius=28, fill="#FFFFFF")
    y = box_top + 26
    for ln in body_lines:
        if y > h - 50 - 70:
            d.text((pad + 10, y), "…", font=f_body, fill="#687083")
            break
        d.text((pad + 10, y), ln, font=f_body, fill="#2A3142")
        y += 54
    d.text((pad, h - 40), "Chanakya - Store IQ", font=f_small, fill="#9AA1AF")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()


# ---------- Playwright renderer ----------
_FIT_JS = """([MAX, MIN, W]) => {
  const c = document.getElementById('card'), f = document.getElementById('fit');
  if (!c || !f) return 1;
  let z = 1;
  for (let i = 0; i < 5; i++) {
    const h = f.offsetHeight;
    if (h * z <= MAX + 0.5) break;
    z = Math.max(MIN, MAX / h);
    f.style.width = (W / z) + 'px';
    f.style.transform = 'scale(' + z + ')';
    if (z <= MIN) break;
  }
  if (z < 1) c.style.height = Math.min(MAX, Math.ceil(f.offsetHeight * z)) + 'px';
  return z;
}"""


def make_env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))
    env.globals.update(icon=icon)
    env.filters.update(
        dv=dv_class, pct=pct, frac=frac, initial=initial, stock_icon=stock_icon, aslist=aslist, icon_name=icon_name
    )
    env.tests["dev"] = is_dev
    return env


class Renderer:
    def __init__(self, pool_size: int = 2):
        self.env = make_env()
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
                    await self._render_html(
                        Card.model_construct(template=name, lang="hi", data=sample_data(name, "hi"))
                    )
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
            # scale slightly-too-tall content to ≤ 1350 px (down to MIN_FIT_SCALE); beyond that CSS clips
            await page.evaluate(_FIT_JS, [MAX_HEIGHT, MIN_FIT_SCALE, WIDTH])
            el = await page.query_selector("#card")
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
