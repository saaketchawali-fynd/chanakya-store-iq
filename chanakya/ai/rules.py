"""Offline understanding (no AI model): deterministic keyword + number extraction for associate text.

Uses the `keywords` lists in the category packs and common.yaml. Extracts only what is said; budget only
when a budget word is present (hard rule: never infer money from anything else)."""

from __future__ import annotations

import re

from chanakya.ai.schemas import NumberPair, SlotPair, Understanding
from chanakya.brain.packs import Pack, PackRegistry
from chanakya.truth.fmt import latin_digits

WORD_NUM = {
    # Hindi / Hinglish (Roman)
    "ek": 1,
    "do": 2,
    "teen": 3,
    "char": 4,
    "chaar": 4,
    "paanch": 5,
    "panch": 5,
    "chhe": 6,
    "che": 6,
    "saat": 7,
    "aath": 8,
    "nau": 9,
    "das": 10,
    "gyarah": 11,
    "barah": 12,
    "baarah": 12,
    "pandrah": 15,
    "bees": 20,
    "pachees": 25,
    "tees": 30,
    "chaalis": 40,
    "chalis": 40,
    "pachaas": 50,
    "pachas": 50,
    "saath": 60,
    "sattar": 70,
    "assi": 80,
    "nabbe": 90,
    "sau": 100,
    # English
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    # Devanagari Hindi / Marathi
    "एक": 1,
    "दो": 2,
    "दोन": 2,
    "तीन": 3,
    "चार": 4,
    "पाँच": 5,
    "पांच": 5,
    "पाच": 5,
    "छह": 6,
    "सहा": 6,
    "सात": 7,
    "आठ": 8,
    "नौ": 9,
    "नऊ": 9,
    "दस": 10,
    "दहा": 10,
    "बारह": 12,
    "बारा": 12,
    "पचास": 50,
    "पन्नास": 50,
    "साठ": 60,
    "सत्तर": 70,
    "सौ": 100,
}
THOUSAND = r"(?:k|hazaar|hazar|hajar|thousand|हज़ार|हजार)"
LAKH = r"(?:lakh|lac|लाख)"
NUM = r"(\d+(?:\.\d+)?)"
FT = r"(?:ft|feet|foot|fut|feet ka|फ़ुट|फुट|फीट|फूट)"
STEPS = r"(?:kadam|qadam|steps|step|कदम|पावलं|पावले|पाऊल)"
BUDGET_WORDS = ("budget", "बजट", "बजेट", "range", "tak ka", "tak ki", "पर्यंत", "तक का", "socha hai", "soch rahe")
# store rule 3 (contest brief): never guess wealth from appearance; the associate sets the start level. A budget is
# taken from a note only when it was said as a budget, never from what the customer drives, wears or looks like.
WEALTH_CUES = ("car", "gaadi", "gadi", "bmw", "mercedes", "audi", "kapde", "kapda", "clothes", "branded", "jewel",
               "gehne", "gehna", "watch", "ghadi", "ameer", "amir", "rich", "paise wale", "paisewale", "paisa wala",
               "salary", "income", "kamate", "bungalow", "villa", "dikhte", "lagte", "dikh rahe", "lag rahe",
               "गाडी", "कपडे", "श्रीमंत", "अमीर", "दिखते", "लगते", "दिसतात", "वाटतात")


# a budget said without the word (user, 6 Nov 2026: the needs by voice, understood on its own): an amount followed
# by 'tak' / 'ke andar' / 'पर्यंत' / 'aas paas', or after 'under' / 'around' / 'lagbhag'. Never a price seen elsewhere.
BUDGET_AFTER = re.compile(r"^\s*(?:tak|tk|ke andar|andar|ke under|tak ka|tak ki|ke aas ?paas|aas ?paas|ke around|ki range|"
                          r"तक|पर्यंत|च्या आत|के आसपास|आसपास|के अंदर)")
