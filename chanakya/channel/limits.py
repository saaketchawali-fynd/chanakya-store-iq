"""WhatsApp platform limits + safe truncation helpers (spec 6.2)."""

from __future__ import annotations

BODY_MAX = 1024
CAPTION_MAX = 1024
BUTTON_TITLE_MAX = 20
BUTTON_ID_MAX = 256
MAX_BUTTONS = 3
LIST_BUTTON_MAX = 20
LIST_ROW_TITLE_MAX = 24
LIST_ROW_DESC_MAX = 72
LIST_ROWS_MAX = 10
FOOTER_MAX = 60
IMAGE_MAX_BYTES = 5 * 1024 * 1024
AUDIO_MAX_BYTES = 16 * 1024 * 1024
FLOOR_MAX_LINES = 3


def truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def truncate_title(s: str, n: int = BUTTON_TITLE_MAX) -> str:
    return truncate(s, n)
