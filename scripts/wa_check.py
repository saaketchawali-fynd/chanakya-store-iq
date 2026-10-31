"""Check and fix the Meta side of WhatsApp delivery. Run it yourself; the token is typed at a hidden prompt
(or read from WA_TOKEN) and never printed.

    python scripts/wa_check.py                # read-only: token, app, number, account, subscribed apps
    python scripts/wa_check.py --subscribe    # also subscribe YOUR app to the WhatsApp Business Account
    python scripts/wa_check.py --send         # also send Meta's hello_world template to your phone

Why --subscribe matters: on new Meta apps the test number's WhatsApp Business Account (WABA) delivers real
webhooks only to the apps subscribed to it — often just Meta's internal "WA DevX Webhook Events 1P App".
The dashboard "Test" button bypasses this, so tests arrive while real messages never do."""

from __future__ import annotations

import getpass
import json
import os
import sys

import httpx

GRAPH = "https://graph.facebook.com/v23.0"
SERVICE = os.environ.get("CHANAKYA_URL", "https://chanakya-4s27.onrender.com")


def ask(env: str | None, prompt: str, secret: bool = False) -> str:
    v = os.environ.get(env, "").strip() if env else ""
    while not v:  # re-ask until something is entered
        v = (getpass.getpass(prompt) if secret else input(prompt)).strip()
    return v


def show(title: str, r: httpx.Response) -> dict:
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:300]}
    ok = r.status_code < 400
    print(f"\n{'OK ' if ok else 'ERR'} {title} (HTTP {r.status_code})")
    print("   " + json.dumps(data, ensure_ascii=False)[:700])
    return data if ok else {}


def subscribed(c: httpx.Client, h: dict, waba: str, app_id: str | None) -> bool | None:
    d = show(
        f"Apps subscribed to WhatsApp Business Account {waba}", c.get(f"{GRAPH}/{waba}/subscribed_apps", headers=h)
    )
    if not d:
        return None
    rows = [(a.get("whatsapp_business_api_data") or a) for a in d.get("data", [])]
    ids = [str(x.get("id")) for x in rows]
    names = [x.get("name") for x in rows]
    mine = bool(app_id) and app_id in ids
    print(f"   → subscribed apps: {names or 'none'}")
    print(
        "   → YOUR app is subscribed ✓"
        if mine
        else "   → YOUR app is NOT subscribed ✗ (real messages won't reach Chanakya)"
    )
    return mine


def main() -> None:
    token = ask("WA_TOKEN", "WA_TOKEN (hidden): ", secret=True)
    phone_id = ask("WA_PHONE_NUMBER_ID", "WA_PHONE_NUMBER_ID: ")
    h = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=25) as c:
        show("Token belongs to", c.get(f"{GRAPH}/me", headers=h))
        r = c.get(f"{GRAPH}/debug_token", headers=h, params={"input_token": token})
        td = r.json().get("data", {}) if r.status_code < 400 else {}
        app_id = str(td.get("app_id") or "")
        print(f"\nToken was issued for app: {td.get('application')} (app id {app_id or '?'})")

        base = "display_phone_number,verified_name,status,quality_rating"
        r = c.get(f"{GRAPH}/{phone_id}", headers=h, params={"fields": base + ",health_status,webhook_configuration"})
        if r.status_code >= 400:  # if the extra fields are refused, retry without them
            r = c.get(f"{GRAPH}/{phone_id}", headers=h, params={"fields": base})
        p = show("Phone number", r)
        wabas = [
            str(e.get("id"))
            for e in (p.get("health_status") or {}).get("entities", [])
            if e.get("entity_type") == "WABA" and e.get("id")
        ]
        if p.get("webhook_configuration"):
            print(f"   → webhook used for this number: {p['webhook_configuration']}")

        waba = os.environ.get("WA_BUSINESS_ACCOUNT_ID", "").strip() or (wabas[0] if wabas else "")
        if waba:
            print(f"\nWhatsApp Business Account ID (from the phone number): {waba}")
        else:
            waba = ask(
                None, "WhatsApp Business Account ID (App Dashboard → Connect on WhatsApp → Step 1. Try it out): "
            )

        mine = subscribed(c, h, waba, app_id)
        if "--subscribe" in sys.argv:
            show(
                "Subscribing YOUR app to this WhatsApp Business Account",
                c.post(f"{GRAPH}/{waba}/subscribed_apps", headers=h),
            )
            mine = subscribed(c, h, waba, app_id)

        if "--send" in sys.argv:
            i = sys.argv.index("--send")
            to = "".join(ch for ch in (sys.argv[i + 1] if len(sys.argv) > i + 1 else "") if ch.isdigit())
            while len(to) < 10:  # missing or a placeholder like 91XXXXXXXXXX
                to = "".join(ch for ch in input("Your WhatsApp number (e.g. 919876543210): ") if ch.isdigit())
            if len(to) == 10:
                to = "91" + to
            show(
                f"Sending the hello_world template to {to} with THIS token",
                c.post(
                    f"{GRAPH}/{phone_id}/messages",
                    headers=h,
                    json={
                        "messaging_product": "whatsapp",
                        "to": to,
                        "type": "template",
                        "template": {"name": "hello_world", "language": {"code": "en_US"}},
                    },
                ),
            )

        try:
            d = c.get(f"{SERVICE}/health").json().get("delivery", {})
            print("\nChanakya has received:", {k: v for k, v in d.items() if v} or "nothing yet")
        except Exception:
            pass
        if mine:
            print(
                f"\nREADY: send 'Namaste' to {p.get('display_phone_number', 'the test number')} from the associate phone."
            )
        elif mine is False and "--subscribe" not in sys.argv:
            print("\nNEXT: run again with --subscribe to connect your app.")


if __name__ == "__main__":
    main()
