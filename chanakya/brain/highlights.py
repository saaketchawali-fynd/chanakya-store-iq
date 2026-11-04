"""Saral Samjhao from the backend's own words (user, 4 Nov 2026): the product's highlights → what it means at home,
for THIS customer.

Jio carries few spec fields a benefit rule can read (no brightness, refresh rate or dialogue flags), but every
product lists its highlights: "ClearAudio+: Hear music & dialogue with better clarity", "HDR10+", "Chromecast",
"Coolpack 12 Hours keeps food frozen", "Microsoft Office included: Yes". A theme (highlights.yaml) is found on a
product when one of its own highlights matches a theme line; it matters when the customer's answers switch one of
the theme's needs on. The words said are the theme line's, and the highlight is quoted as the proof (callers
register it with the source highlight:<sku>). Nothing is claimed that the product's own highlight does not say.

Used by the label scan, the options card and the "Made for your home" card (points), the fit score and the
question picker (contributions / slot_informative: an answer that a highlight can act on is worth asking), and
the demo plan and option reasons (differences)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from chanakya.brain import packs as P

HERE = Path(__file__).with_name("highlights.yaml")
NEGATIVE = re.compile(r":\s*(no|none|nil|n/?a|not (included|available))\s*$", re.I)  # "Office included: No"
GENERIC_KEY = re.compile(r"^(processor|ram|internal storage|storage( type)?|operating system|display|battery|graphics|"
                         r"screen|cooling type|key features?)$", re.I)
STRONG = 1.5  # a need this strong makes its question worth asking (data_questions.skip_slots)
FIT_SCALE = 0.75  # a highlight need weighs a little less in the fit than a spec rule of the same weight
PROOF_MAX = 40


@dataclass(frozen=True)
class Line:
    rx: re.Pattern
    say: dict


@dataclass(frozen=True)
class Need:
    id: str
    slot: str | None
    values: tuple
    gte: float | None
    want: str | None
    w: float


@dataclass(frozen=True)
class Theme:
    id: str
    lines: tuple[Line, ...]
    needs: tuple[Need, ...]
    covers: tuple[str, ...]
    general: float
    demo: dict
    label: str = ""
    specs: tuple = ()  # ({key, line, proof?}, …): spec fields that show it when no highlight does
    keywords: tuple = ()  # words that name this need in a note (offline rules)
    demo_one: dict | None = None  # the demo when there is only one product (no "on both")


@dataclass(frozen=True)
class Found:
    theme: str
    line: Line
    text: str  # the highlight, as the backend wrote it
    proof: str  # its short form, quoted to the customer


@dataclass(frozen=True)
class Point:
    theme: str
    need: str | None  # the need it answers (prefix id); None = a general point ("also worth knowing")
    weight: float
    say: dict  # the daily-life words {hi, mr, en}
    proof: str
    text: str
    demo: dict


_DATA: dict | None = None
_FOUND: dict[tuple, dict[str, Found]] = {}


def _data() -> dict:
    global _DATA
    if _DATA is None:
        raw = yaml.safe_load(HERE.read_text(encoding="utf-8"))
        themes: dict[str, list[Theme]] = {}
        for cat, rows in (raw.get("themes") or {}).items():
            out = []
            for t in rows:
                lines = tuple(Line(re.compile(li["rx"], re.I), {k: li[k] for k in ("hi", "mr", "en") if k in li})
                              for li in t.get("lines") or [])
                needs = tuple(Need(n["id"], n.get("slot"), tuple(str(v) for v in n.get("in") or ()), n.get("gte"),
                                   n.get("want"), float(n.get("w", 1))) for n in t.get("needs") or [])
                out.append(Theme(t["id"], lines, needs, tuple(t.get("covers") or ()), float(t.get("general", 0.3)),
                                 t.get("demo") or {}, t.get("label") or t["id"], tuple(t.get("specs") or ()),
                                 tuple(str(k).lower() for k in t.get("keywords") or ()), t.get("demo_one")))
            themes[cat] = out
        _DATA = {"prefix": raw.get("prefix") or {}, "say": raw.get("say") or {}, "themes": themes}
    return _DATA


def themes(category: str | None) -> list[Theme]:
    return _data()["themes"].get(category or "", [])


def theme(category: str | None, theme_id: str) -> Theme | None:
    return next((t for t in themes(category) if t.id == theme_id), None)


def texts(p) -> list[str]:
    """The product's own highlight lines (English as the backend wrote them); a negative one ("…: No") is dropped."""
    out: list[str] = []
    for f in (getattr(p, "info", None) or {}).get("features") or []:
        t = f.get("en") or next(iter(f.values()), "") if isinstance(f, dict) else str(f or "")
        t = " ".join(str(t).split()).strip(" ,;")
        if t and not NEGATIVE.search(t) and t not in out:
            out.append(t)
    return out


