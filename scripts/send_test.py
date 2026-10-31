"""Send a test message via the WhatsApp channel: python scripts/send_test.py 91XXXXXXXXXX "Namaste (test)"."""

import asyncio
import sys

from chanakya.channel.whatsapp import WhatsAppChannel
from chanakya.config import get_settings
from chanakya.core.contracts import Button, OutMessage


async def main(to: str, text: str) -> None:
    ch = WhatsAppChannel(get_settings())
    try:
        wamid = await ch.send(
            OutMessage(
                to=to,
                kind="buttons",
                body=text,
                buttons=[Button(id="g:new", title="Naya customer"), Button(id="g:help", title="Madad")],
            )
        )
        print("sent", wamid)
    finally:
        await ch.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "Namaste! Chanakya test message."))
