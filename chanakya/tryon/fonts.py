"""Devanagari-capable font lookup (macOS, Linux/Noto, fallback default)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

# (path, face index, bold path or None, bold face index)
_CANDIDATES = [
    ("/System/Library/Fonts/Kohinoor.ttc", 0, "/System/Library/Fonts/Kohinoor.ttc", 2),
    ("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc", 0, None, 1),
    ("/System/Library/Fonts/Supplemental/DevanagariMT.ttc", 0, None, 1),
    (
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        0,
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        0,
    ),
    ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", 0, "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", 0),
    (
        "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
        0,
        "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Bold.ttf",
        0,
    ),
    (
        "/usr/share/fonts/noto/NotoSansDevanagari-Regular.ttf",
        0,
        "/usr/share/fonts/noto/NotoSansDevanagari-Bold.ttf",
        0,
    ),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
]


@lru_cache
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path, idx, bold_path, bold_idx in _CANDIDATES:
        if not Path(path).exists():
            continue
        if bold:
            bp = bold_path or path
            if Path(bp).exists():
                try:
                    return ImageFont.truetype(bp, size, index=bold_idx)
                except Exception:
                    pass
        try:
            return ImageFont.truetype(path, size, index=idx)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()
