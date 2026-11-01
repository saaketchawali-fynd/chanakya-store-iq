"""Voice-note check against the real Gemini model, run on your Mac (the key is typed at a hidden prompt).

    python scripts/voice_check.py               # synthesise sample notes with macOS voices, then test them
    python scripts/voice_check.py my_note.ogg   # test your own WhatsApp voice note (export it from WhatsApp)

For each note it prints what Chanakya understood (transcript, language, home facts, numbers, intents) and how long
the model took — the same call the WhatsApp server makes."""

from __future__ import annotations

import asyncio
import getpass
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLES = [  # (macOS voice, text) — store-floor style notes
    ("Lekha", "शाह जी का लिविंग रूम है, सोफ़े से दस फ़ीट दूर, दोपहर में धूप आती है, पापा क्रिकेट देखते हैं, "
              "बजट पचास हज़ार तक है"),
    ("Rishi", "Customer says the TV will go on the wall, the room is quite dark at night and they mostly watch "
              "movies on Netflix. Grandparents also watch, so dialogue must be clear."),
    ("Lekha", "जोशी जी का बेडरूम डेढ़ सौ स्क्वेयर फ़ीट है, टॉप फ़्लोर है, रोज़ दस घंटे एसी चलेगा, वोल्टेज ऊपर नीचे होता है"),
    ("Rishi", "Customer is asking, is four K useless because TV channels are only HD?"),
]


def synth(voice: str, text: str, out_dir: Path, i: int) -> Path:
    aiff = out_dir / f"note{i}.aiff"
    ogg = out_dir / f"note{i}.ogg"
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ac", "1", "-ar", "16000",
                    "-c:a", "libopus", "-b:a", "24k", str(ogg)], check=True)
    return ogg


async def main() -> None:
    os.environ.setdefault("GEMINI_API_KEY", getpass.getpass("GEMINI_API_KEY (hidden): ").strip())
    os.environ["AI_MODE"] = "live"
    from chanakya.ai.client import AIClient
    from chanakya.brain.packs import load_packs
    from chanakya.config import Settings

    packs = load_packs()
    ai = AIClient(Settings(_env_file=None), packs)
    files = [Path(a) for a in sys.argv[1:]]
    tmp = Path(tempfile.mkdtemp())
    if not files:
        files = [synth(v, t, tmp, i) for i, (v, t) in enumerate(SAMPLES)]
    for f in files:
        pack = packs.get("tv")
        ctx = {"category": "tv", "known_slots": {},
               "slots": [{"id": s.id, "hint": s.ask.get("en", ""), "number_name": s.number,
                          "values": [{"id": v, "means": s.value_title(v, "en")} for v in s.values]} for s in pack.slots],
               "myths": [{"id": m.id, "belief": m.belief} for m in packs.all_myths("tv")], "library": [],
               "options_shown": {}}
        t0 = time.perf_counter()
        try:
            u = await ai.understand(None, f.read_bytes(), ctx, mime="audio/ogg")
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"\n== {f.name}  ({ms} ms)")
            print("  transcript:", u.transcript)
            print("  language:", u.language, "| category:", u.category, "| customer:", u.customer_name)
            print("  slots:", [(s.slot, s.value) for s in u.slots])
            print("  numbers:", [(n.name, n.value) for n in u.numbers])
            print("  intents:", u.intents, "| claim:", u.claim_id, "| question:", u.question_for_expert)
        except Exception as e:  # noqa: BLE001
            print(f"\n== {f.name}  FAILED after {int((time.perf_counter() - t0) * 1000)} ms: {e}")


if __name__ == "__main__":
    asyncio.run(main())
