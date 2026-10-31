"""JSON logging. Never logs tokens, raw audio or full phone numbers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from datetime import UTC, datetime

_TOKEN_RE = re.compile(r"(EAA[A-Za-z0-9]{10,}|Bearer\s+\S+)")
_PHONE_RE = re.compile(r"\b\d{10,15}\b")


def wa_hash(wa_id: str | None) -> str | None:
    if not wa_id:
        return None
    return hashlib.sha1(wa_id.encode()).hexdigest()[:8]


def _redact(s: str) -> str:
    s = _TOKEN_RE.sub("[redacted]", s)
    return _PHONE_RE.sub(lambda m: "#" + wa_hash(m.group(0)), s)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": _redact(record.getMessage()),
        }
        extra = getattr(record, "fields", None)
        if extra:
            for k, v in extra.items():
                if k in ("wa_id", "to", "from"):
                    out[k + "_hash"] = wa_hash(str(v))
                else:
                    out[k] = _redact(str(v)) if isinstance(v, str) else v
        if record.exc_info:
            out["err"] = _redact(self.formatException(record.exc_info))[-2000:]
        return json.dumps(out, ensure_ascii=False, default=str)


class Log:
    """Tiny structured logger: log.info("event", key=value)."""

    def __init__(self, name: str):
        self._l = logging.getLogger(name)

    def _emit(self, level: int, event: str, exc: bool = False, **fields):
        self._l.log(level, event, extra={"fields": fields}, exc_info=exc)

    def debug(self, event: str, **f):
        self._emit(logging.DEBUG, event, **f)

    def info(self, event: str, **f):
        self._emit(logging.INFO, event, **f)

    def warning(self, event: str, **f):
        self._emit(logging.WARNING, event, **f)

    def error(self, event: str, **f):
        self._emit(logging.ERROR, event, **f)

    def exception(self, event: str, **f):
        self._emit(logging.ERROR, event, exc=True, **f)


def setup_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "asyncio", "aiosqlite", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_log(name: str) -> Log:
    return Log(name)
