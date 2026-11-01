"""Make the 'Aaj ka clip' videos: one ~20-second vertical clip per step and language (data/clips/<step>_<lang>.mp4).

Four clean slides per clip (the step and its Niti line, why it matters, the line to say, today's try), rendered
with Playwright and joined with crossfades by ffmpeg (H.264, silent AAC track, faststart) so WhatsApp plays them.
Run: python scripts/make_clips.py  (needs Chromium from Playwright and ffmpeg)."""

from __future__ import annotations

import asyncio
import html
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "clips"

from chanakya.i18n.lang import t  # noqa: E402

STEPS = ["validate", "assess", "translate", "demonstrate", "complete", "close", "follow_through"]
NITI = {"validate": "niti_validate", "assess": "niti_assess", "translate": "niti_translate",
        "demonstrate": "niti_recommend", "complete": "niti_complete", "close": "niti_close",
        "follow_through": "niti_follow"}
WHY = {
    "validate": {"hi": "Customer ki baat maanne se bharosa banta hai — phir woh aapki sunte hain.",
                 "mr": "ग्राहकाचं म्हणणं मान्य केल्याने विश्वास बसतो — मग ते तुमचं ऐकतात.",
                 "en": "Agreeing first builds trust; then the customer listens to you."},
    "assess": {"hi": "Ghar samajhne se sahi product milta hai — aur daam ki baat baad mein aati hai.",
               "mr": "घर समजल्याने योग्य उत्पादन मिळतं — आणि किंमत नंतर येते.",
               "en": "Understanding the home finds the right product, and pushes price to the end."},
    "translate": {"hi": "Spec se customer ko kuch samajh nahi aata — unki zindagi ki baat samajh aati hai.",
                  "mr": "स्पेक्स ग्राहकाला कळत नाहीत — त्यांच्या रोजच्या जगण्याची भाषा कळते.",
                  "en": "Specs mean little to customers; their daily life means everything."},
    "demonstrate": {"hi": "Ek farak aankhon se dikhe to 10 baatein bolne ki zaroorat nahi.",
                    "mr": "एक फरक डोळ्यांनी दिसला की दहा गोष्टी सांगायची गरज नाही.",
                    "en": "One difference they can see beats ten things you say."},
    "complete": {"hi": "Poora kharcha pehle batane se billing par koi surprise nahi.",
                 "mr": "पूर्ण खर्च आधी सांगितल्याने बिलिंगला सरप्राइज नाही.",
                 "en": "The full cost up front means no surprise at billing."},
    "close": {"hi": "Demo ke baad daam sach ke saath — dabaav nahi, bharosa.",
              "mr": "डेमोनंतर किंमत खरेपणाने — दबाव नाही, विश्वास.",
              "en": "Price after the demo, with the truth: trust, not pressure."},
    "follow_through": {"hi": "Bikri ke baad ka rishta agli bikri laata hai.",
                       "mr": "विक्रीनंतरचं नातं पुढची विक्री आणतं.",
                       "en": "The relationship after the sale brings the next sale."},
}
SAY = {
    "validate": {"hi": "बहुत सही सोचा है। एक बार कमरे से मिला लेते हैं।", "mr": "छान विचार केलाय. एकदा खोलीशी जुळवून बघू.",
                 "en": "Good thinking. Let's match it to your room."},
    "assess": {"hi": "सोफ़े से टीवी कितने कदम दूर होगा?", "mr": "सोफ्यापासून टीव्ही किती पावलं लांब असेल?",
               "en": "How many steps from the sofa to the TV?"},
    "translate": {"hi": "120 Hz मतलब क्रिकेट में बॉल धुंधली नहीं होगी।", "mr": "120 Hz म्हणजे क्रिकेटमध्ये बॉल धूसर दिसणार नाही.",
                  "en": "120 Hz means the ball stays sharp in cricket."},
    "demonstrate": {"hi": "अपने सोफ़े जितनी दूरी से दोनों देखिए।", "mr": "तुमच्या सोफ्याइतक्या अंतरावरून दोन्ही पाहा.",
                    "en": "Look at both from your sofa distance."},
    "complete": {"hi": "ये पूरा खर्च है — बिल में यही आएगा।", "mr": "हा पूर्ण खर्च आहे — बिलात हेच येईल.",
                 "en": "This is the full cost; the bill will match it."},
    "close": {"hi": "डिलीवरी शनिवार को चाहिए या रविवार को?", "mr": "डिलिव्हरी शनिवारी हवी की रविवारी?",
              "en": "Delivery on Saturday or Sunday?"},
    "follow_through": {"hi": "क्या मैं ये सब आपके WhatsApp पर भेज दूँ?", "mr": "हे सगळं तुमच्या WhatsApp वर पाठवू का?",
                       "en": "May I send all this to your WhatsApp?"},
}
HEAD = {"hi": ("Aaj ka step", "Kyun zaroori", "Aise boliye", "Aaj try kariye"),
        "mr": ("आजचा टप्पा", "का महत्त्वाचं", "असं बोला", "आज करून बघा"),
        "en": ("Today's step", "Why it matters", "Say it like this", "Try it today")}

