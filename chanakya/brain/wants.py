"""What the customer asked for about the product itself, from the associate's note ("55 inch Sony chahiye",
"dedh ton 5 star", "7 kg front load", "16 GB RAM"). Read from the words only (any of hi / en / mr), never guessed.

A want is a hard preference when the shortlist is picked: products that meet every want come first. Each matched
want becomes a reason the associate can say ("55 inch, as they asked").

parse_note() reads a whole note clause by clause (user, 4 Nov 2026): what is said about the product they HAVE NOW
("abhi Sony 43 hai", "जुना 32 इंच") is `existing`, never a want; "Samsung nahi" is `avoid`; "naye features" is an
upgrade. Gemini does the same by meaning; this is the offline fallback and the guard on the model's output."""

from __future__ import annotations

import re

SIZE = re.compile(r"\b(\d{2,3})\s*(?:\"|”|inch(?:es)?|in\b|इंच)", re.I)
TON = re.compile(r"\b(\d(?:\.\d)?)\s*(?:ton|टन)\b", re.I)
TON_WORD = re.compile(r"\b(dedh|डेढ़|दीड)\s*(?:ton|टन)", re.I)
KG = re.compile(r"\b(\d{1,2}(?:\.\d)?)\s*(?:kg|kilo|किलो)\b", re.I)
LITRE = re.compile(r"\b(\d{3})\s*(?:l\b|ltr|litre|liter|लीटर|लिटर)", re.I)
STAR = re.compile(r"\b([1-5])\s*(?:star|स्टार)\b", re.I)
RAM = re.compile(r"\b(4|8|12|16|24|32|64)\s*gb\s*(?:ram)?\b", re.I)
PANELS = (("mini led", "MiniLED"), ("miniled", "MiniLED"), ("oled", "OLED"), ("qled", "QLED"))
LOADS = (("front load", "front"), ("front-load", "front"), ("top load", "top"), ("top-load", "top"))


# brands a customer may ask for whether or not the store has them today: an ask for one the catalogue lacks is told
# honestly ('Samsung isn't in today's catalogue'), never silently swapped
KNOWN_BRANDS = ["Samsung", "LG", "Sony", "Panasonic", "Haier", "Whirlpool", "Voltas", "Daikin", "Godrej", "Bosch", "IFB",
                "Hitachi", "Lloyd", "Blue Star", "Xiaomi", "Redmi", "OnePlus", "TCL", "Vu", "Acer", "Asus", "HP", "Dell",
                "Lenovo", "Apple", "Realme", "Toshiba", "Philips", "Onida", "Carrier", "Mitsubishi", "Reconnect",
                "Kelvinator", "Hisense", "Motorola", "Thomson", "MSI"]


def parse(text: str, category: str | None, brands: list[str]) -> dict:
    """{'size_in': 55, 'brand': 'Sony', 'panel': 'OLED', 'ton': 1.5, 'capacity_kg': 7, 'capacity_l': 300,
    'star': 5, 'load': 'front', 'ram_gb': 16} — only what the words say, only keys that fit the category."""
    brands = list(dict.fromkeys([*(brands or []), *KNOWN_BRANDS]))
    t = " " + " ".join((text or "").lower().split()) + " "
    w: dict = {}
    if category in (None, "tv"):
        m = SIZE.search(t)
        if m and 24 <= int(m.group(1)) <= 100:
            w["size_in"] = int(m.group(1))
        for word, panel in PANELS:
            if f" {word} " in t or f" {word}," in t:
                w["panel"] = panel
                break
    if category in (None, "ac"):
        m = TON.search(t)
        if m and 0.5 <= float(m.group(1)) <= 3:
            w["ton"] = float(m.group(1))
        elif TON_WORD.search(t):
            w["ton"] = 1.5
    if category in (None, "wm"):
        m = KG.search(t)
        if m and 5 <= float(m.group(1)) <= 15:
            w["capacity_kg"] = float(m.group(1))
        for word, load in LOADS:
            if word in t:
                w["load"] = load
                break
    if category in (None, "fridge"):
        m = LITRE.search(t)
        if m and 100 <= int(m.group(1)) <= 900:
            w["capacity_l"] = int(m.group(1))
    if category in (None, "ac", "fridge"):
        m = STAR.search(t)
        if m:
            w["star"] = int(m.group(1))
    if category in (None, "laptop"):
        m = RAM.search(t)
        if m:
            w["ram_gb"] = int(m.group(1))
    for b in sorted(brands, key=len, reverse=True):  # the longest brand name first ("LG" inside "Lloyd" etc.)
        if b and re.search(rf"(?<![a-z]){re.escape(b.lower())}(?![a-z])", t):
            w["brand"] = b
            break
    return w