BUDGET_BEFORE = re.compile(r"(?:under|upto|up to|around|about|approx\.?|approximately|lagbhag|within|below|max|maximum|"
                           r"kareeb|लगभग|करीब|साधारण)\s*$")
PRICE_SEEN = ("online", "amazon", "flipkart", "website", "dusri dukaan", "doosri dukaan", "mil raha", "mil rha", "dekha",
              "ऑनलाइन", "बघितल", "देखा")


def _clauses(t: str) -> list[str]:
    return re.split(r"[.,;!?|।]| aur | but | lekin | पण ", t)


def _wealthy(clause: str) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(c)}(?!\w)", clause) for c in WEALTH_CUES)


def budget_said(t: str) -> float | None:
    """The amount said as the customer's budget without the word 'budget' ('35 hazaar tak', 'around 35000'), in a
    clause that is not about a price seen elsewhere (online, another shop) nor about their car, clothes or looks."""
    for clause in _clauses(t):
        if any(w in clause for w in PRICE_SEEN) or _wealthy(clause):
            continue
        for v, start, end in _amount_spans(clause):
            if BUDGET_AFTER.match(clause[end:end + 18]) or BUDGET_BEFORE.search(clause[max(0, start - 16):start]):
                return v
    return None


def explicit_budget(transcript: str | None) -> bool:
    """True when the note says a budget as a budget ('budget 50 hazaar', '50 hazaar tak', 'around 35000'), and that
    clause is not about the customer's car, clothes, jewellery or looks. With no transcript to check (a test fixture),
    True."""
    if not transcript:
        return True
    t = _norm(transcript)
    if not any(w in t for w in BUDGET_WORDS):
        return budget_said(_words_to_digits(t)) is not None
    for clause in _clauses(t):
        if any(w in clause for w in BUDGET_WORDS) and _wealthy(clause):
            return False
    return True


QUESTION_HINTS = ("kya ", "kya?", "?", "does ", "is it", "support", "का?", "क्या", "आहे का", "kaisa")


def _norm(text: str) -> str:
    t = latin_digits(text or "").lower()
    t = re.sub(r"[₹,]", lambda m: "" if m.group(0) == "," else " ₹", t)
    return " " + re.sub(r"\s+", " ", t).strip() + " "


def _words_to_digits(t: str) -> str:
    def rep(m):
        return str(WORD_NUM[m.group(0).strip()])

    keys = sorted(WORD_NUM, key=len, reverse=True)
    pattern = r"(?<![\wऀ-ॿ])(" + "|".join(re.escape(k) for k in keys) + r")(?![\wऀ-ॿ])"
    return re.sub(pattern, rep, t)


def _has(t: str, phrases: list[str]) -> str | None:
    best = None
    for p in phrases or []:
        p2 = " " + p.lower().strip() + " " if p.isascii() else p.lower().strip()
        target = t if p.isascii() else t.strip()
        if p2 in target or (not p.isascii() and p.lower() in target):
            if best is None or len(p) > len(best):
                best = p
    return best


def _amount_spans(t: str) -> list[tuple[float, int, int]]:
    """Money-like amounts: '50000', '50k', '50 hazaar', '1.2 lakh', '₹45,000' → (value, start, end)."""
    out = []
    for m in re.finditer(NUM + r"\s*" + LAKH, t):
        out.append((float(m.group(1)) * 100000, m.start(), m.end()))
    for m in re.finditer(NUM + r"\s*" + THOUSAND, t):
        out.append((float(m.group(1)) * 1000, m.start(), m.end()))
    for m in re.finditer(r"(?<![\d.])(\d{4,7})(?![\d.])", t):
        v = float(m.group(1))
        if 5000 <= v <= 900000:
            out.append((v, m.start(), m.end()))
    return sorted(out, key=lambda a: a[1])


def _amount(t: str) -> list[tuple[float, int]]:
    return [(v, start) for v, start, _ in _amount_spans(t)]


