"""Chanakya test-label QR codes: 'CHANAKYA:SKU:<sku>' → the exact product, independent of OCR quality."""

from __future__ import annotations

import io

from chanakya.log import get_log

log = get_log("qr")
PREFIX = "CHANAKYA:SKU:"


def decode_sku(img_bytes: bytes) -> str | None:
    """Find a Chanakya label QR in a photo (tries a few scales, as phone photos vary a lot)."""
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageOps

        im = ImageOps.exif_transpose(Image.open(io.BytesIO(img_bytes))).convert("L")
        det = cv2.QRCodeDetector()
        for target in (None, 1600, 1000, 2400):
            work = im
            if target and max(im.size) != target:
                k = target / max(im.size)
                work = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))))
            arr = np.array(work)
            for a in (arr, cv2.GaussianBlur(arr, (3, 3), 0)):
                text, _, _ = det.detectAndDecode(a)
                if text and text.startswith(PREFIX):
                    return text[len(PREFIX):].strip()
    except Exception as e:  # noqa: BLE001 — a failed decode just falls back to reading the label text
        log.warning("qr_decode_failed", err=str(e)[:120])
    return None
