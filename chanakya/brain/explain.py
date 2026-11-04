"""'Explain the difference' for the data questions (user, 4 Nov 2026): customers often don't know what LED, QLED,
NanoCell, OLED, Google TV or webOS mean, so the associate gets a simple analogy for each choice, in the customer's
language, and a hint from THIS home (light, viewing, hours of use …). The analogies describe how each kind of
technology works in general, never a claim about one model; the stock numbers stay with the associate's reason.
Lines said to the customer: Hindi in Roman script (Hinglish), Marathi in Devanagari, English. Technical terms stay English."""

from __future__ import annotations

from chanakya.brain import packs as P

# (attribute, value key) → analogy per language
ANALOGY: dict[tuple[str, str], dict[str, str]] = {
    ("panel", "led"): {"en": "LED is like a good bright bulb: clear picture, the best value.",
                       "hi": "LED ek achhe tez bulb jaisa hai: saaf picture, sabse kifaayati.",
                       "mr": "LED चांगल्या तेजस्वी बल्बसारखा: स्पष्ट चित्र, सर्वात किफायतशीर."},
    ("panel", "qled"): {"en": "QLED adds a colour layer, like good sunglasses: richer, brighter colours.",
                        "hi": "QLED mein rangon ki ek parat hoti hai, jaise achhe dhoop ke chashme: rang zyada gehre aur chamkeele.",
                        "mr": "QLED मध्ये रंगांचा एक थर असतो, जसा चांगला गॉगल: रंग अधिक गडद आणि उजळ."},
    ("panel", "nanocell"): {"en": "NanoCell is LG's colour filter on LED: purer colours, a similar idea to QLED.",
                            "hi": "NanoCell LG ki colour-filter technology hai: zyada shuddh rang, QLED jaisi soch.",
                            "mr": "NanoCell ही LG ची रंग-फिल्टर तंत्रज्ञान: अधिक शुद्ध रंग, QLED सारखी कल्पना."},
    ("panel", "oled"): {"en": "In OLED every dot lights itself, like stars at night: true black, best for movies in a dark room.",
                        "hi": "OLED mein har dot khud jalta hai, jaise raat mein taare: asli kaala, andhere kamre mein filmon ke liye behtareen.",
                        "mr": "OLED मध्ये प्रत्येक बिंदू स्वतः उजळतो, जसे रात्रीचे तारे: खरा काळा, अंधाऱ्या खोलीत सिनेमासाठी उत्तम."},
    ("panel", "miniled"): {"en": "Mini LED has thousands of tiny lamps behind the screen: very bright with deep black.",
                           "hi": "Mini LED mein screen ke peechhe hazaaron chhote lamp: bahut tez roshni aur gehra kaala.",
                           "mr": "Mini LED मध्ये स्क्रीनमागे हजारो लहान दिवे: खूप तेज आणि गडद काळा."},
    ("os", "google"): {"en": "Google TV works like an Android phone: Google apps, voice search, cast from your phone.",
                       "hi": "Google TV ek Android phone jaisa chalta hai: Google apps, bolkar search, phone se cast.",
                       "mr": "Google TV Android फोनसारखा चालतो: Google अ‍ॅप्स, बोलून शोध, फोनवरून कास्ट."},
    ("os", "webos"): {"en": "webOS is LG's simple system with the Magic Remote: point and click, like a mouse.",
                      "hi": "webOS LG ka aasaan system hai, Magic Remote ke saath: mouse ki tarah ishaara karke click.",
                      "mr": "webOS ही LG ची सोपी सिस्टम, Magic Remote सोबत: माउससारखं दाखवून क्लिक."},
    ("os", "tizen"): {"en": "Tizen is Samsung's fast, simple system; it works smoothly with Samsung phones.",
                      "hi": "Tizen Samsung ka tez, aasaan system hai; Samsung phone ke saath aaram se chalta hai.",
                      "mr": "Tizen ही Samsung ची वेगवान, सोपी सिस्टम; Samsung फोनसोबत छान चालते."},
    ("os", "vidaa"): {"en": "VIDAA is a light, quick system: the main apps with simple menus.",
                      "hi": "VIDAA halka, tez system hai: main apps, aasaan menu.",
                      "mr": "VIDAA हलकी, जलद सिस्टम: मुख्य अ‍ॅप्स, सोपे मेन्यू."},
    ("os", "fire"): {"en": "Fire TV is Amazon's system: Prime Video and Alexa built in.",
                     "hi": "Fire TV Amazon ka system hai: Prime Video aur Alexa pehle se.",
                     "mr": "Fire TV ही Amazon ची सिस्टम: Prime Video आणि Alexa आधीच."},
    ("os", "basic"): {"en": "A basic smart system: the main apps like YouTube and Netflix, simple to use.",
                      "hi": "Ek basic smart system: YouTube, Netflix jaise main apps, chalane mein aasaan.",
                      "mr": "एक बेसिक स्मार्ट सिस्टम: YouTube, Netflix सारखे मुख्य अ‍ॅप्स, वापरायला सोपी."},
    ("resolution", "4k"): {"en": "4K has four times the detail of Full HD: sharp even up close, best on bigger screens.",
                           "hi": "4K mein Full HD se chaar guna detail: paas se bhi saaf, badi screen ke liye behtar.",
                           "mr": "4K मध्ये Full HD पेक्षा चारपट बारकावे: जवळूनही स्पष्ट, मोठ्या स्क्रीनसाठी उत्तम."},
    ("resolution", "full hd"): {"en": "Full HD is clear on smaller screens from a normal distance.",
                                "hi": "Full HD chhoti screen par normal doori se saaf dikhta hai.",
                                "mr": "Full HD लहान स्क्रीनवर नेहमीच्या अंतरावरून स्पष्ट दिसतो."},
    ("resolution", "hd"): {"en": "HD is fine for small screens and regular TV channels.",
                           "hi": "HD chhoti screen aur aam TV channels ke liye theek hai.",
                           "mr": "HD लहान स्क्रीन आणि नेहमीच्या TV चॅनेलसाठी ठीक."},
    ("inverter", "true"): {"en": "An inverter is like a car cruising at a steady speed: steady cooling, lower bills.",
                           "hi": "Inverter ek jaisi raftaar se chalti car jaisa: baraabar thandak, kam bill.",
                           "mr": "Inverter एकसारख्या वेगाने चालणाऱ्या गाडीसारखा: सारखा गारवा, कमी बिल."},
    ("inverter", "false"): {"en": "Non-inverter is like stop-start driving: lower price, a higher bill with long use.",
                            "hi": "Non-inverter ruk-ruk kar chalne jaisa: daam kam, zyada chalane par bill zyada.",
                            "mr": "Non-inverter थांबत-थांबत चालवण्यासारखा: किंमत कमी, जास्त वापरल्यास बिल जास्त."},
    ("frost_free", "true"): {"en": "Frost free defrosts itself: no ice build-up, even cooling.",
                             "hi": "Frost free khud barf hatata hai: barf nahi jamti, baraabar thandak.",
                             "mr": "Frost free स्वतः बर्फ काढतो: बर्फ साचत नाही, सारखा गारवा."},
    ("frost_free", "false"): {"en": "Direct cool needs defrosting now and then: a lower price and simple to run.",
                              "hi": "Direct cool mein kabhi-kabhi barf hatani padti hai: daam kam, chalana aasaan.",
                              "mr": "Direct cool मध्ये कधीतरी बर्फ काढावा लागतो: किंमत कमी, वापरायला सोपा."},
    ("load", "front"): {"en": "Front load tumbles like a dryer drum: gentler on clothes and uses less water, but you bend to load.",
                        "hi": "Front load drum ki tarah ghumata hai: kapdon par naram, paani kam, par jhukkar daalna padta hai.",
                        "mr": "Front load ड्रमसारखं फिरवतं: कपड्यांवर सौम्य, पाणी कमी, पण वाकून टाकावं लागतं."},
    ("load", "top"): {"en": "Top load you fill standing up: no bending, quicker washes.",
                      "hi": "Top load khade-khade bharte hain: jhukna nahi, dhulai jaldi.",
                      "mr": "Top load उभ्याने भरता येतं: वाकायचं नाही, धुलाई लवकर."},
    ("automatic", "true"): {"en": "Fully automatic washes, rinses and spins in one go: set it and forget it.",
                            "hi": "Fully automatic ek baar mein dhona, khangaalna, sukhaana: set kijiye aur bhool jaaiye.",
                            "mr": "Fully automatic एकाच वेळी धुणं, खंगाळणं, पिळणं: सेट करा आणि विसरा."},
    ("automatic", "false"): {"en": "Semi automatic has two tubs and you move the clothes: a lower price, more hands-on.",
                             "hi": "Semi automatic mein do tub, kapde khud badalne hote hain: daam kam, thoda zyada kaam.",
                             "mr": "Semi automatic मध्ये दोन टब, कपडे स्वतः हलवावे लागतात: किंमत कमी, थोडं जास्त काम."},
}
NEED: dict[str, dict] = {  # attribute → [(home condition, hint)], first match wins; then the default
    "panel": {"rules": [({"slot": "room_light", "in": ["sunny"]},
                         {"en": "Your room gets sun: the brighter ones (QLED, Mini LED) stay clearer.",
                          "hi": "Aapke kamre mein dhoop aati hai: zyada chamkeele (QLED, Mini LED) saaf rehte hain.",
                          "mr": "तुमच्या खोलीत ऊन येतं: जास्त उजळ (QLED, Mini LED) स्पष्ट राहतात."}),
                        ({"slot": "room_light", "in": ["dim"]},
                         {"en": "Your room is mostly dim: OLED's true black shines for movies.",
                          "hi": "Aapka kamra zyadatar andhera rehta hai: filmon ke liye OLED ka asli kaala behtareen.",
                          "mr": "तुमची खोली बहुतेक अंधारी असते: सिनेमासाठी OLED चा खरा काळा उत्तम."})],
              "default": {"en": "For normal light and a careful budget, LED is plenty.",
                          "hi": "Normal roshni aur soch-samajhkar budget ho toh LED kaafi hai.",
                          "mr": "नेहमीचा उजेड आणि मोजकं बजेट असेल तर LED पुरेसा."}},
    "os": {"rules": [],
           "default": {"en": "If the family uses Android phones, Google TV feels familiar; all of them run the main apps.",
                       "hi": "Ghar mein Android phone hon toh Google TV jaana-pehchaana lagega; sabmein main apps chalte hain.",
                       "mr": "घरी Android फोन असतील तर Google TV ओळखीचा वाटेल; सगळ्यांत मुख्य अ‍ॅप्स चालतात."}},
    "resolution": {"rules": [],
                   "default": {"en": "For 50 inch and above, 4K makes the difference you can see.",
                               "hi": "50 inch aur usse bade mein 4K ka farak saaf dikhta hai.",
                               "mr": "50 inch आणि त्याहून मोठ्यामध्ये 4K चा फरक स्पष्ट दिसतो."}},
    "inverter": {"rules": [({"slot": "hours_per_day", "gte": 8},
                            {"en": "It runs many hours a day here: an inverter pays back in the bill.",
                             "hi": "Yahan roz kai ghante chalega: inverter bill mein paise bachata hai.",
                             "mr": "इथे रोज अनेक तास चालेल: inverter बिलात पैसे वाचवतो."})],
                 "default": {"en": "Used only a few hours a day? Either works; the inverter saves more over time.",
                             "hi": "Roz thoda hi chalega? Dono theek; inverter samay ke saath zyada bachata hai.",
                             "mr": "रोज थोडंच चालेल? दोन्ही चालतील; inverter कालांतराने जास्त वाचवतो."}},
    "frost_free": {"rules": [], "default": {"en": "For a busy family and weekly shopping, frost free saves effort.",
                                            "hi": "Busy family aur hafte ki kharidaari ho toh frost free mehnat bachata hai.",
                                            "mr": "व्यस्त कुटुंब आणि आठवड्याची खरेदी असेल तर frost free मेहनत वाचवतो."}},
    "load": {"rules": [({"slot": "elders_back", "in": ["yes"]},
                        {"en": "Bending is hard for whoever loads: top load is easier.",
                         "hi": "Kapde daalne wale ko jhukna mushkil hai: top load aasaan rahega.",
                         "mr": "कपडे टाकणाऱ्याला वाकणं कठीण: top load सोपं."})],
             "default": {"en": "Front load is gentler on clothes; top load is easier to load.",
                         "hi": "Front load kapdon par naram; top load daalne mein aasaan.",
                         "mr": "Front load कपड्यांवर सौम्य; top load टाकायला सोपं."}},
    "automatic": {"rules": [], "default": {"en": "For daily washing with little time, fully automatic is easiest.",
                                           "hi": "Roz dhulai aur kam samay ho toh fully automatic sabse aasaan.",
                                           "mr": "रोज धुलाई आणि कमी वेळ असेल तर fully automatic सर्वात सोपं."}},
}


