"""In-memory delivery diagnostics shown on /health: counts, timestamps and Graph error codes only
(no phone numbers, message text or tokens)."""

from __future__ import annotations

from datetime import UTC, datetime

_d: dict = {
    "verify_ok": 0,
    "verify_failed": 0,
    "webhook_posts": 0,
    "bad_signature": 0,
    "messages_in": 0,
    "statuses_in": 0,
    "sent_ok": 0,
    "send_errors": 0,
    "last_webhook_at": None,
    "last_message_at": None,
    "last_send_ok_at": None,
    "last_send_error": None,
    "last_status_error": None,
}


def bump(key: str, n: int = 1) -> None:
    _d[key] = (_d.get(key) or 0) + n


def mark(key: str, value=None) -> None:
    _d[key] = value if value is not None else datetime.now(UTC).isoformat(timespec="seconds")


def snapshot() -> dict:
    return dict(_d)