def extract_numbers(t: str, category: str | None) -> list[NumberPair]:
    nums: dict[str, float] = {}
    for m in re.finditer(NUM + r"\s*" + STEPS, t):
        nums.setdefault("seat_distance_ft", round(float(m.group(1)) * 2.5, 1))
    for m in re.finditer(NUM + r"\s*" + FT, t):
        v = float(m.group(1))
        ctx = t[max(0, m.start() - 25) : m.end() + 25]
        if re.search(r"pipe|पाइप|पाईप", ctx):
            nums["pipe_run_ft"] = v
        elif re.search(r"wall|deewar|diwar|दीवार|भिंत|chaudi|रुंद|wide", ctx) and not re.search(r"sofa|सोफ", ctx):
            nums["wall_width_ft"] = v
        elif category in (None, "tv"):
            nums.setdefault("seat_distance_ft", v)
    m = re.search(NUM + r"\s*[x×*]\s*" + NUM, t) or re.search(NUM + r"\s*(?:by|बाय)\s*" + NUM, t)
    if m:
        nums["room_size_sqft"] = float(m.group(1)) * float(m.group(2))
    m = re.search(NUM + r"\s*(?:sq\.? ?ft|sqft|square feet|sq feet|स्क्वेअर|वर्ग फ़ुट|वर्ग फुट)", t)
    if m:
        nums["room_size_sqft"] = float(m.group(1))
    m = re.search(NUM + r"\s*(?:log|logon|members|member|people|जन|जण|लोग|लोक|लोकं)\b", t) or re.search(
        r"family (?:of )?" + NUM, t
    )
    if m:
        nums["family_size"] = float(m.group(1))
    m = re.search(NUM + r"\s*(?:ghante|ghanta|hours|hour|hrs|घंटे|घंटा|तास)", t)
    if m:
        nums["hours_per_day"] = float(m.group(1))
    m = re.search(NUM + r"\s*(?:cm|सेमी|से.मी.)", t)
    if m:
        nums["niche_width_cm"] = float(m.group(1))
    m = re.search(NUM + r"\s*(?:st|nd|rd|th)?\s*(?:floor|manzil|मंज़िल|मंजिल|मजला|मजल्यावर)", t)
    if m:
        nums["floor_number"] = float(m.group(1))
    if any(w in t for w in BUDGET_WORDS):
        amts = _amount(t)
        if amts:
            pos = min((t.find(w) for w in BUDGET_WORDS if w in t), default=0)
            nums["budget_inr"] = min(amts, key=lambda a: abs(a[1] - pos))[0]
    else:
        said = budget_said(t)
        if said is not None:
            nums["budget_inr"] = said
    return [NumberPair(name=k, value=v) for k, v in nums.items()]  # type: ignore[arg-type]


# 2. the product, when the note does not name it (user, 6 Nov 2026: "when the associate defines the home conditions,
# reqs etc by voice note ... it should be able to understand and give the best fit products on its own")
CATEGORY_CUES: dict[str, tuple[str, ...]] = {
    "tv": (r"sofa", "सोफ", r"\binch", "इंच", r"cricket", "क्रिकेट", r"\bmatch\b", "मॅच", "मैच", r"netflix", r"\bott\b",
           r"youtube", r"serial", "सीरियल", "मालिका", r"channel", "चैनल", "चॅनेल", r"\bmovies?\b", "सिनेमा", r"\bnews\b"),
    "ac": (r"sq\.? ?ft", r"sqft", r"square f", "स्क्वेअर", "वर्ग फ", r"garmi", "गर्मी", "उकाडा", r"\btons?\b", "टन",
           r"top floor", r"humid", r"\bdhoop wala kamra"),
    "wm": (r"\bkg\b", "किलो", r"kapd", "कपड", r"bedsheet", "चादर", r"towel", r"\bdhul", r"\bdhot", "धुला", "धुण", "धुत",
           r"washing", r"jeans", r"uniform"),
    "fridge": (r"lit(?:re|er)", "लीटर", "लिटर", r"sab[zj]i", r"vegetable", "भाजी", "सब्ज़ी", "सब्जी", r"freezer", r"\bbarf\b",
               r"\bice\b", r"doodh", r"\bmilk\b", "दूध", r"khana ban", r"\bcook", "स्वयंपाक", "खाना बन", r"leftover"),
    "laptop": (r"college", "कॉलेज", r"office work", r"coding", r"gaming", r"\bram\b", r"\bgb\b", r"\bzoom\b",
               r"online class", r"excel", r"programming", r"assignment"),
}


