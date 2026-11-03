"""Speech providers for Live: audio frames in, final transcript lines (and Gemini `heard` reports) out.

- gemini: Gemini Live (google-genai `aio.live`), TEXT output, input transcription, one tool `heard`.
- openai: OpenAI Realtime in transcription mode (gpt-4o-transcribe, server VAD).
- browser: no server provider; the page sends text from on-device speech recognition.

Keys stay on the server. A provider that fails raises; the socket handler then moves the page to `browser`."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable

from chanakya.log import get_log

log = get_log("live")

OnText = Callable[[str], Awaitable[None]]
OnHeard = Callable[[dict], Awaitable[dict]]

LANG_CODE = {"hi": "hi", "en": "en", "mr": "mr"}


def pick_provider(s, want: str | None = None) -> str:
    want = (want or s.live_provider or "auto").lower()
    if want == "gemini" and s.gemini_api_key:
        return "gemini"
    if want == "openai" and s.openai_api_key:
        return "openai"
    if want == "auto":
        if s.gemini_api_key:
            return "gemini"
        if s.openai_api_key:
            return "openai"
    return "browser"


def rate_for(name: str) -> int:
    return 24000 if name == "openai" else 16000


def instruction(packs, category: str | None) -> str:
    """The listener's brief: only report, never advise; the slot ids and values it may use come from the packs."""
    lines = []
    for cat, pack in packs.packs.items():
        if category and cat != category:
            continue
        for sd in pack.slots:
            if sd.values:
                lines.append(f"{cat}.{sd.id}: {', '.join(sd.values)}")
    return (
        "You are a silent listener in an Indian electronics store (Reliance Digital). A store associate is talking "
        "with a customer in Hindi, English, Marathi or a mix. Never speak, never give advice, never state prices or "
        "specs. Whenever the customer says something about the product they want, their home, family, usage or "
        "budget, call the tool `heard` with only what was actually said, using these slot ids and values "
        "(category.slot: values):\n" + "\n".join(lines)
        + "\nNumbers: seat_distance_ft, room_sqft, family_size, budget_inr (only when a budget is said). "
        "If nothing fits, do not call the tool. Reply with an empty text."
    )


HEARD_SCHEMA = {
    "name": "heard",
    "description": "Report facts the customer stated (validated by the store system).",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["tv", "ac", "fridge", "wm", "laptop"]},
            "slots": {"type": "array", "items": {"type": "object", "properties": {
                "slot": {"type": "string"}, "value": {"type": "string"}}, "required": ["slot", "value"]}},
            "numbers": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "value": {"type": "number"}}, "required": ["name", "value"]}},
        },
    },
}


class Provider:
    name = "browser"

    async def start(self) -> None: ...

    async def audio(self, pcm: bytes) -> None: ...

    async def close(self) -> None: ...


class GeminiLive(Provider):
    name = "gemini"

    def __init__(self, s, packs, category, lang: str, on_text: OnText, on_heard: OnHeard):
        self.s, self.packs, self.category, self.lang = s, packs, category, lang
        self.on_text, self.on_heard = on_text, on_heard
        self._cm = self._session = self._task = None
        self._buf = ""

    async def start(self) -> None:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.s.gemini_api_key)
        cfg = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            system_instruction=instruction(self.packs, self.category),
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**HEARD_SCHEMA)])],
        )
        self._cm = client.aio.live.connect(model=self.s.gemini_live_model, config=cfg)
        self._session = await asyncio.wait_for(self._cm.__aenter__(), timeout=10)
        self._task = asyncio.create_task(self._recv())

    async def _recv(self) -> None:
        from google.genai import types

        try:
            while True:
                async for msg in self._session.receive():
                    sc = msg.server_content
                    if sc and sc.input_transcription and sc.input_transcription.text:
                        self._buf += sc.input_transcription.text
                    if sc and (sc.turn_complete or sc.generation_complete) and self._buf.strip():
                        text, self._buf = self._buf.strip(), ""
                        await self.on_text(text)
                    if msg.tool_call:
                        out = []
                        for fc in msg.tool_call.function_calls or []:
                            res = await self.on_heard(dict(fc.args or {})) if fc.name == "heard" else {}
                            out.append(types.FunctionResponse(id=fc.id, name=fc.name, response=res))
                        if out:
                            await self._session.send_tool_response(function_responses=out)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # surfaced through the socket handler
            log.warning("gemini_live_recv", error=str(e)[:200])
            raise

    async def audio(self, pcm: bytes) -> None:
        from google.genai import types

        await self._session.send_realtime_input(audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000"))

    @property
    def failed(self) -> BaseException | None:
        t = self._task
        return t.exception() if t and t.done() and not t.cancelled() else None

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self._cm:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass


class OpenAIRealtime(Provider):
    name = "openai"
    URL = "wss://api.openai.com/v1/realtime?intent=transcription"

    def __init__(self, s, lang: str, on_text: OnText):
        self.s, self.lang, self.on_text = s, lang, on_text
        self._ws = self._task = None

    async def start(self) -> None:
        from websockets.asyncio.client import connect

        self._ws = await asyncio.wait_for(connect(self.URL, additional_headers={
            "Authorization": f"Bearer {self.s.openai_api_key}", "OpenAI-Beta": "realtime=v1"}), timeout=10)
        await self._ws.send(json.dumps({"type": "transcription_session.update", "session": {
            "input_audio_format": "pcm16",
            "input_audio_transcription": {"model": self.s.openai_transcribe_model, "language": LANG_CODE.get(self.lang, "hi"),
                                          "prompt": "Hindi, English and Marathi mixed; TV, AC, fridge, washing machine."},
            "turn_detection": {"type": "server_vad", "silence_duration_ms": 600},
            "input_audio_noise_reduction": {"type": "near_field"},
        }}))
        self._task = asyncio.create_task(self._recv())

    async def _recv(self) -> None:
        try:
            async for raw in self._ws:
                ev = json.loads(raw)
                if ev.get("type") == "conversation.item.input_audio_transcription.completed" and ev.get("transcript"):
                    await self.on_text(ev["transcript"].strip())
                elif ev.get("type") == "error":
                    raise RuntimeError(str(ev.get("error"))[:200])
        except asyncio.CancelledError:
            pass

    @property
    def failed(self) -> BaseException | None:
        t = self._task
        return t.exception() if t and t.done() and not t.cancelled() else None

    async def audio(self, pcm: bytes) -> None:
        await self._ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}))

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
