"""Check the Meta side of WhatsApp delivery. Run it yourself; secrets are typed at a hidden prompt (or read from
WA_TOKEN / WA_BUSINESS_ACCOUNT_ID / WA_PHONE_NUMBER_ID env vars) and never printed.

    python scripts/wa_check.py               # read-only checks
    python scripts/wa_check.py --subscribe   # also link the WhatsApp Business Account to this app

Checks: the token works; the phone number ID is the number you are messaging; the WABA is subscribed to an app
(needed for message webhooks); and what the live Chanakya service has received (/health)."""

from __future__ import annotations

import getpass
import json
import os
import sys

import httpx

GRAPH = "https://graph.facebook.com/v23.0"
SERVICE = os.environ.get("CHANAKYA_URL", "https://chanakya-4s27.onrender.com")


def ask(env: str, prompt: str, secret: bool = False) -> str:
    v = os.environ.get(env, "").strip()
    if v:
        return v
    return (getpass.getpass(prompt) if secret else input(prompt)).strip()


def show(title: str, r: httpx.Response) -> dict:
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:300]}
    ok = r.status_code < 400
    print(f"\n{'OK ' if ok else 'ERR'} {title} (HTTP {r.status_code})")
    print("   " + json.dumps(data, ensure_ascii=False)[:600])
    return data if ok else {}


def main() -> None:
    token = ask("WA_TOKEN", "WA_TOKEN (hidden): ", secret=True)
    phone_id = ask("WA_PHONE_NUMBER_ID", "WA_PHONE_NUMBER_ID: ")
    waba = os.environ.get("WA_BUSINESS_ACCOUNT_ID", "").strip()
    h = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=20) as c:
        show("Token belongs to", c.get(f"{GRAPH}/me", headers=h))
        if not waba:  # find the WhatsApp Business Account(s) this token was granted
            dbg = c.get(f"{GRAPH}/debug_token", headers=h, params={"input_token": token}).json().get("data", {})
            ids = sorted(
                {
                    t
                    for sc in dbg.get("granular_scopes", [])
                    if sc.get("scope", "").startswith("whatsapp_")
                    for t in sc.get("target_ids", [])
                }
            )
            print(f"\nWhatsApp Business Account IDs on this token: {ids or 'none found'}")
            waba = ids[0] if len(ids) == 1 else ask("_", "WhatsApp Business Account ID to use: ")
        p = show(
            "Phone number (compare with the number you message)",
            c.get(
                f"{GRAPH}/{phone_id}",
                headers=h,
                params={"fields": "display_phone_number,verified_name,status,quality_rating"},
            ),
        )
        if waba:
            subs = show(
                "Apps subscribed to this WABA (must list your app)", c.get(f"{GRAPH}/{waba}/subscribed_apps", headers=h)
            )
            show(
                "Phone numbers under this WABA (must include the phone ID above)",
                c.get(f"{GRAPH}/{waba}/phone_numbers", headers=h, params={"fields": "id,display_phone_number"}),
            )
            if "--subscribe" in sys.argv:
                show("Subscribing this WABA to the app", c.post(f"{GRAPH}/{waba}/subscribed_apps", headers=h))
            elif subs.get("data") == []:
                print("\n→ No app is subscribed. Re-run with --subscribe to link it.")
        d = c.get(f"{SERVICE}/health").json().get("delivery", {})
        print("\nChanakya has received:", {k: v for k, v in d.items() if v} or "nothing yet")
        if p.get("display_phone_number"):
            print(f"\nSend 'Namaste' to {p['display_phone_number']} from the associate phone.")


if __name__ == "__main__":
    main()
