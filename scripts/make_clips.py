"""Make the before-the-shift training videos: one 40-45 second vertical lesson per sales step and language
(data/clips/<step>_<lang>.mp4), the way an expert floor associate would teach a new one (user, 4 Nov 2026).

Nine clean slides: the step and its goal; four expert moves, revealed one by one; a real exchange on the floor; the
mistake to avoid and what to do instead; which Chanakya button helps; one thing to try today. The lessons are in
scripts/clip_training.py. Rendered with Playwright, joined with crossfades by ffmpeg (H.264, silent AAC track,
faststart) so WhatsApp plays them inline.
Run: python scripts/make_clips.py [step …]  (needs Chromium from Playwright and ffmpeg)."""

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
from scripts.clip_training import HEAD, LESSONS  # noqa: E402

STEPS = ["validate", "assess", "translate", "demonstrate", "complete", "close", "follow_through"]
# seconds per slide: goal, 4 moves (one by one), floor, avoid/instead, Chanakya, try today  → ~45 s
TIMES = [5.5, 3.8, 3.8, 3.8, 6.0, 7.0, 6.0, 4.5, 5.0]

CSS = """*{margin:0;box-sizing:border-box}
body{width:720px;height:1280px;font-family:"Inter","Avenir Next","Helvetica Neue","Kohinoor Devanagari","Noto Sans",
"Noto Sans Devanagari",sans-serif;background:#F4F5F7;color:#0B1220;display:flex;flex-direction:column;-webkit-font-smoothing:antialiased}
.top{padding:56px 52px 0}.row{display:flex;justify-content:space-between;align-items:center}
.brand{display:flex;align-items:center;gap:10px;font-size:21px;font-weight:600;color:#6B7280}
.mk{width:26px;height:26px;border-radius:8px;background:#4338CA;display:inline-block}
.pill{font-size:18px;font-weight:600;padding:6px 14px;border-radius:999px;background:#EEF0FF;color:#4338CA}
.kick{margin-top:36px;font-size:21px;font-weight:700;letter-spacing:1.4px;text-transform:uppercase;color:#4338CA}
.kick.dv{letter-spacing:0;text-transform:none}
.title{margin-top:6px;font-size:60px;font-weight:700;line-height:1.08;letter-spacing:-1px}
.body{flex:1;padding:24px 52px 70px;display:flex;flex-direction:column;justify-content:center}
.k{font-size:21px;font-weight:700;color:#6B7280;letter-spacing:1.4px;text-transform:uppercase;margin-bottom:18px}
.k.dv{text-transform:none;letter-spacing:0}
.goal{background:#0B1020;color:#fff;border-radius:28px;padding:40px 38px;font-size:40px;line-height:1.36;font-weight:600}
.goal small{display:block;font-size:20px;font-weight:600;color:#A5B4FC;margin-bottom:12px;letter-spacing:1px}
.mv{display:flex;gap:18px;align-items:flex-start;margin-top:16px;background:#fff;border:1.5px solid #E8EAEE;border-radius:24px;
padding:22px 24px;font-size:31px;line-height:1.34;font-weight:600}
.mv .n{flex:none;width:46px;height:46px;border-radius:50%;background:#4338CA;color:#fff;font-size:23px;font-weight:700;
display:flex;align-items:center;justify-content:center;margin-top:1px}
.mv.dim{opacity:.18}.mv.new{border-color:#4338CA;box-shadow:0 8px 24px rgba(67,56,202,.12)}
.chat{display:flex;flex-direction:column;gap:20px}
.bub{max-width:88%;border-radius:26px;padding:26px 28px;font-size:33px;line-height:1.38;font-weight:600}
.bub small{display:block;font-size:19px;font-weight:700;letter-spacing:1px;margin-bottom:8px;opacity:.75}
.bub.c{align-self:flex-start;background:#fff;border:1.5px solid #E8EAEE;border-top-left-radius:8px}
.bub.y{align-self:flex-end;background:#4338CA;color:#fff;border-top-right-radius:8px}
.av{border-radius:26px;padding:30px 30px;font-size:34px;line-height:1.36;font-weight:600;margin-top:4px}
.av small{display:block;font-size:20px;font-weight:700;letter-spacing:1px;margin-bottom:10px}
.av.bad{background:#FEF2F2;color:#991B1B}.av.good{background:#ECFDF5;color:#065F46;margin-top:18px}
.help{background:#fff;border:1.5px solid #E8EAEE;border-left:10px solid #4338CA;border-radius:26px;padding:34px 32px;
font-size:36px;line-height:1.38;font-weight:600}
.try{background:#ECFDF5;color:#065F46;border-radius:28px;padding:40px 36px;font-size:40px;line-height:1.36;font-weight:700}
.bar{height:8px;background:#E5E7EB;margin:0 52px 56px;border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:#4338CA}"""