def infer_category(text: str, packs: PackRegistry) -> str | None:
    """The product the note is about: named ('TV', 'एसी'), else read from the needs when they clearly point to one
    product (sofa distance and cricket → TV; sq ft and heat → AC; clothes and kg → washing machine; vegetables and
    litres → fridge; college and coding → laptop). None when it is not clear: then the associate is asked."""
    t = _norm(text)
    for c, words in (packs.common.get("category_keywords") or {}).items():
        if _has(t, words):
            return c
    score = {}
    for c in packs.packs:
        cues = sum(1 for rx in CATEGORY_CUES.get(c, ()) if re.search(rx, t))
        score[c] = len({sp.slot for sp in extract_slots(t, packs.get(c))}) + 3 * min(cues, 3)
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < 4 or (len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 3):
        return None
    return ranked[0][0]


def extract_slots(t: str, pack: Pack) -> list[SlotPair]:
    out: list[SlotPair] = []
    for s in pack.slots:
        hits = []
        for val, phrases in (s.keywords or {}).items():
            p = _has(t, phrases)
            if p:
                hits.append((len(p), val))
        if not hits:
            continue
        if s.multi:
            out += [SlotPair(slot=s.id, value=v) for _, v in sorted(hits, reverse=True)]
        else:
            out.append(SlotPair(slot=s.id, value=max(hits)[1]))
    return out


