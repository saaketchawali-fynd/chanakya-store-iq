"""Never-dead-end replies (spec 7.8). No numbers, always valid."""

from __future__ import annotations

from chanakya.channel.limits import truncate_title
from chanakya.core.buttons import encode
from chanakya.core.contracts import Button, OutMessage, SessionState, Step
from chanakya.i18n.lang import t


def safe_reply(to: str, lang: str, session: SessionState | None) -> OutMessage:
    def b(key: str, bid: str) -> Button:
        return Button(id=bid, title=truncate_title(t(key, lang)))

    if session is None or session.step == Step.CLOSED:
        buttons = [
            b("b_new_customer", encode("g", "new")),
            b("b_status", encode("g", "status")),
            b("b_help", encode("g", "help")),
        ]
    elif session.step == Step.ASSESS:
        c = session.code
        buttons = [
            b("b_next_q", encode("s", c, "go")),
            b("b_options", encode("s", c, "options")),
            b("b_status", encode("g", "status")),
        ]
    else:
        c = session.code
        buttons = [
            b("b_options", encode("s", c, "options")),
            b("b_cost_show", encode("s", c, "cost_show")),
            b("b_status", encode("g", "status")),
        ]
    return OutMessage(to=to, kind="buttons", body=t("fallback_body", lang), buttons=buttons)


def plain(to: str, lang: str, key: str = "fallback_body") -> OutMessage:
    return OutMessage(to=to, kind="text", body=t(key, lang))


def role_reply(to: str, lang: str, role: str | None) -> OutMessage | None:
    """After a failure, the manager, the expert and the visitor get their own way back (never the associate's
    customer buttons)."""
    home = {"manager": ("b_menu", encode("m", "menu")), "expert": ("b_expert_queue", encode("x", 0, "list")),
            "visitor": ("b_visit_home", encode("v", 0, "home", 1))}.get(role or "")
    if home is None:
        return None
    return OutMessage(to=to, kind="buttons", body=t("fallback_body", lang),
                      buttons=[Button(id=home[1], title=truncate_title(t(home[0], lang)))])