CSS = """*{margin:0;box-sizing:border-box}body{width:720px;height:1280px;font-family:"Noto Sans","Noto Sans Devanagari",
"Kohinoor Devanagari","Devanagari Sangam MN",sans-serif;background:linear-gradient(160deg,#1B2A4A 0%,#24365e 60%,#2e4372 100%);
color:#fff;display:flex;flex-direction:column;padding:96px 64px}.brand{font-size:26px;opacity:.7;font-weight:600}
.k{margin-top:150px;color:#E8833A;font-size:30px;font-weight:700}.h{margin-top:22px;font-size:62px;font-weight:800;line-height:1.18}
.p{margin-top:36px;font-size:40px;line-height:1.45;opacity:.95}.q{margin-top:40px;background:#fff;color:#1B2A4A;border-radius:28px;
padding:40px;font-size:42px;line-height:1.4;font-weight:600}.bar{margin-top:auto;height:8px;border-radius:4px;background:#E8833A;width:var(--w)}
.n{margin-top:18px;font-size:24px;opacity:.6}"""


def slide(lang: str, idx: int, kicker: str, title: str | None, text: str | None, quote: str | None) -> str:
    e = html.escape
    parts = [f'<div class="brand">Chanakya · Store IQ</div><div class="k">{e(kicker)}</div>']
    if title:
        parts.append(f'<div class="h">{e(title)}</div>')
    if text:
        parts.append(f'<div class="p">{e(text)}</div>')
    if quote:
        parts.append(f'<div class="q">“{e(quote)}”</div>')
    parts.append(f'<div class="bar" style="--w:{(idx + 1) * 25}%"></div><div class="n">{idx + 1} / 4</div>')
    return f'<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{"".join(parts)}</body></html>'


async def render_slides(page, step: str, lang: str, d: Path) -> list[Path]:
    h = HEAD[lang]
    step_name = t(f"step_{step}", lang)
    slides = [
        slide(lang, 0, h[0], step_name, t(NITI[step], lang), None),
        slide(lang, 1, h[1], None, WHY[step][lang], None),
        slide(lang, 2, h[2], None, None, SAY[step][lang]),
        slide(lang, 3, h[3], None, t(f"tip_{step}", lang), None),
    ]
    out = []
    for i, html_doc in enumerate(slides):
        await page.set_content(html_doc, wait_until="load")
        await page.evaluate("document.fonts.ready")
        f = d / f"s{i}.png"
        await page.screenshot(path=str(f))
        out.append(f)
    return out


def join(slides: list[Path], out: Path, per: float = 5.0, fade: float = 0.6) -> None:
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    for s in slides:
        args += ["-loop", "1", "-t", str(per), "-i", str(s)]
    args += ["-f", "lavfi", "-t", str(per * len(slides) - fade * (len(slides) - 1)), "-i", "anullsrc=r=44100:cl=mono"]
    chain, last = [], "[0:v]"
    for i in range(1, len(slides)):
        offset = per * i - fade * i
        tag = f"[v{i}]"
        chain.append(f"{last}[{i}:v]xfade=transition=fade:duration={fade}:offset={offset}{tag}")
        last = tag
    chain.append(f"{last}format=yuv420p[v]")
    args += ["-filter_complex", ";".join(chain), "-map", "[v]", "-map", f"{len(slides)}:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "26", "-r", "24", "-c:a", "aac", "-b:a", "32k",
             "-shortest", "-movflags", "+faststart", str(out)]
    subprocess.run(args, check=True)


async def main() -> None:
    from playwright.async_api import async_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 720, "height": 1280})
        for step in STEPS:
            for lang in ("hi", "mr", "en"):
                with tempfile.TemporaryDirectory() as d:
                    slides = await render_slides(page, step, lang, Path(d))
                    out = OUT / f"{step}_{lang}.mp4"
                    join(slides, out)
                    print(out.name, out.stat().st_size // 1024, "KB")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
