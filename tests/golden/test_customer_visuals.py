"""Customer-facing visuals and the details the associate asked for after the first phone test:
- every customer-facing visual in English, plus one copy in the associate's language (none when that is English);
- the options card built from this home (needs grid, benefits with proof), never a generic feature list;
- the bill: 'Show customer' always leaves the associate a next step;
- units and technical terms in English in every language; Devanagari for Hindi customer wording;
- photo labels drawn per script (no empty boxes), the live-sports question, the voice safety net."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from types import SimpleNamespace

import yaml

from chanakya.brain.packs import load_packs
from chanakya.config import ROOT
from chanakya.sim.harness import PHONES
from chanakya.tryon import fonts
from tests.helpers import button_ids, check_floor, pick, to_options

PACKS = load_packs()
NOW = datetime(2026, 11, 1, tzinfo=UTC)
UNIT_WORDS = re.compile(r"(?<![ऀ-ॿ])(इंच|फ़ुट|फुट|फूट|फ़ीट|फीट|सेमी|किलो|लीटर|लिटर|टन|घंटे|तास|वॉट|स्टार|निट्स)(?![ऀ-ॿ])")


async def session(h):
    return await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")


async def test_options_card_is_english_with_a_copy_and_built_from_this_home(h):
    out = await to_options(h)
    m = out[0]
    check_floor(out)
    assert m.card.template == "options" and m.card.lang == "en"
    assert m.card_copy is not None and m.card_copy.lang == "hi" and m.copy_caption  # the associate's language
    d = m.card.data
    best = next(o for o in d["options"] if o["is_best"])
    assert best["points"] and all(p["text"] for p in best["points"])
    assert any(p["proof"] for p in best["points"])  # each benefit backed by the product's own spec
    needs = [r["need"] for r in d["grid"]]
    assert needs and needs[0].startswith("Size for")  # the customer's own distance, the heaviest need first
    assert all(len(r["cells"]) == len(d["options"]) for r in d["grid"])
    assert d["home"]  # their answers, as chips
    others = [o for o in d["options"] if not o["is_best"]]
    assert all(o["role"] in ("value", "step") for o in others)
    assert "price" not in str(d).lower()


async def test_show_customer_keeps_a_next_step_and_bill_is_english(h):
    await to_options(h)
    await h.tap("s:C1:shown")
    out = await pick(h)
    k = next(m for m in out if m.card and m.card.template == "kharcha")
    assert k.card.lang == "en" and k.card_copy and k.card_copy.lang == "hi"
    out = await h.tap("s:C1:cost_show")
    check_floor(out)
    m = out[0]
    assert m.card.template == "kharcha" and m.card.lang == "en"
    assert button_ids(out) == ["s:C1:offers", "s:C1:cost_rm", "s:C1:nottoday"]  # never a dead end
    assert m.footer


async def test_english_associate_gets_no_copy(h):
    await h.say("lang en")
    out = await to_options(h, clang="en")
    assert out[0].card.lang == "en" and out[0].card_copy is None


async def test_product_card_is_one_english_card_for_both(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:mr")  # Hindi associate, Marathi customer
    for photo in ("label_meru55.jpg", "label_meru55.jpg"):  # before and after the home is understood
        out = await h.photo(photo)
        m = next(m for m in out if m.card and m.card.template == "product")
        assert m.card.lang == "en" and m.card_copy is None and m.copy_caption is None
        assert sum(1 for x in out if x.card) == 1  # never a second product card in another language
        await h.voice("assess_1")


async def test_offer_and_tryon_have_copies(h):
    await to_options(h, photo="room_living.jpg")
    out = await h.tap("s:C1:tryon")
    img = next(m for m in out if m.image_bytes)
    assert img.image_copy and img.image_copy != img.image_bytes  # same drawing, words in the associate's language
    await h.tap("s:C1:shown")
    await pick(h)
    await h.tap("s:C1:offers")
    out = await h.tap("s:C1:pay:upi")
    oc = next(m for m in out if m.card and m.card.template == "offer")
    assert oc.card.lang == "en" and oc.card_copy.lang == "hi"
    assert oc.card.data["total_str"] == oc.card_copy.data["total_str"]  # same numbers in both


def test_units_and_terms_stay_english_in_display_text():
    files = [*(ROOT / "chanakya/brain/packs").glob("*.yaml"), *(ROOT / "chanakya/i18n").glob("*.yaml"),
             ROOT / "data/seed/products.yaml", ROOT / "data/seed/charges.yaml", ROOT / "data/seed/offers.yaml"]
    bad = []
    for f in files:
        skip = None
        for line in f.read_text().splitlines():
            ind, st = len(line) - len(line.lstrip(" ")), line.strip()
            if skip is not None and (not st or ind > skip):
                continue
            skip = None
            if re.match(r"(- )?keywords:\s*$", st):
                skip = ind
                continue
            if "keywords: [" in st:
                continue  # what the associate may SAY stays understood in Hindi/Marathi
            if UNIT_WORDS.search(line):
                bad.append(f"{f.name}: {st[:80]}")
    assert not bad, bad[:5]


def test_every_hindi_answer_has_a_devanagari_customer_version():
    vh = yaml.safe_load((ROOT / "chanakya/i18n/values_hi.yaml").read_text())
    missing = [t["hi"] for p in PACKS.packs.values() for sd in p.slots for t in sd.value_titles.values()
               if t.get("hi") and t["hi"] not in vh]
    assert not missing, missing[:5]


def test_every_fit_rule_has_a_need_in_every_language():
    for p in PACKS.packs.values():
        for r in p.fit_rules:
            assert all(p.needs.get(r.id, {}).get(lg) for lg in ("hi", "mr", "en")), (p.category, r.id)


def test_photo_labels_split_by_script():
    assert fonts.runs('43" · Arka 4K') == [('43" · Arka 4K', False)]
    r = fonts.runs("सोफ़े से 7 ft")
    assert r[0] == ("सोफ़े से ", True) and r[1] == ("7 ft", False)
    r = fonts.runs("अनुमानित झलक · सही अनुपात में · TEST DATA")
    assert ("· ", False) in r  # punctuation goes to the Latin font (Devanagari fonts may not have it)
    assert all(not any(fonts.is_deva(c) for c in chunk) for chunk, d in r if not d)


async def test_live_sports_question_and_exclusive_answers(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.pick("s:C1:cat:tv")
    for bid in ("s:C1:slot:trigger:second", "s:C1:slot:room:living", "s:C1:slot:seat_distance:d8_10",
                "s:C1:slot:room_light:sunny"):
        out = await h.tap(bid)
    assert button_ids(out) == ["s:C1:slot:content:sports", "s:C1:slot:content:sports_some", "s:C1:slot:content:few_sports"]
    assert "cricket" in out[0].body.lower()
    from chanakya.brain import packs as P

    s = await session(h)
    pack = PACKS.get("tv")
    P.set_slot(s.home, pack, "content", "sports", None, "tap", NOW)
    P.set_slot(s.home, pack, "content", "few_sports", None, "tap", NOW)
    assert P.values(s.home, "content") == ["few_sports"]  # how often: the latest answer replaces the earlier one


async def test_voice_safety_net_transcribes_then_understands():
    from chanakya.ai.client import AIClient
    from chanakya.config import Settings

    class Models:
        def __init__(self):
            self.n = 0

        async def generate_content(self, model, contents, config):
            self.n += 1
            if config.response_schema is not None:
                raise RuntimeError("schema rejected")
            return SimpleNamespace(text="sofa se das feet, dhoop aati hai, har match dekhte hain")

    ai = AIClient(Settings(_env_file=None, ai_mode="live", gemini_api_key="k"), PACKS)
    ai.gemini.client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    u = await ai.understand(None, b"OggS", {"category": "tv"}, mime="audio/ogg")
    assert "dhoop" in u.transcript
    assert ("room_light", "sunny") in {(s.slot, s.value) for s in u.slots}
