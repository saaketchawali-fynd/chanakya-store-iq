"""Optional harmonise step: an injected editor with `async edit_image(bytes, prompt) -> bytes | None`.
The image model never decides size or position; verify.check guards the result."""

from __future__ import annotations

import io

from PIL import Image

HARMONISE = (
    "This photo shows a real room with a television that has been placed on the wall digitally, plus a\n"
    "dashed outline showing a larger size option. Make the television look naturally mounted: add a soft,\n"
    "realistic shadow on the wall and match the room's lighting and colour on the TV frame and screen\n"
    "reflections. Do NOT move, resize, rotate or reshape the television or the dashed outline. Do NOT change\n"
    "anything else in the room. Do not add or remove objects, people or text. Keep the image size identical."
)


async def run(editor, img: Image.Image) -> Image.Image | None:
    """Returns the edited image, or None when there is no editor / it returns nothing / it fails."""
    if editor is None:
        return None
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=92)
    try:
        data = await editor.edit_image(buf.getvalue(), HARMONISE)
    except Exception:
        return None
    if not data:
        return None
    try:
        out = Image.open(io.BytesIO(data))
        out.load()
        return out.convert("RGB")
    except Exception:
        return None
