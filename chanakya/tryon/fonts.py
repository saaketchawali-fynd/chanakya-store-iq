"""Fonts for text drawn on photos (try-on labels), in every language.

Linux/Noto ships Devanagari and Latin as separate files: Noto Sans Devanagari has digits but no Latin letters, and
Noto Sans has no Devanagari. So text is split into runs by script and each run is drawn with a font that has it
(macOS Kohinoor has both). Use `measure` and `draw` instead of ImageDraw.text for any label text."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FontT = ImageFont.FreeTypeFont | ImageFont.ImageFont

# (regular path, face index, bold path, bold face index); the first that exists wins
_DEVA = [
    ("/System/Library/Fonts/Kohinoor.ttc", 0, "/System/Library/Fonts/Kohinoor.ttc", 2),
    ("NotoSansDevanagari-Regular", 0, "NotoSansDevanagari-Bold", 0),
    ("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc", 0, None, 1),
    ("Lohit-Devanagari", 0, None, 0),
]
_LATIN = [
    ("/System/Library/Fonts/Kohinoor.ttc", 0, "/System/Library/Fonts/Kohinoor.ttc", 2),
    ("NotoSans-Regular", 0, "NotoSans-Bold", 0),
    ("DejaVuSans", 0, "DejaVuSans-Bold", 0),
    ("/System/Library/Fonts/Helvetica.ttc", 0, "/System/Library/Fonts/Helvetica.ttc", 1),
]
_FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts")


@lru_cache
def _find(name: str | None) -> str | None:
    """An absolute path as is; a bare file stem is looked up under the system font folders (any sub-folder)."""
    if not name:
        return None
    if name.startswith("/"):
        return name if Path(name).exists() else None
    for d in _FONT_DIRS:
        base = Path(d)
        if base.exists():
            for ext in (".ttf", ".otf", ".ttc"):
                hit = next(iter(sorted(base.rglob(name + ext))), None)
                if hit:
                    return str(hit)
    return None


def _load(cands, size: int, bold: bool) -> FontT | None:
    for reg, idx, bold_name, bold_idx in cands:
        path = _find(reg)
        if not path:
            continue
        if bold:
            bp = _find(bold_name) or path
            try:
                return ImageFont.truetype(bp, size, index=bold_idx if bold_name else idx)
            except Exception:  # noqa: BLE001
                pass
        try:
            return ImageFont.truetype(path, size, index=idx)
        except Exception:  # noqa: BLE001
            continue
    return None


def _default(size: int) -> FontT:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


@lru_cache
def latin(size: int, bold: bool = False) -> FontT:
    return _load(_LATIN, size, bold) or _load(_DEVA, size, bold) or _default(size)


@lru_cache
def deva(size: int, bold: bool = False) -> FontT:
    return _load(_DEVA, size, bold) or latin(size, bold)


def font(size: int, bold: bool = False) -> FontT:
    """The Latin font (kept for callers that draw digits/Latin only)."""
    return latin(size, bold)


def is_deva(ch: str) -> bool:
    o = ord(ch)
    return 0x0900 <= o <= 0x097F or 0xA8E0 <= o <= 0xA8FF or 0x1CD0 <= o <= 0x1CFF or o in (0x200C, 0x200D)


def runs(text: str) -> list[tuple[str, bool]]:
    """Split into (chunk, is_devanagari). Spaces stay with the run they follow (a Hindi phrase is shaped in one
    piece); every other non-Devanagari character (digits, Latin, · × ₹ ") goes to the Latin font, which has it."""
    out: list[list] = []
    for ch in text:
        if ch.isspace() and out:
            out[-1][0] += ch
            continue
        d = is_deva(ch)
        if out and out[-1][1] == d:
            out[-1][0] += ch
        else:
            out.append([ch, d])
    return [(s, d) for s, d in out]


def describe() -> dict:
    """Which fonts the photo labels use and whether complex shaping (raqm) is on: shown on /health."""
    from PIL import features

    def path(f) -> str | None:
        return getattr(f, "path", None)

    return {"latin": path(latin(20)), "deva": path(deva(20)), "raqm": bool(features.check("raqm"))}


def _fonts(size: int, bold: bool) -> tuple[FontT, FontT]:
    return latin(size, bold), deva(size, bold)


def metrics(size: int, bold: bool = False) -> tuple[int, int]:
    """(ascent, descent) covering both scripts, so mixed lines share one baseline."""
    asc = desc = 0
    for f in _fonts(size, bold):
        a, d = f.getmetrics() if hasattr(f, "getmetrics") else (size, 0)
        asc, desc = max(asc, a), max(desc, d)
    return asc, desc


def measure(text: str, size: int, bold: bool = False) -> tuple[int, int, int]:
    """(left bearing of the first run, total advance width, line height)."""
    lat, dev = _fonts(size, bold)
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    w, left = 0.0, 0
    for i, (chunk, d) in enumerate(runs(text)):
        f = dev if d else lat
        if i == 0:
            left = probe.textbbox((0, 0), chunk, font=f)[0]
        w += probe.textlength(chunk, font=f)
    asc, desc = metrics(size, bold)
    return left, round(w), asc + desc


def draw(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, size: int, bold: bool = False, fill=None) -> None:
    """Draw `text` with its top at xy[1] (line box of `metrics`), each script in its own font, on one baseline."""
    lat, dev = _fonts(size, bold)
    asc, _ = metrics(size, bold)
    x, y = xy
    for chunk, is_d in runs(text):
        f = dev if is_d else lat
        fa = f.getmetrics()[0] if hasattr(f, "getmetrics") else asc
        d.text((x, y + asc - fa), chunk, font=f, fill=fill)
        x += d.textlength(chunk, font=f)