def meets(p, wants: dict) -> list[str]:
    """The wants this product meets (keys); a product meets all when len == len(wants)."""
    ok = []
    for k, v in (wants or {}).items():
        if k == "brand":
            if (p.brand or "").lower() == str(v).lower():
                ok.append(k)
            continue
        have = p.spec(k)
        if have is None:
            continue
        if k == "size_in" and abs(float(have) - float(v)) <= 1.5:
            ok.append(k)
        elif k in ("ton", "capacity_kg") and abs(float(have) - float(v)) <= 0.25:
            ok.append(k)
        elif k == "capacity_l" and abs(float(have) - float(v)) <= 40:
            ok.append(k)
        elif k in ("star", "ram_gb") and float(have) >= float(v):
            ok.append(k)
        elif k in ("panel", "load") and str(have).lower() == str(v).lower():
            ok.append(k)
        elif k not in ("size_in", "ton", "capacity_kg", "capacity_l", "star", "ram_gb", "panel", "load"):
            if (have is v) if isinstance(v, bool) else str(have).lower() == str(v).lower():
                ok.append(k)  # a data-question answer: the same value as the stock carries
    return ok


def meets_all(p, wants: dict) -> bool:
    return bool(wants) and len(meets(p, wants)) == len(wants)


EXISTING_CUE = re.compile(
    r"purana|purani|purane|\bold\b|existing|current|currently|abhi wala|abhi wali|abhi ka\b|already|pehle se|"
    r"unke paas|unka|uska|unki|they have|has a|have a|using|पुराना|पुरानी|पुराने|अभी वाला|जुना|जुनी|जुने|सध्याचा|"
    r"सध्याची|आधीचा|के पास|कडे आहे|"
    r"\babhi\b.*\b(?:hai|he)\b|ghar (?:mein|me|pe)\b.*\b(?:hai|he)\b|अभी.*है|घरी.*आहे", re.I)
WANT_CUE = re.compile(r"chahiye|chaiye|chahte|\bwant|\bneed|\blena\b|leni|lenge|dikhao|हवा|हवी|हवे|चाहिए|लेना|"
                      r"घ्यायच|पाहिजे", re.I)
NEG = r"(?:nahi|nahin|nai|mat|not|no|don'?t|नहीं|नको|नाही)"
UPGRADE = re.compile(r"new features?|naye features?|naya feature|latest|upgrade|\bbetter\b|behtar|"
                     r"naya (?:tv|wala|model)|newer|advanced|नए फ़ीचर|नए फीचर|नया|नवीन|नवे|अपग्रेड|अधिक चांगला", re.I)
SPLIT = re.compile(r"(?:[,;!?|\n]|(?<!\d)\.|\.(?!\d))+|\s(?:aur|but|lekin|par|magar|and|पर|लेकिन|और|आणि|पण)\s",
                   re.I)  # '1.5 ton' is one number, never cut at its point
BARE_TV = re.compile(r"\b(24|28|32|40|42|43|48|50|55|58|65|70|75|85)\b")


def parse_note(text: str, category: str | None, brands: list[str]) -> dict:
    """{wants, existing, avoid, upgrade} from a note, clause by clause (see the module note)."""
    out: dict = {"wants": {}, "existing": {}, "avoid": {}, "upgrade": bool(UPGRADE.search(text or ""))}
    for clause in SPLIT.split(text or ""):
        c = (clause or "").strip()
        if not c:
            continue
        got = parse(c, category, brands)
        ex = bool(EXISTING_CUE.search(c))
        if ex and "size_in" not in got and category in (None, "tv"):
            m = BARE_TV.search(c)  # "abhi Sony 43 hai": a bare size about the TV they have
            if m:
                got["size_in"] = int(m.group(1))
        if not got:
            continue
        b = got.get("brand")
        if b and re.search(rf"{NEG}\W+(?:\w+\W+){{0,2}}{re.escape(b)}\b|\b{re.escape(b)}\W+(?:\w+\W+){{0,2}}{NEG}\b", c, re.I):
            out["avoid"]["brand"] = got.pop("brand")  # "Samsung nahi chahiye"
        if ex and WANT_CUE.search(c) and "size_in" in got:  # "purane 32 ki jagah 55 chahiye": old first, new last
            sizes = [int(x) for x in SIZE.findall(c.lower()) if 24 <= int(x) <= 100]
            if len(sizes) >= 2:
                out["existing"]["size_in"], got["size_in"], ex = sizes[0], sizes[-1], False
        out["existing" if ex else "wants"].update(got)
    return clean(out)


def clean(out: dict) -> dict:
    """Never want what they already have (the same size + brand), and never want what they ruled out."""
    w, e, a = out.get("wants") or {}, out.get("existing") or {}, out.get("avoid") or {}
    if e and all(w.get(k) == e.get(k) for k in w if k in ("size_in", "brand")) and set(w) <= {"size_in", "brand"}:
        w = {}  # only the old one's facts said back: nothing new asked
    if a.get("brand") and str(w.get("brand", "")).lower() == str(a["brand"]).lower():
        w.pop("brand", None)
    out["wants"] = w
    return out
