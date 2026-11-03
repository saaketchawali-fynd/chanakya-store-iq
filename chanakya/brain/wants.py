"""What the customer asked for about the product itself, from the associate's note ("55 inch Sony chahiye",
"dedh ton 5 star", "7 kg front load", "16 GB RAM"). Read from the words only (any of hi / en / mr), never guessed.

A want is a hard preference when the shortlist is picked: products that meet every want come first. Each matched
want becomes a reason the associate can say ("55 inch, as they asked")."""

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


def parse(text: str, category: str | None, brands: list[str]) -> dict:
    """{'size_in': 55, 'brand': 'Sony', 'panel': 'OLED', 'ton': 1.5, 'capacity_kg': 7, 'capacity_l': 300,
    'star': 5, 'load': 'front', 'ram_gb': 16} — only what the words say, only keys that fit the category."""
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