def page(lang: str, step: str, frac: float, inner: str) -> str:
    e = html.escape
    h = HEAD[lang]
    dv = " dv" if lang != "en" else ""
    top = (f'<div class="top"><div class="row"><div class="brand"><span class="mk"></span>Chanakya · {e(h["secs"])}</div>'
           f'<span class="pill">{e(h["pill"])}</span></div>'
           f'<div class="kick{dv}">{e(h["step"])}</div><div class="title">{e(t(f"step_{step}", lang))}</div></div>')
    return (f'<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{top}<div class="body">{inner}</div>'
            f'<div class="bar"><i style="width:{frac * 100:.0f}%"></i></div></body></html>')


def slides(step: str, lang: str) -> list[tuple[str, float]]:
    """(html, seconds) in order: about 45 seconds in all."""
    e = html.escape
    L = LESSONS[step][lang]
    h = HEAD[lang]
    dv = " dv" if lang != "en" else ""
    n = len(TIMES)
    frac = [sum(TIMES[: i + 1]) / sum(TIMES) for i in range(n)]
    out = [(page(lang, step, frac[0], f'<div class="goal"><small>{e(h["goal"]).upper() if lang == "en" else e(h["goal"])}</small>'
                                      f'{e(L["goal"])}</div>'), TIMES[0])]
    for shown in (1, 2, 3, 4):  # the four expert moves appear one by one (the newest highlighted)
        mv = "".join(f'<div class="mv{"" if i < shown else " dim"}{" new" if i == shown - 1 else ""}"><span class="n">{i + 1}</span>'
                     f'<span>{e(m)}</span></div>' for i, m in enumerate(L["moves"]))
        out.append((page(lang, step, frac[shown], f'<div class="k{dv}">{e(h["moves"])}</div>{mv}'), TIMES[shown]))
    cust, you = L["floor"]
    chat = (f'<div class="k{dv}">{e(h["floor"])}</div><div class="chat">'
            f'<div class="bub c"><small>{e(h["customer"])}</small>“{e(cust)}”</div>'
            f'<div class="bub y"><small>{e(h["you"])}</small>“{e(you)}”</div></div>')
    out.append((page(lang, step, frac[5], chat), TIMES[5]))
    av = (f'<div class="av bad"><small>{e(h["avoid"])}</small>{e(L["avoid"])}</div>'
          f'<div class="av good"><small>{e(h["instead"])}</small>{e(L["instead"])}</div>')
    out.append((page(lang, step, frac[6], av), TIMES[6]))
    out.append((page(lang, step, frac[7], f'<div class="k{dv}">{e(h["chanakya"])}</div><div class="help">{e(L["chanakya"])}</div>'),
                TIMES[7]))
    out.append((page(lang, step, frac[8], f'<div class="k{dv}">{e(h["try"])}</div><div class="try">{e(L["try"])}</div>'), TIMES[8]))
    return out


async def render_slides(pg, step: str, lang: str, d: Path) -> list[tuple[Path, float]]:
    out = []
    for i, (doc, secs) in enumerate(slides(step, lang)):
        await pg.set_content(doc, wait_until="load")
        await pg.evaluate("document.fonts.ready")
        f = d / f"s{i}.png"
        await pg.screenshot(path=str(f))
        out.append((f, secs))
    return out


def join(items: list[tuple[Path, float]], out: Path, fade: float = 0.35) -> None:
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    for f, secs in items:
        args += ["-loop", "1", "-t", f"{secs + fade:.2f}", "-i", str(f)]
    total = sum(s for _, s in items) + fade
    args += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=mono"]
    chain, last, offset = [], "[0:v]", 0.0
    for i in range(1, len(items)):
        offset += items[i - 1][1]
        tag = f"[v{i}]"
        chain.append(f"{last}[{i}:v]xfade=transition=fade:duration={fade}:offset={offset:.2f}{tag}")
        last = tag
    chain.append(f"{last}format=yuv420p[v]")
    args += ["-filter_complex", ";".join(chain), "-map", "[v]", "-map", f"{len(items)}:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "26", "-r", "24", "-c:a", "aac", "-b:a", "32k",
             "-shortest", "-movflags", "+faststart", str(out)]
    subprocess.run(args, check=True)


async def main() -> None:
    from playwright.async_api import async_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        pg = await browser.new_page(viewport={"width": 720, "height": 1280})
        for step in STEPS:
            if only and step not in only:
                continue
            for lang in ("hi", "mr", "en"):
                with tempfile.TemporaryDirectory() as d:
                    items = await render_slides(pg, step, lang, Path(d))
                    out = OUT / f"{step}_{lang}.mp4"
                    join(items, out)
                    print(out.name, out.stat().st_size // 1024, "KB")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
