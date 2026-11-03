"""3 Nov 2026 RCA: Google's Gemini service was overloaded (503 'high demand' and hanging calls) on every model,
for three voice notes in a row. Voice now: the transcript first (across models, with retries), the meaning from
the words (Gemini, else the offline rules), and a note that cannot be heard is kept and answered later."""

from __future__ import annotations

from types import SimpleNamespace

from chanakya.ai.gemini import AIUnavailable
from chanakya.core import router
from chanakya.sim.harness import PHONES


class DownText:
    """The text model is down too: the meaning must come from the offline rules."""

    async def json(self, *a, **k):
        raise AIUnavailable("503 high demand")

    async def hedged(self, *a, **k):
        raise AIUnavailable("503 high demand")


def live(h, monkeypatch, fail_times: int):
    calls = {"n": 0}

    async def hear(audio, mime=None, timeout=24.0):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise AIUnavailable("503 UNAVAILABLE high demand")
        return "Shah ji ka living room hai, sofa se das feet, dhoop aati hai"

    monkeypatch.setattr(h.rt.ai, "gemini", DownText())
    monkeypatch.setattr(h.rt.ai, "hear", hear)
    monkeypatch.setattr(router, "VOICE_RETRY_WAITS", (0.01, 0.01, 0.01))
    return calls


async def start(h):
    await h.tap("g:new")
    await h.tap("s:C1:lang:en")
    await h.pick("s:C1:cat:tv")


async def test_voice_is_understood_even_when_the_text_model_is_down(h, monkeypatch):
    await start(h)
    live(h, monkeypatch, fail_times=0)
    out = await h.voice_bytes(b"OggS-1")
    assert "Google" not in out[0].body and "samajh nahi" not in out[0].body  # answered at once, no error
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.slots.get("room_light")  # 'dhoop aati hai' understood by the offline rules


async def test_a_note_heard_late_is_answered_not_lost(h, monkeypatch):
    await start(h)
    calls = live(h, monkeypatch, fail_times=2)  # Google busy for the first try and the first retry
    n0 = len(h.ch.sent)
    out = await h.voice_bytes(b"OggS-2")
    first = [m for m in out if m.to == PHONES["associate"]][0]
    assert "busy" in first.body.lower() and "saaf samajh" not in first.body  # honest, not 'unclear'
    await h.rt.orch.drain(timeout=5)
    later = [s.msg for s in h.ch.sent[n0:] if s.msg.kind != "reaction"]
    assert len(later) >= 2 and calls["n"] >= 3  # the note was answered once the words came through
    s = await h.rt.svc.sessions.by_code(PHONES["associate"], "C1")
    assert s.home.slots.get("room_light")


async def test_gives_up_plainly_after_the_retries(h, monkeypatch):
    await start(h)
    live(h, monkeypatch, fail_times=99)
    n0 = len(h.ch.sent)
    await h.voice_bytes(b"OggS-3")
    await h.rt.orch.drain(timeout=5)
    bodies = [s.msg.body or "" for s in h.ch.sent[n0:] if s.msg.kind != "reaction"]
    assert "type it" in bodies[-1].lower() or "text" in bodies[-1].lower()
    _ = SimpleNamespace