def _key(attr: str, value) -> str:
    v = str(value).lower()
    if attr == "panel":
        return next((k for k in ("miniled", "oled", "qled", "nanocell", "led") if k in v.replace(" ", "").replace("-", "")), v)
    if attr == "os":
        if "google" in v or "android" in v:
            return "google"
        return next((k for k in ("webos", "tizen", "vidaa", "fire") if k in v.replace(" ", "")), "basic")
    if attr == "resolution":
        return "4k" if "4k" in v or "uhd" in v else "full hd" if "full" in v or "fhd" in v else "hd" if "hd" in v else v
    return v


def analogy(attr: str, value, lang: str) -> str | None:
    d = ANALOGY.get((attr, _key(attr, value)))
    return (d.get(lang) or d.get("en")) if d else None


def can_explain(attr: str, values: list) -> bool:
    return len(values) >= 2 and all(analogy(attr, v, "en") for v in values[:2])


def need_hint(attr: str, home, pack, lang: str) -> str | None:
    spec = NEED.get(attr)
    if not spec:
        return None
    for cond, hint in spec["rules"]:
        sid = cond["slot"]
        if "in" in cond and any(v in cond["in"] for v in P.values(home, sid)):
            return hint.get(lang) or hint["en"]
        if "gte" in cond:
            n = P.number(home, sid, pack)
            if n is not None and n >= cond["gte"]:
                return hint.get(lang) or hint["en"]
    d = spec.get("default")
    return (d.get(lang) or d.get("en")) if d else None
