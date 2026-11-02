"""i18n: t(key, lang, **vars) over strings.yaml (+ strings_extra.yaml); script detection."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from chanakya.truth.firewall import NumberRegistry

HERE = Path(__file__).resolve().parent
LANGS = ("hi", "mr", "en")


class _SafeDict(dict):
    def __missing__(self, key):  # leave unknown placeholders visible in tests rather than crashing
        return "{" + key + "}"


_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u23F0-\u23FF\uFE0F]")


_CHECK = "[\u2713\u2714\u2705]"


def clean(text: str) -> str:
    """No emojis in anything Chanakya writes: strip pictographs and tidy the spaces they leave. A tick that
    separated two phrases ("Sahi ✓ Dhoop mein…") becomes a dash, so the sentences don't run together."""
    t = re.sub(rf"(?m)^[ \t]*{_CHECK}[ \t]*", "", text or "")
    t = re.sub(rf"[ \t]*{_CHECK}[ \t]*$", "", t, flags=re.M)
    t = re.sub(rf"[ \t]*{_CHECK}[ \t]+", " — ", t)
    out = _EMOJI.sub("", t)
    return "\n".join(" ".join(line.split()) for line in out.split("\n"))


@lru_cache
def strings() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for name in ("strings.yaml", "strings_extra.yaml", "strings_flow.yaml"):
        p = HERE / name
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            out.update(data)
    return {k: {lg: clean(v) if isinstance(v, str) else v for lg, v in d.items()} for k, d in out.items()}


def reload_strings() -> None:
    strings.cache_clear()


def has(key: str) -> bool:
    return key in strings()


def raw(key: str, lang: str) -> str:
    entry = strings().get(key)
    if entry is None:
        raise KeyError(f"missing i18n key: {key}")
    return entry.get(lang) or entry.get("en") or entry.get("hi") or ""  # never Hindi by default


def fill(template: str, **vars) -> str:
    return template.format_map(_SafeDict(**{k: ("" if v is None else v) for k, v in vars.items()}))


def t(key: str, lang: str, reg: NumberRegistry | None = None, **vars) -> str:
    """Localised string. If reg is given, the template's literal numerals are registered as static text
    (variables must already be registry-formatted)."""
    tpl = raw(key, lang)
    if reg is not None:
        reg.static(tpl)
    return fill(tpl, **vars)


def pick(d: dict | None, lang: str, reg: NumberRegistry | None = None) -> str:
    """Pick a language from an inline {hi, mr, en} dict (packs, seed titles)."""
    if not d:
        return ""
    if isinstance(d, str):
        s = d
    else:
        s = d.get(lang) or d.get("en") or d.get("hi") or next(iter(d.values()), "")
    if reg is not None:
        reg.static(s)
    return s


_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_MR_MARKERS = re.compile(r"(आहे|नाही|आहेत|करा|पाहिजे|हवा|हवी|किती|खोली|घरात|ळ)")


def detect_script_lang(text: str) -> str | None:
    """Rough language guess from script and markers (used only as a hint)."""
    if not text:
        return None
    if _DEVANAGARI.search(text):
        return "mr" if _MR_MARKERS.search(text) else "hi"
    return None


def t_for(lang: str):
    def _t(key: str, **vars) -> str:
        return t(key, lang, **vars) if has(key) else key

    return _t
