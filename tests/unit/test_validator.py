from datetime import UTC, datetime

from chanakya.core import validator
from chanakya.core.contracts import Button, Card, HomeCard, OutMessage, SlotValue
from chanakya.truth.firewall import NumberRegistry

BANNED = ["sirf aaj", "last piece"]


def _home():
    return HomeCard(slots={"room_light": [SlotValue(slot="room_light", value="sunny", at=datetime.now(UTC))]})


def test_lines_buttons_titles():
    reg = NumberRegistry()
    m = OutMessage(
        to="1", kind="buttons", body="a\nb", buttons=[Button(id=f"g:x{i}", title="t" * 20) for i in range(1)]
    )
    m.buttons.append(Button.model_construct(id="g:y", title="x" * 30))
    m.buttons += [Button(id="g:z", title="z"), Button(id="g:w", title="w")]
    c = validator.check(m, reg, None, "hi", BANNED)
    assert not c.fatal
    assert len(c.msg.buttons) == 3 and all(len(b.title) <= 20 for b in c.msg.buttons)
    long = OutMessage(to="1", kind="text", body="\n".join(str(i) for i in range(30)))
    reg.static(" ".join(str(i) for i in range(30)))
    c = validator.check(long, reg, None, "hi", BANNED)  # past the 3-line floor rule: never sent long
    assert not c.fatal and len(c.msg.body.split("\n")) == 3 and any(v.kind == "too_many_lines" for v in c.violations)
    spaced = OutMessage(to="1", kind="text", body="a\n\n\nb")
    assert validator.check(spaced, reg, None, "hi", BANNED).msg.body == "a\nb"  # no blank lines


def test_coach_needs_kyun_and_known_cite():
    reg = NumberRegistry()
    ok = OutMessage(to="1", kind="text", body='*Bolo*: "x"\n*Kyun*: y', coach=True, why_cites=["room_light"])
    assert not validator.check(ok, reg, _home(), "hi", BANNED).fatal
    bad = ok.model_copy(update={"why_cites": ["mounting"]})
    assert validator.check(bad, reg, _home(), "hi", BANNED).fatal
    assert not validator.check(bad, reg, _home(), "hi", BANNED, asking={"mounting"}).fatal
    nokyun = ok.model_copy(update={"body": "hello"})
    assert validator.check(nokyun, reg, _home(), "hi", BANNED).fatal


def test_firewall_and_banned_in_card_data():
    reg = NumberRegistry()
    price = reg.inr(62990, "p")
    m = OutMessage(
        to="1",
        kind="image",
        body="ok",
        card=Card(
            template="kharcha", lang="hi", data={"lines": [{"amount_str": price}], "zaroori_total_str": "₹40,000"}
        ),
    )
    assert validator.check(m, reg, None, "hi", BANNED).fatal
    m2 = OutMessage(to="1", kind="text", body="Sirf aaj ka offer hai")
    assert validator.check(m2, reg, None, "hi", BANNED).fatal


def test_enforce_replaces_with_fallback():
    reg = NumberRegistry()
    m = OutMessage(to="1", kind="text", body="₹40,000 only")
    out, v = validator.enforce(
        m, reg, None, "hi", BANNED, None, lambda: OutMessage(to="x", kind="text", body="fallback")
    )
    assert out.body == "fallback" and out.to == "1" and any(x.kind == "firewall" for x in v)
