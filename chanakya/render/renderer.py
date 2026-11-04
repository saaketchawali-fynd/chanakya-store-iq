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
FONTS = HERE / "static" / "fonts"  # bundled typefaces (Montserrat, SIL Open Font License: static/fonts/OFL.txt)
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
    "offer",
    "view",
    "demo",
    "homecompare",
    "addons",
)
WIDTH = 1080
MAX_HEIGHT = 1350
MIN_FIT_SCALE = 0.78  # content up to ~1730 px is scaled to fit 1350 (nothing is cut); taller content is clipped

# ---------- fixed labels (hi for customer cards in Devanagari; associate cards in Roman Hinglish) ----------
LABELS: dict[str, dict[str, dict[str, str]]] = {
    "common": {
        "hi": {"test_data": "TEST DATA", "brand": "Reliance Digital", "brand_sub": "Chanakya"},
        "mr": {"test_data": "TEST DATA", "brand": "Reliance Digital", "brand_sub": "Chanakya"},
        "en": {"test_data": "TEST DATA", "brand": "Reliance Digital", "brand_sub": "Chanakya"},
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
            "title_one": "आपके घर के लिए सही",
            "best": "सबसे सही",
            "no_price": "कीमत डेमो के बाद",
            "why": "आपके घर में क्यों सही",
            "role_value": "कम ख़र्च, फिर भी सही",
            "role_new": "नया फ़िट",
            "role_broader": "और विकल्प",
            "role_step": "एक लेवल ऊपर",
            "compare": "आपके घर के हिसाब से तुलना",
        },
        "mr": {
            "kicker": "तुमच्या घरासाठी",
            "title": "{n} योग्य पर्याय",
            "title_one": "तुमच्या घरासाठी योग्य",
            "best": "सर्वोत्तम",
            "no_price": "किंमत डेमोनंतर",
            "why": "तुमच्या घरासाठी का योग्य",
            "role_value": "कमी खर्च, तरीही योग्य",
            "role_new": "नवा फिट",
            "role_broader": "आणखी पर्याय",
            "role_step": "एक पायरी वर",
            "compare": "तुमच्या घरानुसार तुलना",
        },
        "en": {
            "kicker": "For your home",
            "title": "{n} picks that fit",
            "title_one": "The pick that fits",
            "best": "Best fit",
            "no_price": "Price after the demo",
            "why": "Why it fits your home",
            "role_value": "Fits, costs less",
            "role_new": "New fit",
            "role_broader": "More option",
            "role_step": "One step up",
            "compare": "How each fits your home",
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
            "made": "Aapke ghar ke liye",
            "picked": "Aapki pasand",
            "for_home": "Aapke ghar ke liye",
            "also": "Aur bhi",
            "price_later": "Daam demo ke baad",
            "warranty": "Warranty",
        },
        "mr": {
            "kicker": "उत्पादन",
            "specs": "मुख्य स्पेक्स",
            "features": "खास गोष्टी",
            "pros": "चांगल्या गोष्टी",
            "cons": "लक्षात ठेवा",
            "fit": "घराशी जुळणी",
            "made": "तुमच्या घरासाठी",
            "picked": "तुमची निवड",
            "for_home": "तुमच्या घरासाठी",
            "also": "आणखी",
            "price_later": "किंमत डेमोनंतर",
            "warranty": "वॉरंटी",
        },
        "en": {
            "kicker": "Product",
            "specs": "Key specs",
            "features": "Highlights",
            "pros": "Good for this home",
            "cons": "Worth knowing",
            "fit": "home match",
            "made": "Made for your home",
            "picked": "Your pick",
            "for_home": "For your home",
            "also": "Also",
            "price_later": "Price after the demo",
            "warranty": "Warranty",
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
            "zaroori_total": "कुल चुकाना",
            "total_note": "प्रोडक्ट + ज़रूरी इंस्टॉलेशन",
            "req_group": "इंस्टॉलेशन के लिए ज़रूरी",
            "sug_group": "आपके घर के लिए सुझाव",
            "add_group": "Aapke ghar ke liye joda",
            "with_suggestions_total": "सुझाव सहित कुल",
            "exchange": "एक्सचेंज",
            "lifetime": "चलाने का खर्च",
            "note": "बिल = कार्ड · कोई सरप्राइज़ नहीं",
            "protect": "सुरक्षा (वैकल्पिक)",
            "good_to_know": "साथ में",
        },
        "mr": {
            "title": "पूर्ण खर्च",
            "product": "उत्पादन",
            "zaroori": "आवश्यक",
            "sujhav": "सुचवलेलं",
            "optional": "पर्यायी",
            "zaroori_total": "एकूण भरायचे",
            "total_note": "उत्पादन + आवश्यक इन्स्टॉलेशन",
            "req_group": "इन्स्टॉलेशनसाठी आवश्यक",
            "sug_group": "तुमच्या घरासाठी सूचना",
            "add_group": "तुमच्या घरासाठी जोडलं",
            "with_suggestions_total": "सूचनांसह एकूण",
            "exchange": "एक्सचेंज",
            "lifetime": "चालवण्याचा खर्च",
            "note": "बिल = कार्ड · कोणतंही सरप्राइज नाही",
            "protect": "संरक्षण (पर्यायी)",
            "good_to_know": "सोबत",
        },
        "en": {
            "title": "Full cost",
            "product": "Product",
            "zaroori": "Required",
            "sujhav": "Suggested",
            "optional": "Optional",
            "zaroori_total": "Total to pay",
            "total_note": "product + required installation",
            "req_group": "Needed for installation",
            "sug_group": "Suggested for your home",
            "add_group": "Added for your home",
            "with_suggestions_total": "Total with suggestions",
            "exchange": "Exchange",
            "lifetime": "Running cost",
            "note": "Bill = card · no surprises",
            "protect": "Protect it (optional)",
            "good_to_know": "Included and noted",
        },
    },
    "offer": {
        "hi": {"title": "आज का ऑफ़र", "pay": "पेमेंट", "price": "दाम", "after_offers": "ऑफ़र के बाद",
               "no_offer": "इस पेमेंट पर आज कोई ऑफ़र नहीं", "no_offer_any": "आज इस मॉडल पर कोई ऑफ़र नहीं", "extras": "इंस्टॉलेशन और ज़रूरी सामान",
               "total": "आज का कुल खर्च", "total_note": "इंस्टॉलेशन समेत", "saving": "कुल बचत", "emi": "EMI",
               "exchange": "एक्सचेंज", "also": "दूसरे कार्ड पर भी", "note": "सिर्फ़ असली ऑफ़र · बिल = कार्ड"},
        "mr": {"title": "आजची ऑफर", "pay": "पेमेंट", "price": "किंमत", "after_offers": "ऑफरनंतर",
               "no_offer": "या पेमेंटवर आज ऑफर नाही", "no_offer_any": "आज या मॉडेलवर ऑफर नाही", "extras": "इन्स्टॉलेशन व आवश्यक साहित्य",
               "total": "आजचा एकूण खर्च", "total_note": "इन्स्टॉलेशनसह", "saving": "एकूण बचत", "emi": "EMI",
               "exchange": "एक्सचेंज", "also": "दुसऱ्या कार्डवरही", "note": "फक्त खऱ्या ऑफर · बिल = कार्ड"},
        "en": {"title": "Today's offer", "pay": "Payment", "price": "Price", "after_offers": "After offers",
               "no_offer": "No offer on this payment today", "no_offer_any": "No live offer on this model today", "extras": "Installation and required items",
               "total": "Total today", "total_note": "installation included", "saving": "You save", "emi": "EMI",
               "exchange": "Exchange", "also": "Also with another card", "note": "Only real offers · bill = card"},
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
    "addons": {  # "Complete it": the customer's card, English (hi/mr kept for completeness)
        "hi": {"kicker": "Poora kijiye", "title": "Saath mein:", "goes": "Aapke ghar ke liye saath mein",
               "total": "Sab milakar", "step_up": "Ek kadam upar", "about": "lagbhag", "a_day": "roz",
               "foot": "Sab data se · har ek ki apni wajah"},
        "mr": {"kicker": "पूर्ण करा", "title": "सोबत:", "goes": "तुमच्या घरासाठी सोबत", "total": "सगळं मिळून",
               "step_up": "एक पाऊल वर", "about": "सुमारे", "a_day": "दररोज", "foot": "सगळं डेटामधून · प्रत्येकाचं कारण"},
        "en": {"kicker": "Complete it", "title": "Goes well with your", "goes": "For your home, with it",
               "total": "Full cost with all of these", "step_up": "Or step up", "about": "about", "a_day": "a day",
               "foot": "All from the data · each one for a reason of theirs"},
    },
    "demo": {  # the associate's own script: in their language (user, 5 Nov 2026)
        "hi": {"kicker": "Demo guide", "title": "Pehle unki zaroorat dikhaiye", "wow": "Pehle wow", "then": "Phir",
               "more": "Aur dikhaiye"},
        "mr": {"kicker": "डेमो गाइड", "title": "आधी त्यांची गरज दाखवा", "wow": "आधी wow", "then": "मग",
               "more": "आणखी दाखवा"},
        "en": {"kicker": "Demo guide", "title": "Show their need first", "wow": "Wow first", "then": "Then",
               "more": "Also show"},
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
        "hi": {"title": "Shift se pehle", "changes": "Stock badlav", "offers": "Aaj ke offers", "followups": "Follow-up",
               "focus": "Aaj ka focus", "keep": "Dhyan rahe"},
        "mr": {"title": "शिफ्टआधी", "changes": "स्टॉक बदल", "offers": "आजच्या ऑफर्स", "followups": "फॉलो-अप",
               "focus": "आजचा फोकस", "keep": "लक्षात ठेवा"},
        "en": {"title": "Before the shift", "changes": "Stock changes", "offers": "Today's offers", "followups": "Follow-ups",
               "focus": "Today's focus", "keep": "Keep in mind"},
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
    "play": '<circle cx="12" cy="12" r="9"/><path d="M10 8.5l5 3.5-5 3.5z"/>',
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


# Product pictures from the backend (Jio Commerce media URLs) are downloaded once to this disk cache before a card
# is drawn, then inlined like local files. A picture that cannot be fetched is simply left out.
IMG_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "img"
IMAGE_KEYS = ("product_image", "image")


def _cache_path(url: str) -> Path:
    import hashlib

    ext = Path(url.split("?")[0]).suffix.lower()
    return IMG_CACHE / (hashlib.sha1(url.encode()).hexdigest()[:24] + (ext if ext in (".jpg", ".jpeg", ".png", ".webp") else ".img"))


def _card_urls(data: dict) -> list[str]:
    found = [data.get(k) for k in IMAGE_KEYS] + [data.get("brand_logo")] + list(data.get("gallery") or [])
    found += [(data.get(k) or {}).get("image") for k in ("product", "step_up") if isinstance(data.get(k), dict)]
    found += [(x or {}).get("image") for x in data.get("tiles") or [] if isinstance(x, dict)]
    for key in ("options", "items", "products", "cols"):  # the demo guide's and the side-by-side's pictures too
        found += [(o or {}).get(k) for o in data.get(key) or [] if isinstance(o, dict) for k in ("image", "photo")]
    found += [(data.get(side) or {}).get("image") for side in ("a", "b") if isinstance(data.get(side), dict)]
    return [u for u in found if isinstance(u, str) and u.startswith("https://")]


async def fetch_images(card: Card) -> None:
    """Download the card's backend pictures that are not cached yet (≤ 4 MB each, images only)."""
    urls = [u for u in dict.fromkeys(_card_urls(card.data or {})) if not _cache_path(u).is_file()]
    if not urls:
        return
    import httpx

    IMG_CACHE.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=4), follow_redirects=True) as c:
        for u in urls:
            try:
                r = await c.get(u)
                if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                    continue
                if len(r.content) > 4 * 1024 * 1024:
                    continue
                tmp = _cache_path(u).with_suffix(".part")
                tmp.write_bytes(r.content)
                tmp.replace(_cache_path(u))
            except Exception:
                continue


