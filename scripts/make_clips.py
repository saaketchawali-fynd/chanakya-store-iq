"""Make the 'Aaj ka clip' videos: one ~22-second vertical clip per step and language (data/clips/<step>_<lang>.mp4).

Focused on what to keep in mind, in five clean slides: the step and its rule; three points to keep in mind (revealed
one by one); the line to say; what to avoid; one thing to try today. Rendered with Playwright, joined with
crossfades by ffmpeg (H.264, silent AAC track, faststart) so WhatsApp plays them inline.
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
INTRO = {"hi": "20 second mein: 3 baatein jo aaj dhyan rakhni hain", "mr": "20 सेकंदात: आज लक्षात ठेवायच्या 3 गोष्टी",
         "en": "In 20 seconds: 3 things to keep in mind today"}
HEAD = {"hi": ("Aaj ka step", "Dhyan rahe", "Aise boliye", "Ye mat kariye", "Aaj try kariye"),
        "mr": ("आजचा टप्पा", "लक्षात ठेवा", "असं बोला", "हे टाळा", "आज करून बघा"),
        "en": ("Today's step", "Keep in mind", "Say it like this", "Avoid", "Try it today")}

# step → lang → (three points to keep in mind, the line to say, what to avoid, one thing to try today)
CLIP = {
    "assess": {
        "en": (["One question at a time: why now, then the home", "Ask about life, not specs: 'how far is the sofa?'",
                "Say the home back before you recommend"],
               "How many steps from the sofa to the TV?", "Don't start with brands or price",
               "Read the home back to one customer: 'So, for your home…'"),
        "hi": (["Ek baar mein ek sawaal: pehle kyun abhi, phir ghar", "Spec nahi, zindagi poochiye: 'sofa kitni door hai?'",
                "Suggest karne se pehle ghar dohraaiye"],
               "सोफ़े से टीवी कितने कदम दूर होगा?", "Brand ya daam se shuru mat kariye",
               "Ek customer ko ghar dohraaiye: 'To aapke ghar ke liye…'"),
        "mr": (["एका वेळी एक प्रश्न: आधी आत्ता का, मग घर", "स्पेक्स नाही, जगणं विचारा: 'सोफा किती लांब?'",
                "सुचवण्याआधी घर पुन्हा सांगा"],
               "सोफ्यापासून टीव्ही किती पावलं लांब असेल?", "ब्रँड किंवा किमतीपासून सुरुवात करू नका",
               "एका ग्राहकाला घर पुन्हा सांगा: 'तर तुमच्या घरासाठी…'"),
    },
    "validate": {
        "en": (["Agree with the true part first", "Then correct with one fact about their home",
                "Still unsure? Show it: a demo beats an argument"],
               "Good thinking. Let's match it to your room.", "Never say 'that's wrong'",
               "Answer one doubt with 'True, and…'"),
        "hi": (["Pehle sahi hissa maaniye", "Phir unke ghar ki ek baat se sudhaariye",
                "Shak bacha ho to dikhaiye: demo bahas se behtar"],
               "बहुत सही सोचा है। एक बार कमरे से मिला लेते हैं।", "Kabhi mat kahiye 'ye galat hai'",
               "Ek shak ka jawab 'sahi, aur…' se dijiye"),
        "mr": (["आधी खरा भाग मान्य करा", "मग त्यांच्या घराच्या एका गोष्टीने दुरुस्त करा",
                "शंका उरली तर दाखवा: डेमो वादापेक्षा चांगला"],
               "छान विचार केलाय. एकदा खोलीशी जुळवून बघू.", "कधीही म्हणू नका 'हे चूक आहे'",
               "एका शंकेचं उत्तर 'बरोबर, आणि…' ने द्या"),
    },
    "translate": {
        "en": (["Turn every spec into daily life", "Tie it to their home: 'because in your home…'",
                "Ask 'Does that matter to you?', then listen"],
               "120 Hz means the ball stays sharp in cricket.", "Don't read the label aloud",
               "Add 'because in your home…' to every feature"),
        "hi": (["Har spec ko roz ki zindagi mein badaliye", "Ghar se jodiye: 'kyunki aapke ghar mein…'",
                "Poochiye 'ye aapke kaam ka hai?', phir suniye"],
               "120 Hz मतलब क्रिकेट में बॉल धुंधली नहीं होगी।", "Label padh kar mat sunaiye",
               "Har feature ke saath 'kyunki aapke ghar mein…' jodiye"),
        "mr": (["प्रत्येक स्पेक रोजच्या जगण्यात बदला", "घराशी जोडा: 'कारण तुमच्या घरात…'",
                "विचारा 'हे तुमच्या कामाचं आहे का?', मग ऐका"],
               "120 Hz म्हणजे क्रिकेटमध्ये बॉल धूसर दिसणार नाही.", "लेबल वाचून दाखवू नका",
               "प्रत्येक फीचरसोबत 'कारण तुमच्या घरात…' जोडा"),
    },
    "demonstrate": {
        "en": (["Three options: lead with the best fit", "Show one difference they can see, in 20 seconds",
                "Then ask: 'Which one felt right?'"],
               "Look at both from your sofa distance.", "Don't talk over the demo",
               "Run one side-by-side demo, then stay quiet"),
        "hi": (["Teen options: sabse sahi fit se shuru", "Ek farak dikhaiye jo aankh se dikhe, 20 second mein",
                "Phir poochiye: 'kaunsa sahi laga?'"],
               "अपने सोफ़े जितनी दूरी से दोनों देखिए।", "Demo ke beech bolte mat rahiye",
               "Ek side-by-side demo kariye, phir chup rahiye"),
        "mr": (["तीन पर्याय: सर्वात योग्य पर्यायाने सुरुवात", "डोळ्यांना दिसणारा एक फरक, 20 सेकंदात",
                "मग विचारा: 'कोणता योग्य वाटला?'"],
               "तुमच्या सोफ्याइतक्या अंतरावरून दोन्ही पाहा.", "डेमोच्या मध्ये बोलत राहू नका",
               "एक शेजारी-शेजारी डेमो करा, मग शांत राहा"),
    },
    "complete": {
        "en": (["Ask the site questions that change the cost first", "Full cost, line by line, before any offer",
                "They can remove extras; required charges stay"],
               "This is the full cost; the bill will match it.", "No surprise charges at billing",
               "Show the full-cost card before any offer"),
        "hi": (["Kharcha badalne wale sawaal pehle", "Offer se pehle poora kharcha, line by line",
                "Customer extra hata sakte hain; zaroori kharcha rahega"],
               "ये पूरा खर्च है — बिल में यही आएगा।", "Billing par koi surprise kharcha nahi",
               "Offer se pehle poora kharcha card dikhaiye"),
        "mr": (["खर्च बदलणारे प्रश्न आधी", "ऑफरआधी पूर्ण खर्च, ओळीनुसार",
                "ग्राहक जादा वस्तू काढू शकतात; आवश्यक खर्च राहतो"],
               "हा पूर्ण खर्च आहे — बिलात हेच येईल.", "बिलिंगला कोणताही सरप्राइज खर्च नाही",
               "ऑफरआधी पूर्ण खर्चाचं कार्ड दाखवा"),
    },
    "close": {
        "en": (["Price after the demo; offers only from the data", "Ask how they'll pay: only those offers apply",
                "Close with a choice: 'Saturday or Sunday?'"],
               "Delivery on Saturday or Sunday?", "Never 'today only' or 'last piece'",
               "On 'not today', ask: 'What's holding you back?'"),
        "hi": (["Daam demo ke baad; offer sirf data se", "Payment poochiye: wahi offer lagte hain",
                "Vikalp se close: 'Shanivaar ya Ravivaar?'"],
               "डिलीवरी शनिवार को चाहिए या रविवार को?", "Kabhi 'sirf aaj' ya 'aakhri piece' nahi",
               "'Aaj nahi' par poochiye: 'kis baat par ruk rahe hain?'"),
        "mr": (["किंमत डेमोनंतर; ऑफर फक्त डेटातून", "पेमेंट विचारा: त्याच ऑफर लागू",
                "पर्यायाने क्लोज: 'शनिवार की रविवार?'"],
               "डिलिव्हरी शनिवारी हवी की रविवारी?", "कधीही 'फक्त आज' किंवा 'शेवटचा पीस' नाही",
               "'आज नाही' म्हटल्यावर विचारा: 'कशामुळे थांबताय?'"),
    },
    "follow_through": {
        "en": (["Consent first: the QR, then WhatsApp", "The Family card goes to whoever decides",
                "Every customer leaves with a task and an owner"],
               "May I send all this to your WhatsApp?", "Never message without consent",
               "Send one Family card, even if they don't buy"),
        "hi": (["Pehle consent: QR, phir WhatsApp", "Parivaar Card faisla lene wale tak",
                "Har customer ke saath ek task aur uska owner"],
               "क्या मैं ये सब आपके WhatsApp पर भेज दूँ?", "Bina consent message kabhi nahi",
               "Ek Parivaar Card bhejiye, chahe kharida na ho"),
        "mr": (["आधी संमती: QR, मग WhatsApp", "परिवार कार्ड निर्णय घेणाऱ्यापर्यंत",
                "प्रत्येक ग्राहकासोबत एक टास्क आणि त्याचा मालक"],
               "हे सगळं तुमच्या WhatsApp वर पाठवू का?", "संमतीशिवाय कधीही मेसेज नाही",
               "एक परिवार कार्ड पाठवा, खरेदी झाली नाही तरी"),
    },
}

CSS = """*{margin:0;box-sizing:border-box}body{width:720px;height:1280px;font-family:"Noto Sans","Noto Sans Devanagari",
"Kohinoor Devanagari","Devanagari Sangam MN",sans-serif;background:#F6F3EE;color:#1B2A4A;display:flex;flex-direction:column}
.top{background:linear-gradient(140deg,#18264A,#2A3F6C);color:#fff;padding:72px 60px 54px;border-radius:0 0 44px 44px}
.brand{font-size:24px;font-weight:700;opacity:.75;letter-spacing:.5px}.step{margin-top:26px;font-size:28px;font-weight:700;color:#F4A86F}
.title{margin-top:8px;font-size:58px;font-weight:800;line-height:1.15}.rule{margin-top:14px;font-size:34px;color:#D6DDEB;line-height:1.35}
.body{flex:1;padding:56px 60px 0}.k{font-size:30px;font-weight:800;color:#B65E1C;letter-spacing:1px;text-transform:uppercase}
.k.dv{text-transform:none;letter-spacing:0}.pt{display:flex;gap:22px;align-items:flex-start;margin-top:34px;font-size:40px;line-height:1.36;font-weight:600}
.pt .n{flex:none;width:58px;height:58px;border-radius:50%;background:#E8833A;color:#fff;font-size:30px;font-weight:800;display:flex;align-items:center;justify-content:center;margin-top:2px}
.pt.dim{opacity:.18}.quote{margin-top:34px;background:#fff;border-radius:32px;padding:44px 42px;font-size:46px;line-height:1.38;
font-weight:700;box-shadow:0 10px 30px rgba(27,42,74,.10);border-left:12px solid #2E8B57}.avoid{margin-top:34px;background:#FBE4E1;
color:#9B2F28;border-radius:32px;padding:44px 42px;font-size:44px;line-height:1.38;font-weight:700}.try{margin-top:34px;background:#FDF0E4;
border-radius:32px;padding:44px 42px;font-size:44px;line-height:1.38;font-weight:700;border:3px solid #F6D2AE}
.bar{height:10px;background:#E9E3DA;margin:0 60px 70px;border-radius:5px;overflow:hidden}.bar i{display:block;height:100%;background:#E8833A}"""


def page(lang: str, step: str, frac: float, inner: str, big_head: bool) -> str:
    e = html.escape
    h = HEAD[lang]
    top = (f'<div class="top"><div class="brand">Chanakya · Store IQ</div><div class="step">{e(h[0])}</div>'
           f'<div class="title">{e(t(f"step_{step}", lang))}</div>'
           + (f'<div class="rule">{e(t(NITI[step], lang))}</div>' if big_head else "") + "</div>")
    return (f'<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{top}<div class="body">{inner}</div>'
            f'<div class="bar"><i style="width:{frac * 100:.0f}%"></i></div></body></html>')


def slides(step: str, lang: str) -> list[tuple[str, float]]:
    """(html, seconds) in order."""
    e = html.escape
    keep, say, avoid, try_ = CLIP[step][lang]
    h = HEAD[lang]
    dv = " dv" if lang != "en" else ""
    intro = f'<div class="quote" style="border-left-color:#E8833A;font-size:42px">{e(INTRO[lang])}</div>'
    out = [(page(lang, step, 0.12, intro, True), 3.2)]
    for shown in (1, 2, 3):  # the three points appear one by one
        pts = "".join(f'<div class="pt{"" if i < shown else " dim"}"><span class="n">{i + 1}</span><span>{e(p)}</span></div>'
                      for i, p in enumerate(keep))
        out.append((page(lang, step, 0.12 + 0.14 * shown, f'<div class="k{dv}">{e(h[1])}</div>{pts}', False),
                    2.6 if shown < 3 else 3.4))
    out.append((page(lang, step, 0.72, f'<div class="k{dv}">{e(h[2])}</div><div class="quote">“{e(say)}”</div>', False), 4.0))
    out.append((page(lang, step, 0.86, f'<div class="k{dv}">{e(h[3])}</div><div class="avoid">{e(avoid)}</div>', False), 3.0))
    out.append((page(lang, step, 1.0, f'<div class="k{dv}">{e(h[4])}</div><div class="try">{e(try_)}</div>', False), 3.6))
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