def understand_text(
    text: str, packs: PackRegistry, category: str | None, myth_ids: list[str] | None = None, options_shown: bool = False,
    brands: list[str] | None = None,
) -> Understanding:
    raw = text or ""
    if category not in packs.packs:  # e.g. a department of "all": treat as not yet known
        category = None
    t0 = _norm(raw)  # keyword matching (number words kept as words)
    t = _words_to_digits(t0)  # number extraction
    common = packs.common
    u = Understanding(transcript=raw.strip(), language="hi")
    if re.search(r"[ऀ-ॿ]", raw):
        u.language = "mr" if re.search(r"(आहे|नाही|आहेत|करा|हवा|हवी|किती|खोली|घरात|ळ)", raw) else "hi"
    elif re.search(r"\b(the|is|and|want|room|customer wants)\b", t) and not re.search(r"\b(hai|hain|ka|ki|ke)\b", t):
        u.language = "en"

    # category
    cat = None
    for c, words in (common.get("category_keywords") or {}).items():
        if _has(t, words):
            cat = c
            break
    if cat is None and category is None:  # the product not named and not known yet: read it from the needs
        cat = infer_category(raw, packs)
    u.category = cat  # type: ignore[assignment]
    pack = packs.get(cat or category) or packs.get("tv")

    # customer language (only if stated)
    for lg, words in (common.get("customer_lang_keywords") or {}).items():
        if _has(t, words):
            u.customer_language = lg  # type: ignore[assignment]

    # name: "Shah ji", "Mrs Shah", "शाह जी"
    m = re.search(r"\b([A-Z][a-z]{2,})\s+(?:ji|Ji)\b", raw) or re.search(r"\b(?:Mrs|Mr|Ms)\.?\s+([A-Z][a-z]{2,})", raw)
    if m:
        u.customer_name = f"{m.group(1)} ji"
    else:
        m = re.search(r"([ऀ-ॿ]{2,})\s+जी", raw)
        if m and m.group(1) not in ("दादा", "दादी", "पिता", "माता"):
            u.customer_name = f"{m.group(1)} जी"

    u.numbers = extract_numbers(t, cat or category)
    if pack:
        seen: set[tuple[str, str]] = set()
        for sp in extract_slots(t0, pack) + extract_slots(t, pack):
            single = not (pack.slot(sp.slot) and pack.slot(sp.slot).multi)
            if (sp.slot, sp.value) in seen or (single and any(x.slot == sp.slot for x in u.slots)):
                continue
            seen.add((sp.slot, sp.value))
            u.slots.append(sp)

    for who, words in (common.get("who_words") or {}).items():
        if _has(t, words):
            u.sports_viewer = who  # type: ignore[assignment]
            break

    intents: list[str] = []
    ik = common.get("intent_keywords") or {}
    for intent in (
        "new_customer",
        "price_asked",
        "request_options",
        "request_cost",
        "request_offers",
        "demo_done",
        "consent_yes",
        "demo_next",
        "request_tryon",
        "sold",
        "not_today",
        "lost",
        "request_delivery",
        "request_emi",
        "send_family_card",
        "set_reminder",
        "next_step",
    ):
        if _has(t0, ik.get(intent, [])):
            intents.append(intent)
    # myths (customer claims)
    myth_pool = packs.all_myths(cat or category)
    for my in myth_pool:
        if myth_ids is not None and my.id not in myth_ids:
            continue
        if _has(t0, my.keywords):
            u.claim_id = my.id
            intents.append("customer_claim")
            break
    # objections
    for oid, ob in (common.get("objections") or {}).items():
        if isinstance(ob, dict) and _has(t0, ob.get("keywords", [])):
            u.objection = oid  # type: ignore[assignment]
            intents.append("objection")
            break
    if u.objection == "cheaper_online" and u.claim_id == "gen_online_cheaper":
        u.claim_id = None
    # option choice: "B wala", "option B", "B pasand"
    m = re.search(r"(?:option\s+|ऑप्शन\s*)([abc])\b", t) or re.search(
        r"\b([abc])\s+(?:wala|vala|wali|pasand|chahiye|lenge|लेंगे|हवा|घेणार|final)", t
    )
    if m and options_shown:
        u.option_choice = m.group(1).upper()  # type: ignore[assignment]
        intents.append("choose_option")
    # requests: "55 inch QLED chahiye"
    m = re.search(r"(\d{2})\s*(?:inch|inches|इंच|\")", t)
    if m and re.search(r"chahiye|chaiye|want|हवा|हवी|चाहिए|lena|लेना|घ्यायचा", t):
        u.customer_request = raw.strip()[:80]
    # the product, by clause: what they want, what they have now, what they rule out, the needs it must meet
    from chanakya.ai.schemas import WantPair
    from chanakya.brain import highlights as HL
    from chanakya.brain import wants as W

    note = W.parse_note(raw, cat or category, brands or [])
    keys = set(WantPair.model_fields["key"].annotation.__args__)
    for name in ("wants", "existing", "avoid"):
        setattr(u, name, [WantPair(key=k, value=str(v)) for k, v in note[name].items() if k in keys])
    u.upgrade = note["upgrade"]
    u.asks = HL.asks_in(raw, cat or category)
    if u.slots or u.numbers or u.customer_name or u.asks or u.existing:
        intents.append("customer_info")
    # unmapped product question → expert
    if not (u.slots or u.claim_id or u.objection or intents) and any(h in t for h in QUESTION_HINTS):
        u.question_for_expert = raw.strip()[:200]
        intents.append("ask_question")
    u.intents = list(dict.fromkeys(intents or ["other"]))  # type: ignore[assignment]
    return u


def contains_any(text: str, phrases: list[str]) -> bool:
    return _has(_norm(text), phrases) is not None
