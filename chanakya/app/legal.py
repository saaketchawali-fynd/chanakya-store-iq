"""Public Privacy Policy and data-deletion pages (needed to publish the Meta app)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from chanakya.config import get_settings

router = APIRouter()

_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;color:#333;"
    "line-height:1.55}h1{color:#1B2A4A}h2{color:#E8833A;font-size:1.1em;margin-top:1.6em}"
    ".b{background:#FDECEC;color:#963;padding:2px 8px;border-radius:6px;font-size:.85em}"
)


def _page(title: str, body: str) -> HTMLResponse:
    contact = get_settings().privacy_contact
    contact_html = f"<p>Contact: {contact}</p>" if contact else ""
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{_STYLE}</style>"
        f"</head><body><h1>{title}</h1><p><span class='b'>TEST SERVICE</span></p>{body}{contact_html}"
        "</body></html>"
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return _page(
        "Chanakya Store IQ — Privacy Policy",
        """
<p>Chanakya is an internal test prototype that helps Reliance Digital store associates on WhatsApp. It is used only
with test data and test phone numbers. It is not offered to the public.</p>
<h2>What we process</h2>
<p>WhatsApp phone numbers of registered test staff and of test customers who opt in by scanning a QR code;
messages, button taps, voice notes and photos sent to the test number; and the home details an associate types
(for example room size or seating distance) to suggest products.</p>
<h2>What we never do</h2>
<p>We do not message anyone who has not opted in. We do not infer wealth or income from appearance; room photos are
used only for space and light, and people in photos are blurred. We do not sell or share data with third parties.</p>
<h2>Retention</h2>
<p>Room photos are deleted after 7 days. Conversation records are kept only for the duration of the test.</p>
<h2>Your choices</h2>
<p>Reply STOP on WhatsApp to stop all messages. See <a href="/data-deletion">data deletion</a> to remove your data.</p>
""",
    )


@router.get("/data-deletion", response_class=HTMLResponse)
async def data_deletion():
    return _page(
        "Chanakya Store IQ — Data deletion",
        """
<p>To delete your data, send <b>STOP</b> to the Chanakya WhatsApp test number. We stop all messages at once, and the
project team removes your phone number, messages and photos from the test database within 7 days.
Room photos are deleted automatically after 7 days in any case.</p>
""",
    )
