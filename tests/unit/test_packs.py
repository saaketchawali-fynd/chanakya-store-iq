import re

import yaml

from chanakya.brain.packs import HERE, load_packs, placeholders
from chanakya.i18n.lang import strings

ALLOWED_PH = {
    "spec_value",
    "who",
    "size",
    "distance",
    "apps",
    "kg",
    "ton",
    "sqft",
    "people",
    "hours",
    "monthly_bill",
    "five_year_saving",
    "steps",
    "best",
    "other",
    "valid_to",
    "low",
    "high",
    "monthly",
    "months",
    "fee",
    "total",
    "extra",
    "day1",
    "day2",
}


def test_packs_load_and_validate():
    r = load_packs()
    assert set(r.packs) == {"tv", "ac", "wm", "laptop"}
    for p in r.packs.values():
        for s in p.slots:
            assert len(s.quick) <= 3
            for q in s.quick:
                assert all(len(q.title[lg]) <= 20 for lg in ("hi", "mr", "en"))
            for v in s.values:
                assert v in s.value_titles, (p.category, s.id, v)
        assert p.gate_min_slots <= len(p.required_slots)


def test_placeholders_resolvable():
    r = load_packs()
    spec_keys = set()
    for row in yaml.safe_load((HERE.parent.parent.parent / "data/seed/products.yaml").read_text()):
        spec_keys |= set(row.get("specs", {})) | set(row.get("dims", {})) | set(row.get("energy", {}))
    for p in r.packs.values():
        texts = []
        for b in p.benefits:
            texts += list(b.say.values()) + list(b.say_who.values()) + list(b.card.values())
        for d in p.demos:
            texts += list(d.say.values()) + list(d.steps.values())
        for t in texts:
            for ph in placeholders(t):
                assert ph in ALLOWED_PH or ph in spec_keys, (p.category, ph, t)


def test_buttons_le_20_and_no_devanagari_digits():
    for key, d in strings().items():
        for lg, s in d.items():
            assert not re.search(r"[०-९]", s), key
            if key.startswith(("b_", "cb_")) and "{" not in s:
                assert len(s) <= 20, (key, lg, s)
