from chanakya.channel.parse import parse


def _wrap(messages=None, statuses=None):
    v = {"messaging_product": "whatsapp", "contacts": [{"profile": {"name": "Ravi"}, "wa_id": "917225882392"}]}
    if messages:
        v["messages"] = messages
    if statuses:
        v["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "W", "changes": [{"field": "messages", "value": v}]}],
    }


def test_text_audio_image_buttons_lists():
    msgs, _ = parse(
        _wrap(
            [
                {
                    "from": "917225882392",
                    "id": "w1",
                    "timestamp": "1793500000",
                    "type": "text",
                    "text": {"body": "Namaste"},
                },
                {
                    "from": "917225882392",
                    "id": "w2",
                    "timestamp": "1793500000",
                    "type": "audio",
                    "audio": {"mime_type": "audio/ogg; codecs=opus", "id": "M1", "voice": True},
                },
                {
                    "from": "917225882392",
                    "id": "w3",
                    "timestamp": "1793500000",
                    "type": "image",
                    "image": {"mime_type": "image/jpeg", "id": "M2", "caption": "label"},
                },
                {
                    "from": "917225882392",
                    "id": "w4",
                    "timestamp": "1793500050",
                    "type": "interactive",
                    "context": {"from": "15551234567", "id": "wamid.PREV"},
                    "interactive": {
                        "type": "button_reply",
                        "button_reply": {"id": "s:C1:slot:mounting:wall", "title": "Deewar"},
                    },
                },
                {
                    "from": "917225882392",
                    "id": "w5",
                    "timestamp": "1793500050",
                    "type": "interactive",
                    "interactive": {"type": "list_reply", "list_reply": {"id": "s:C1:budget:b2", "title": "40k–70k"}},
                },
                {"from": "917225882392", "id": "w6", "timestamp": "1793500050", "type": "sticker", "sticker": {}},
            ]
        )
    )
    assert [m.type for m in msgs] == ["text", "audio", "image", "button_reply", "list_reply", "unsupported"]
    assert msgs[0].text == "Namaste" and msgs[0].profile_name == "Ravi"
    assert msgs[1].is_voice and msgs[1].media_id == "M1"
    assert msgs[2].text == "label"
    assert msgs[3].reply_id == "s:C1:slot:mounting:wall" and msgs[3].context_message_id == "wamid.PREV"
    assert msgs[4].reply_id == "s:C1:budget:b2"


def test_statuses():
    _, st = parse(
        _wrap(
            statuses=[
                {
                    "id": "o1",
                    "status": "failed",
                    "recipient_id": "9172",
                    "errors": [{"code": 131047, "title": "Re-engagement message"}],
                }
            ]
        )
    )
    assert st[0]["status"] == "failed" and st[0]["errors"][0]["code"] == 131047