async def image_bytes(ref: str | None) -> bytes | None:
    """A product picture's bytes (backend URL, cached on disk; or a local file in sample mode); None if missing."""
    if not ref or not isinstance(ref, str):
        return None
    if ref.startswith("https://"):
        if not _cache_path(ref).is_file():
            await fetch_images(Card.model_construct(template="product", lang="en", data={"image": ref}))
        c = _cache_path(ref)
        return c.read_bytes() if c.is_file() else None
    p = Path(ref)
    p = p if p.is_absolute() else STATIC_IMG / ref
    return p.read_bytes() if p.is_file() else None


def to_data_uri(img: Any, max_px: int | None = None) -> str | None:
    """bytes | data: URI | cached backend picture (https URL) | path relative to render/static/img → data URI
    (None if unusable).

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
            if img.startswith("https://"):
                c = _cache_path(img)
                return _file_data_uri(str(c), c.stat().st_mtime_ns, max_px) if c.is_file() else None
            p = Path(img)
            if not p.is_absolute():
                p = STATIC_IMG / img
            if p.is_file():
                return _file_data_uri(str(p), p.stat().st_mtime_ns, max_px)
    except Exception:
        return None
    return None


_FIELD_LABELS = ("ऑफ़र", "ऑफर", "offer", "डिलीवरी", "डिलिव्हरी", "delivery", "emi", "ईएमआई")


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
    for key in ("product", "step_up"):  # "Complete it": their product and the step up
        if isinstance(data.get(key), dict) and data[key].get("image"):
            data[key] = dict(data[key], image=to_data_uri(data[key]["image"], 420))
    if data.get("brand_logo"):  # the product page: the brand's own logo, and more real photos of the product
        data["brand_logo"] = to_data_uri(data["brand_logo"], 260)
    if isinstance(data.get("gallery"), list):
        data["gallery"] = [u for u in (to_data_uri(g, 360) for g in data["gallery"][:3]) if u]
    if isinstance(data.get("tiles"), list):  # the product card's three photo tiles
        data["tiles"] = [dict(x, image=to_data_uri(x.get("image"), 900 if x.get("crop") else 520))
                         for x in data["tiles"][:3] if isinstance(x, dict)]
    if isinstance(data.get("options"), list):
        opts = []
        for o in data["options"]:
            o = dict(o or {})
            o["image"] = to_data_uri(o.get("image"), 900 if o.get("is_best") else 560)
            opts.append(o)
        data["options"] = opts
    for key, px in (("products", 320), ("cols", 760)):  # the demo guide's products; the home compare's columns
        if isinstance(data.get(key), list):
            rows = []
            for o in data[key]:
                o = dict(o or {})
                for k in ("image", "photo"):
                    if o.get(k):
                        o[k] = to_data_uri(o[k], px if k == "image" else 900)
                rows.append(o)
            data[key] = rows
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
                {"label": p("Doori", "अंतर", "Distance"), "value": p("8–10 ft", "8–10 ft", "8–10 ft")},
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
            "ignore_text": p("HDMI पोर्ट, OS, स्पीकर watts", "HDMI पोर्ट, OS, स्पीकर वॅट", "HDMI ports, OS, speaker watts"),
            "product_image": "products/TV-MER-55-QL120.png",
        }
    if template == "options":
        stock = p("Store mein {n}", "स्टोअरमध्ये {n}", "{n} in store")
        good, no = "good", "no"
        return {
            "home": p(["7 ft", "Dhoop aati hai", "Har match dekhte"], ["7 ft", "ऊन येतं", "प्रत्येक मॅच पाहतात"],
                      ["7 ft", "Sunny", "Watches every match"]),
            "options": [
                {
                    "label": "A",
                    "title": 'Arka 43" 4K',
                    "role": "value",
                    "diffs": [{"label": p("brightness", "brightness", "Brightness"), "value": "300 nits"},
                              {"label": p("रिफ्रेश रेट", "रिफ्रेश रेट", "Refresh rate"), "value": "60 Hz"}],
                    "stock_line": stock.format(n=4),
                    "image": "products/TV-ARK-43-4K60.png",
                    "is_best": False,
                },
                {
                    "label": "B",
                    "title": 'Meru 43" QLED Pro',
                    "role": "best",
                    "points": [
                        {"text": p("धूप में भी साफ़ तस्वीर", "उन्हातही स्पष्ट चित्र", "Clear picture in sunlight"),
                         "proof": "600 nits"},
                        {"text": p("क्रिकेट में बॉल साफ़", "क्रिकेटमध्ये बॉल स्पष्ट", "Sharp ball in cricket"),
                         "proof": "120 Hz"},
                        {"text": p("आपकी दूरी के लिए सही साइज़", "तुमच्या अंतरासाठी योग्य साइज", "Right size for your distance"),
                         "proof": p("43 inch", "43 inch", "43 inch")},
                    ],
                    "stock_line": stock.format(n=3),
                    "image": "products/TV-MER-55-QL120.png",
                    "is_best": True,
                    "dims_text": "96 × 56 cm",
                },
                {
                    "label": "C",
                    "title": 'Meru 55" QLED Pro',
                    "role": "step",
                    "diffs": [{"label": p("साइज़", "साइज", "Size"), "value": p("55 inch", "55 inch", "55 inch")}],
                    "stock_line": stock.format(n=2),
                    "image": "products/TV-MER-65-QL120.png",
                    "is_best": False,
                },
            ],
            "grid": [
                {"need": p("7 ft दूरी के लिए साइज़", "7 ft अंतरासाठी साइज", "Size for 7 ft away"),
                 "cells": [{"label": "A", "value": '43"', "state": good}, {"label": "B", "value": '43"', "state": good},
                           {"label": "C", "value": '55"', "state": "near"}]},
                {"need": p("धूप वाला कमरा", "उन्हाची खोली", "Sunny room"),
                 "cells": [{"label": "A", "value": "300 nits", "state": no},
                           {"label": "B", "value": "600 nits", "state": good},
                           {"label": "C", "value": "600 nits", "state": good}]},
                {"need": p("हर मैच स्मूथ", "प्रत्येक मॅच स्मूथ", "Every match, smooth"),
                 "cells": [{"label": "A", "value": "60 Hz", "state": no}, {"label": "B", "value": "120 Hz", "state": good},
                           {"label": "C", "value": "120 Hz", "state": good}]},
            ],
        }
    if template == "catalog":
        stock = p("Store mein {n}", "स्टोअरमध्ये {n}", "{n} in store")
        return {
            "title": p("55 inch TV", "55 inch टीव्ही", "55-inch TVs"),
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
            "brand": "Meru",
            "subtitle": "55 inch · 4K · QLED · Google TV",
            "chip": "Best fit for your home",
            "chip_label": "B",
            "chip_kind": "best",
            "needs": [
                {"text": "Clear picture even in afternoon sun", "proof": "600 nits", "icon": "sun"},
                {"text": "The ball stays sharp in cricket", "proof": "120 Hz", "icon": "motion", "tag": "You asked"},
                {"text": "Right size for your 10 ft sofa", "proof": "55 inch", "icon": "size"},
            ],
            "also": [{"text": "Netflix, Prime and YouTube built in", "proof": "Google TV", "icon": "apps"}],
            "rows": [{"text": "Clear picture even in afternoon sun (600\u00a0nits)", "icon": "sun"},
                     {"text": "The ball stays sharp in cricket (120\u00a0Hz)", "icon": "motion"},
                     {"text": "Right size for your 10 ft sofa (55\u00a0inch)", "icon": "size"},
                     {"text": "Netflix, Prime and YouTube built in (Google\u00a0TV)", "icon": "apps"}],
            "tiles": [{"title": "Picture", "caption": "4K, QLED panel, 600 nits brightness, 120 Hz refresh rate.",
                       "image": "products/TV-MER-55-QL120.png", "crop": "zoom-corner"},
                      {"title": "Sound", "caption": "40 W speakers, Dolby Atmos.", "image": "products/TV-MER-55-QL120.png"},
                      {"title": "Smart TV", "caption": "Google TV, Wi-Fi.", "image": "products/TV-MER-55-QL120.png",
                       "crop": "zoom-base"}],
            "warranty": "1 yr + panel 2 yrs",
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
                ["उन्हाच्या खोलीतही स्पष्ट", "क्रिकेटसाठी 120 Hz स्मूथ", "9 ft अंतरासाठी योग्य साइज"],
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
    if template == "addons":
        return {"product": {"title": 'Meru 55" QLED Pro', "image": "products/TV-MER-55-QL120.png",
                            "price_str": "₹58,990"},
                "items": [{"title": "Meru Soundbar 2.1", "why": "clear dialogue · HDMI ARC: one cable",
                           "price_str": "₹12,990", "mrp_str": "MRP saving ₹2,000", "image": "products/SB-MER-21.png",
                           "combo": "₹3,000 off a soundbar with the TV · till 30 Nov"},
                          {"title": "Extended warranty, 2 years", "why": "2 years of cover, about ₹5 a day",
                           "price_str": "₹3,499", "icon": "shield"}],
                "together": [], "total_str": "₹72,479",
                "step_up": {"label": "C", "title": 'Meru 65" QLED Pro', "image": "products/TV-MER-65-QL120.png",
                            "diff_str": "₹22,000", "day_str": "₹12", "adds": "bigger, 65 inch"}}
    if template == "demo":
        return {"chips": ["Living room", "10 ft", "Movies/OTT", "sound features"],
                "products": [
                    {"label": "B", "title": 'Meru 55" QLED Pro', "image": "products/TV-MER-55-QL120.png", "best": True,
                     "steps": [{"tag": "wow", "title": "Voices and dialogue come through clearer", "proof": "Dialogue enhance",
                                "do": "Play a dialogue scene at the same low volume on both",
                                "say": "For the elders at home: voices and dialogue come through clearer."},
                               {"tag": "then", "title": "Sharp ball in cricket", "proof": "120 Hz",
                                "do": "Play a cricket highlight on both; point at the ball on a fast shot", "say": ""},
                               {"tag": "more", "title": "Right size from their sofa", "proof": "55 inch",
                                "do": "Stand the customer 4 steps back, about their sofa distance", "say": ""}]},
                    {"label": "A", "title": 'Arka 55" 4K', "image": "products/TV-ARK-55-4K60.png", "best": False,
                     "steps": [{"tag": "wow", "title": "Right size from their sofa", "proof": "55 inch",
                                "do": "Stand the customer 4 steps back, about their sofa distance", "say": ""},
                               {"tag": "then", "title": "Panel: 4K LED", "proof": None,
                                "do": "Point it out next to Meru 55 QLED Pro", "say": ""}]}]}
    if template == "homecompare":
        return {"chips": ["Living room", "10 ft", "Movies/OTT"],
                "cols": [{"label": "A", "title": 'Arka 55" 4K', "size": "55 inch", "width": "123 cm",
                          "photo": "products/TV-ARK-55-4K60.png", "best": False, "offer": "No live offer today"},
                         {"label": "B", "title": 'Meru 55" QLED Pro', "size": "55 inch", "width": "123 cm",
                          "photo": "products/TV-MER-55-QL120.png", "best": True, "offer": "10% off on HDFC · till 15 Nov"}],
                "rows": [{"need": "Size for 10 ft away", "cells": [{"label": "A", "value": "55 inch", "state": "good"},
                                                                  {"label": "B", "value": "55 inch", "state": "good"}]},
                         {"need": "For your movie nights", "cells": [{"label": "A", "value": "—", "state": "no"},
                                                                    {"label": "B", "value": "HDR10+", "state": "good"}]},
                         {"need": "Panel", "cells": [{"label": "A", "value": "LED", "state": "info"},
                                                    {"label": "B", "value": "QLED", "state": "info"}]}]}
    if template == "view":
        return {"title": "How big it feels", "distance": "10 ft",
                "rows": [{"label": "A", "size": "55 inch", "model": "Arka 55 4K", "deg": "24", "deg_f": 24.1,
                          "seat": "middle", "seat_text": "Middle rows", "dx": 128.1},
                         {"label": "B", "size": "65 inch", "model": "Meru 65 Pro", "deg": "28", "deg_f": 28.4,
                          "seat": "best", "seat_text": "The best seats", "dx": 151.9}],
                "note": "Angle of view from your sofa. Cinemas aim for about 30° to 40°."}
    if template == "parivaar":  # the Family Card is English (one card)
        return {
            "kicker": "Picked for the Shah family",
            "title": 'Your new Meru 55" QLED Pro',
            "image": "products/TV-MER-55-QL120.png",
            "key_specs": [{"label": "Size", "value": "55 inch"}, {"label": "Panel", "value": "QLED"},
                          {"label": "Refresh rate", "value": "120 Hz"}, {"label": "Brightness", "value": "600 nits"}],
            "points": [{"text": "Sharp ball in cricket", "proof": "120 Hz"},
                       {"text": "Clear picture in sunlight", "proof": "600 nits"},
                       {"text": "Right size for your distance", "proof": "55 inch"}],
            "ladder": [{"label": "Price", "value": "₹62,990"}, {"label": "Installation and essentials", "value": "+₹2,298"},
                       {"label": "10% off on HDFC cards · till 15 Nov", "value": "−₹5,000", "offer": True}],
            "total": "₹60,288",
            "total_label": "You pay today",
            "optional": "Recommended add-ons, optional: +₹3,499",
            "steps": [{"icon": "truck", "k": "Delivery", "v": "Tuesday 3 Nov"},
                      {"icon": "calendar", "k": "Installation", "v": "4 Nov · 10–12"},
                      {"icon": "shield", "k": "Warranty", "v": "1 year brand warranty"}],
            "associate_name": "Ravi",
            "store_name": "Reliance Digital Juhu",
            "reply_line": "Reply here to talk to Ravi",
            "test_data": True,
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
    if template == "offer":
        return {
            "customer_name": "Shah",
            "product_title": 'Meru 55" QLED Pro',
            "product_image": "products/TV-MER-55-QL120.png",
            "pay_text": p("HDFC कार्ड", "HDFC कार्ड", "HDFC card"),
            "mrp_str": "₹74,990",
            "price_str": "₹62,990",
            "offers": [
                {"title": p("HDFC कार्ड पर 10% छूट", "HDFC कार्डवर 10% सूट", "10% off on HDFC cards"), "amount_str": "−₹5,000",
                 "till": p("15 Nov तक", "15 Nov पर्यंत", "till 15 Nov")},
                {"title": p("एक्सचेंज पर ₹2,000 बोनस", "एक्सचेंजवर ₹2,000 बोनस", "₹2,000 exchange bonus"), "amount_str": "−₹2,000",
                 "till": p("12 Nov तक", "12 Nov पर्यंत", "till 12 Nov")},
            ],
            "payable_str": "₹55,990",
            "extras_str": "₹3,297",
            "total_str": "₹59,287",
            "saving_str": "₹19,000",
            "finance_text": None,
            "exchange_text": p("पुराने टीवी का एक्सचेंज: ₹4,000 – ₹7,000", "जुन्या टीव्हीचा एक्सचेंज: ₹4,000 – ₹7,000",
                               "Old TV exchange: ₹4,000 – ₹7,000"),
            "also": [],
            "stamp": "2 Nov 11:00",
        }
    if template == "brief":  # the pre-read is English (one card)
        return {
            "greeting": "Good morning, Ravi",
            "date_line": "Monday 2 Nov · Juhu",
            "cat": "TVs",
            "step": "Step 1 of 3 · Pre-read",
            "stock": {"n": "98", "of": "of 106 TVs available today",
                      "tiles": [{"label": '32"', "count": "14 models", "span": "₹10,990 – ₹18,490"},
                                {"label": '43"', "count": "22 models", "span": "₹19,990 – ₹42,990"},
                                {"label": '55"', "count": "31 models", "span": "₹34,990 – ₹1,24,990"},
                                {"label": '65"', "count": "12 models", "span": "₹62,990 – ₹2,19,990"}]},
            "changes": [{"kind": "out_of_stock", "text": "Sony 55 X80L out of stock since yesterday"},
                        {"kind": "back_in_stock", "text": "Meru 55 QLED Pro back in stock since yesterday"}],
            "offers": [
                {"kind": "ends", "badge": "Ends soon", "title": "Diwali: ₹10,000 off select Novex TVs",
                 "detail": "₹10,000 off", "till": "till 4 Nov"},
                {"kind": "live", "badge": "Live", "title": "10% off on HDFC cards",
                 "detail": "up to ₹5,000 · on bills over ₹30,000", "till": "till 15 Nov"},
            ],
            "keep": ["Offers apply to 61 of the 98 TVs on sale",
                     "Diwali: ₹10,000 off select Novex TVs: only 2 days left",
                     "8 protection plans available (₹1,499 – ₹6,999)",
                     "2 customer follow-ups due today"],
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
    elif tpl == "demo":
        title = "Demo guide"
        for pr in d.get("products") or []:
            rows.append(f"{pr.get('label')}: {pr.get('title')}")
            for i, st in enumerate(pr.get("steps") or [], 1):
                proof = f" ({st['proof']})" if st.get("proof") else ""
                rows.append(f"  {i}. {st.get('title')}{proof}" + (f" — {st['do']}" if st.get("do") else ""))
    elif tpl == "homecompare":
        title = "In your home"
        rows += [f"{c.get('label')}: {c.get('title')} {c.get('size') or ''}".strip() for c in d.get("cols") or []]
        for r in d.get("rows") or []:
            rows.append(f"{r.get('need')}: " + " | ".join(f"{c.get('label')} {c.get('value')}" for c in r.get("cells") or []))
    elif tpl == "kharcha":
        title = f"{lb['title']} · {d.get('product_title') or ''}"
        for ln in d.get("lines") or []:
            tag = lb.get(ln.get("tag") or "", ln.get("tag") or "")
            reason = f" ({ln['reason']})" if ln.get("reason") else ""
            rows.append(f"[{tag}] {ln.get('title')}: {ln.get('amount_str')}{reason}")
        rows.append(f"{lb['zaroori_total']}: {d.get('zaroori_total_str') or ''}")
        if d.get("with_suggestions_total_str"):
            rows.append(f"{lb['with_suggestions_total']}: {d['with_suggestions_total_str']}")
        if d.get("mrp_text"):
            rows.insert(1, str(d["mrp_text"]))
        if d.get("protection"):
            pr = d["protection"]
            rows.append(f"{lb.get('protect', '')}: {pr.get('title')} {pr.get('price_str')} ({pr.get('note')})")
        rows += [str(x) for x in aslist(d.get("included")) + aslist(d.get("notes"))]
        for k in ("exchange_text", "lifetime_text", "stamp"):
            if d.get(k):
                rows.append(str(d[k]))
    elif tpl == "offer":
        title = f"{lb['title']} · {d.get('product_title') or ''}"
        rows.append(f"{lb['price']}: {d.get('price_str') or ''}")
        for o in d.get("offers") or []:
            rows.append(f"{o.get('title')}: {o.get('amount_str')} ({o.get('till') or ''})")
        if d.get("extras_str"):
            rows.append(f"{lb['extras']}: +{d['extras_str']}")
        rows.append(f"{lb['total']}: {d.get('total_str') or ''}")
        for k in ("finance_text", "exchange_text", "stamp"):
            if d.get(k):
                rows.append(str(d[k]))
    elif tpl == "parivaar":
        title = d.get("title") or title
        if d.get("kicker"):
            rows.append(str(d["kicker"]))
        rows += [f"- {x.get('text')}" + (f" ({x['proof']})" if x.get("proof") else "") for x in (d.get("points") or [])[:3]]
        specs = " · ".join(
            f"{s.get('label')}: {s.get('value')}" for s in (d.get("key_specs") or [])[:4] if isinstance(s, dict)
        )
        if specs:
            rows.append(specs)
        rows += [f"{c.get('label')}: {c.get('value')}" for c in (d.get("ladder") or []) if isinstance(c, dict)]
        if d.get("total"):
            rows.append(f"{d.get('total_label') or ''}: {d['total']}")
        elif d.get("no_price"):
            rows.append(str(d["no_price"]))
        rows += [f"{x.get('k')}: {x.get('v')}" for x in (d.get("steps") or []) if isinstance(x, dict)]
        who = " · ".join(str(x) for x in (d.get("associate_name"), d.get("store_name")) if x)
        if who:
            rows.append(who)
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
        st = d.get("stock") or {}
        if st.get("n"):
            rows.append(f"{st.get('n')} {st.get('of') or ''}".strip())
        rows += [f"{x.get('label')}: {x.get('count')} · {x.get('span')}" for x in st.get("tiles") or []]
        rows += [f"• {c.get('text')}" for c in d.get("changes") or []]
        rows += [f"• {o.get('title') or ''} · {o.get('detail') or ''} · {o.get('till') or ''}" for o in d.get("offers") or []]
        if d.get("no_offers"):
            rows.append(str(d["no_offers"]))
        rows += [f"• {k}" for k in d.get("keep") or []]
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
    img = Image.new("RGB", (w, h), "#F4F5F7")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, band_h], fill="#0B1020")
    d.rounded_rectangle([pad, 34, pad + 30, 64], radius=8, fill="#4F46E5")
    d.text((pad + 44, 32), "Reliance Digital", font=f_small, fill="#C7CBD6")
    bw = int(d.textlength("TEST DATA", font=f_small)) + 30
    d.rounded_rectangle([w - pad - bw, 30, w - pad, 68], radius=19, fill="#1E293B")
    d.text((w - pad - bw + 15, 34), "TEST DATA", font=f_small, fill="#9CA3AF")
    y = 92
    for tl in title_lines:
        d.text((pad, y), tl, font=f_title, fill="white")
        y += 70
    box_top = band_h + 30
    d.rounded_rectangle([pad - 16, box_top, w - pad + 16, h - 50], radius=28, fill="#FFFFFF", outline="#E8EAEE", width=2)
    y = box_top + 26
    for ln in body_lines:
        if y > h - 50 - 70:
            d.text((pad + 10, y), "…", font=f_body, fill="#6B7280")
            break
        d.text((pad + 10, y), ln, font=f_body, fill="#1F2937")
        y += 54
    d.text((pad, h - 40), "Reliance Digital", font=f_small, fill="#9CA3AF")
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


@lru_cache(maxsize=1)
def font_css() -> Markup:
    """@font-face for the bundled Montserrat (the product card's typeface, user 5 Nov 2026), inlined so a card draws
    the same on every machine without a system install. Empty when the file is missing (the next font is used)."""
    f = FONTS / "Montserrat-wght.ttf"
    if not f.is_file():
        return Markup("")
    b64 = base64.b64encode(f.read_bytes()).decode()
    return Markup('@font-face { font-family: "Montserrat"; font-style: normal; font-weight: 100 900; '
                  f'src: url(data:font/ttf;base64,{b64}) format("truetype"); }}')


def make_env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))
    env.globals.update(icon=icon, font_css=font_css)
    env.filters.update(
        dv=dv_class, pct=pct, frac=frac, initial=initial, stock_icon=stock_icon, aslist=aslist, icon_name=icon_name
    )
    env.tests["dev"] = is_dev
    return env


# Chromium keeps part of every card it draws (pictures included) for as long as its page process lives, so the
# page process is replaced every RECYCLE_AFTER cards, and sooner when the instance nears its memory limit.
RECYCLE_AFTER = 25
MEMORY_HIGH = 0.8  # share of the instance's memory limit
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--no-first-run",
    "--mute-audio",
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=128",
]


class Renderer:
    """One Chromium page behind a lock (cards are drawn one at a time, ~0.3 s each)."""

    def __init__(self):
        self.env = make_env()
        self.ready = False
        self.pw = None
        self.browser = None
        self.ctx = None
        self.page = None
        self._lock = asyncio.Lock()
        self.renders = 0  # cards drawn by the current page process
        self.fresh_starts = 0
        self.last_ms: dict[str, int] = {}
        self.start_attempted = False

    async def start(self, warm_up: bool = True) -> None:
        self.start_attempted = True
        try:
            from playwright.async_api import async_playwright

            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch(args=CHROMIUM_ARGS)
            await self._fresh()
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
            async with self._lock:
                await self._fresh()  # fonts and templates are warm; start serving from a clean page process

    async def _fresh(self) -> None:
        """A new context and page; closing the old context ends its page process and everything it kept."""
        if not self.browser.is_connected():  # Chromium itself died (e.g. killed under memory pressure)
            self.browser, self.ctx = await self.pw.chromium.launch(args=CHROMIUM_ARGS), None
        old = self.ctx
        self.ctx = await self.browser.new_context(viewport={"width": WIDTH, "height": MAX_HEIGHT}, device_scale_factor=1)
        self.page = await self.ctx.new_page()
        self.renders = 0
        if old is not None:
            self.fresh_starts += 1
            try:
                await old.close()
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
        self.pw = self.browser = self.ctx = self.page = None

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

    def _needs_fresh(self) -> bool:
        if self.page is None or self.page.is_closed() or self.renders >= RECYCLE_AFTER:
            return True
        from chanakya.app import memory

        used = memory.used_fraction()
        if used is not None and used > MEMORY_HIGH and self.renders > 0:
            from chanakya.log import get_log

            get_log("renderer").warning("memory_high_fresh_chromium", renders=self.renders, **memory.snapshot())
            return True
        return False

    async def _render_html(self, card: Card) -> bytes:
        html = self.html(card)
        async with self._lock:
            if self._needs_fresh():
                await self._fresh()
            self.renders += 1
            page = self.page
            try:
                await page.set_content(html, wait_until="load")
                await page.evaluate("document.fonts.ready")
                # scale slightly-too-tall content to ≤ 1350 px (down to MIN_FIT_SCALE); beyond that CSS clips
                await page.evaluate(_FIT_JS, [MAX_HEIGHT, MIN_FIT_SCALE, WIDTH])
                # a locator, not query_selector: an element handle is never freed and keeps the whole card alive
                return await page.locator("#card").screenshot(type="jpeg", quality=88)
            except BaseException:
                self.renders = RECYCLE_AFTER  # a failed or timed-out page is replaced before the next card
                raise

    async def render(self, card: Card) -> bytes:
        t0 = time.perf_counter()
        try:
            if not self.ready:
                return render_pillow(card)
            try:
                await asyncio.wait_for(fetch_images(card), timeout=10)
            except Exception:
                pass  # draw without the pictures that could not be fetched
            try:
                return await asyncio.wait_for(self._render_html(card), timeout=15)
            except Exception as e:  # the plain picture, never nothing; recorded so a fallback is visible on /health
                from chanakya.app import diag

                diag.bump("render_fallbacks")
                diag.mark("render_fallback_last", {"template": card.template, "error": f"{type(e).__name__}: {e}"[:160]})
                return render_pillow(card)
        finally:
            self.last_ms[card.template] = int((time.perf_counter() - t0) * 1000)

    def stats(self) -> dict:
        return {"ready": self.ready, "cards_on_page": self.renders, "fresh_starts": self.fresh_starts}


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