def proof_of(text: str) -> str:
    """The short form of a highlight to quote: "ClearAudio+: Hear music…" → "ClearAudio+"; "Hexa Drive - Natural…"
    → "Hexa Drive"; "Microsoft Office included: Yes" → "Microsoft Office included"; "RAM: 16 GB" → "16 GB"."""
    t = " ".join(text.split()).strip(" ,;.")
    if ":" in t:
        k, v = (x.strip() for x in t.split(":", 1))
        if v.lower() in ("yes", "true", "y"):
            t = k
        elif GENERIC_KEY.match(k) and v:
            t = v
        elif len(k) >= 3:
            t = k
    elif re.search(r"\s[-–]\s", t):
        t = re.split(r"\s[-–]\s", t, maxsplit=1)[0].strip()
    if len(t) > PROOF_MAX:
        cut = t[: PROOF_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,;(")
        t = (cut or t[: PROOF_MAX - 1]) + "…"
    return t


PART = re.compile(r",\s*(?![^()]*\))")  # "Wireless display, IPS panel" → two highlights (not inside brackets)


def _strong(tok: str) -> bool:
    return bool(re.search(r"[A-Z0-9+]", tok))


def _quote(part: str, m: re.Match) -> str:
    """A long highlight is quoted by its named feature around the match ("Durable performance with Digital Inverter
    Technology" → "Digital Inverter Technology"); a short one whole."""
    if len(part) <= PROOF_MAX:
        return proof_of(part)
    toks, pos, spans = part.split(" "), 0, []
    for t in toks:
        spans.append((pos, pos + len(t)))
        pos += len(t) + 1
    hit = [i for i, (a, b) in enumerate(spans) if a <= m.start() < b]  # the word where the match starts
    if hit:
        lo, hi = hit[0], hit[-1]
        while lo > 0 and _strong(toks[lo - 1]):
            lo -= 1
        while hi < len(toks) - 1 and _strong(toks[hi + 1]):
            hi += 1
        q = " ".join(toks[lo:hi + 1]).strip(" ,;.()")
        if len(q) >= 4 and (hi > lo or _strong(q)):
            return proof_of(q)
        lead = []
        for t in toks:  # "X-Reality PRO upscales every pixel …" → "X-Reality PRO"
            if not _strong(t):
                break
            lead.append(t)
        if len(lead) >= 2:
            return proof_of(" ".join(lead))
    return proof_of(part)


def found(p, category: str | None = None) -> dict[str, Found]:
    """{theme id: the highlight that shows it} for this product (cached per product and highlights)."""
    lines = texts(p)
    category = category or getattr(p, "category", None)
    key = (getattr(p, "sku", None), category, tuple(lines), id(getattr(p, "specs", None)))
    hit = _FOUND.get(key)
    if hit is not None:
        return hit
    parts = [x.strip() for t in lines for x in PART.split(t) if x.strip()]
    out: dict[str, Found] = {}
    for th in themes(category):
        for line in th.lines:  # the most specific line first
            m = next(((x, mm) for x in parts if (mm := line.rx.search(x))), None)
            if m:
                out[th.id] = Found(th.id, line, m[0], _quote(m[0], m[1]))
                break
        if th.id not in out:  # no highlight says it: a backend spec field may (Dialogue enhance, OS, HDR format …)
            for sp in th.specs:
                v = p.spec(sp["key"]) if hasattr(p, "spec") else None
                if v in (None, False, "", []) or not th.lines:
                    continue
                proof = sp.get("proof") if v is True else (", ".join(map(str, v[:2])) if isinstance(v, list) else str(v))
                if proof:
                    line = th.lines[min(int(sp.get("line", 0)), len(th.lines) - 1)]
                    out[th.id] = Found(th.id, line, proof, proof_of(proof))
                    break
    if len(_FOUND) > 5000:
        _FOUND.clear()
    _FOUND[key] = out
    return out


def need_holds(n: Need, home, wants: dict | None, pack) -> bool:
    if n.want:
        return n.want in (wants or {})
    if not n.slot:
        return False
    if n.gte is not None:
        v = P.number(home, n.slot, pack)
        return v is not None and v >= n.gte
    return any(v in n.values for v in P.values(home, n.slot))


ASKED_W = 2.0  # a need the associate told by meaning ("papa ko dialogue sunai nahi deta" → sound)


def triggered(th: Theme, home, wants: dict | None, pack) -> tuple[float, str | None]:
    """(weight, need id) of the strongest need of this theme the customer's answers switch on; (0, None) if none."""
    best: tuple[float, str | None] = (0.0, None)
    if th.id in (getattr(home, "asks", None) or []):
        best = (ASKED_W, "as_asked")
    for n in th.needs:
        if n.w > best[0] and need_holds(n, home, wants, pack):
            best = (n.w, n.id)
    return best


def _rule_keys(rule) -> set[str]:
    keys = {k for c in rule.spec_any or [] for k in c if not k.startswith("_")}
    keys |= {k for t in rule.tiers or [] for c in t.get("spec_all") or [] for k in c if not k.startswith("_")}
    return keys


def covered(th: Theme, p, pack, home=None) -> bool:
    """A spec rule already judges this for this home and product (the rule applies to their answers and the product
    carries its spec): the rule decides, not the highlight (no double counting on data that has both)."""
    from chanakya.brain.fit import _rule_applies

    for r in pack.fit_rules:
        if r.id in th.covers and (home is None or _rule_applies(r, home, pack)) \
                and any(p.spec(k) is not None for k in _rule_keys(r)):
            return True
    return False


def points(p, home, wants: dict | None, pack, n: int = 3, general: bool = True) -> list[Point]:
    """What this product's own highlights mean for this customer: the needs they answered first (strongest first),
    then, if `general`, the product's other highlights as "also worth knowing"."""
    if pack is None or getattr(p, "category", pack.category) != pack.category:
        return []
    have = found(p, pack.category)
    out: list[Point] = []
    for th in themes(pack.category):
        f = have.get(th.id)
        if f is None:
            continue
        w, need = triggered(th, home, wants, pack)
        if need is None:
            if not general or th.general <= 0:
                continue
            w = th.general
        out.append(Point(th.id, need, w, f.line.say, f.proof, f.text, th.demo))
    out.sort(key=lambda x: (x.need is None, -x.weight))
    return out[:n]


def contributions(p, home, pack) -> list[tuple[str, float, float]]:
    """Fit contributions (rule id, weight, match) for the strong needs the answers switched on: a product whose own
    highlights (or spec fields) do not show the theme gets 0.4 on it; one that shows it is left as it was, so a
    catalogue where every model has it scores exactly as before. Skipped where a spec rule already judges it."""
    out = []
    for th in themes(pack.category):
        w, need = triggered(th, home, None, pack)
        if need is None or w < STRONG or covered(th, p, pack, home):  # only a strong need moves the pick
            continue
        if th.id not in found(p, pack.category):  # neutral when it has it; counts against it when it does not
            out.append((f"hl_{th.id}", round(w * FIT_SCALE, 2), 0.4))
    return out


def informative(th: Theme, avail: list) -> bool:
    """The models on sale really differ on it: at least two show it and at least two do not."""
    have = sum(1 for p in avail if th.id in found(p))
    return have >= 2 and len(avail) - have >= 2


def slot_informative(pack, slot: str, avail: list) -> bool:
    """An answer to this question can change the pick through the products' own highlights."""
    return any(any(n.slot == slot and n.w >= STRONG for n in th.needs) and informative(th, avail)
               for th in themes(pack.category))


def slot_stats(pack, slot: str, avail: list) -> tuple[str, int] | None:
    """(theme id, models showing it) for the strongest informative theme this question feeds (for the Kyun line)."""
    best = None
    for th in themes(pack.category):
        w = max((n.w for n in th.needs if n.slot == slot), default=0)
        if w >= STRONG and informative(th, avail) and (best is None or w > best[0]):
            best = (w, th.id, sum(1 for p in avail if th.id in found(p)))
    return (best[1], best[2]) if best else None


def _rank(category: str, f: Found) -> int:
    th = theme(category, f.theme)
    return th.lines.index(f.line) if th and f.line in th.lines else 99


def differences(a, b, home, wants: dict | None, pack) -> list[Point]:
    """What a's own highlights show that b's do not (needs first): the real difference to show or say. The same
    theme counts when a's highlight is the more specific one ("ClearAudio+: dialogue clarity" vs plain "20W")."""
    mine, other = found(a, pack.category), found(b, pack.category)
    out = []
    for pt in points(a, home, wants, pack, n=10):
        o = other.get(pt.theme)
        if o is None or _rank(pack.category, mine[pt.theme]) < _rank(pack.category, o):
            out.append(pt)
    return out


def say(pt: Point, lang: str, prefix: str | None = None) -> str:
    """The customer line: "{need}: {daily life}. It has '{proof}'." (the caller registers pt.proof)."""
    d = _data()
    pre = d["prefix"].get(prefix or pt.need or "general") or d["prefix"]["general"]
    tpl = d["say"].get(lang) or d["say"]["en"]
    line = pt.say.get(lang) or pt.say.get("en") or ""
    return tpl.format(prefix=pre.get(lang) or pre.get("en"), line=line, proof=pt.proof)


def card_text(pt: Point, lang: str = "en") -> str:
    """The daily-life words in this language (cards: English; the associate's text: their own), sentence case."""
    t = pt.say.get(lang) or pt.say.get("en") or ""
    return t[:1].upper() + t[1:]


def need_label(need: str | None, lang: str) -> str | None:
    if not need:
        return None
    pre = _data()["prefix"].get(need)
    return (pre.get(lang) or pre.get("en")) if pre else None


def catalogue(category: str | None) -> list[dict]:
    """The need themes of a category, for the AI to map a note's meaning to (ids only come back)."""
    return [{"id": th.id, "means": f"{th.label}: {(th.lines[0].say.get('en') if th.lines else '')}"}
            for th in themes(category)]


def asks_in(text: str, category: str | None) -> list[str]:
    """Offline: the need themes a note names, from each theme's keywords (the AI does this by meaning)."""
    t = " " + " ".join((text or "").lower().split()) + " "
    out = []
    for th in themes(category):
        if any((f" {k} " in t) if len(k) <= 4 else (k in t) for k in th.keywords):
            out.append(th.id)
    return out


def demo_text(th: Theme | None, lang: str, alone: bool = False) -> str:
    """How to show this theme, in the associate's language; with one product, without comparing."""
    if th is None:
        return ""
    d = (th.demo_one if alone and th.demo_one else th.demo) or {}
    return d.get(lang) or d.get("en") or ""
