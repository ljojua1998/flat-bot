"""Decide whether a listing is the same property as one we already have.

Brokers re-post the same flat with their own photos crops, text and a
slightly different price. We compare, within the same deal/kind/neighbourhood:
  * ss.ge's own similarityGroup, when present
  * a 64-bit difference hash of the main photo (survives resize/recompress)
  * area + floor + price closeness
Hard conflicts (different floor, room count or area) always block a match,
so identical developer renders of different units are not merged.
"""
import io
import urllib.request

from PIL import Image

from sources import UA

HASH_MAX_DISTANCE = 8


def image_hash(url):
    """dHash of the image at url as a 16-char hex string, or None."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.LANCZOS)
    except Exception:
        return None
    px = list(img.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def hamming(a, b):
    return (int(a, 16) ^ int(b, 16)).bit_count()


def _close(a, b, rel, absolute=0.0):
    return abs(a - b) <= max(absolute, rel * max(a, b))


def conflicts(a, b):
    if a["rooms"] and b["rooms"] and a["rooms"] != b["rooms"]:
        return True
    if a["floor"] is not None and b["floor"] is not None and a["floor"] != b["floor"]:
        return True
    if a["total_floors"] and b["total_floors"] and a["total_floors"] != b["total_floors"]:
        return True
    if a["area"] and b["area"] and not _close(a["area"], b["area"], 0.05, 2):
        return True
    return False


def same_property(a, b):
    if conflicts(a, b):
        return False
    if a.get("similarity") and a.get("similarity") == b.get("similarity"):
        return True
    if a.get("image_hash") and b.get("image_hash") and hamming(a["image_hash"], b["image_hash"]) <= HASH_MAX_DISTANCE:
        return True
    # developers sell many units with the same layout; only a photo match counts for them
    if a.get("owner") == "developer" or b.get("owner") == "developer":
        return False
    area_close = a["area"] and b["area"] and _close(a["area"], b["area"], 0.03, 1)
    floor_same = a["floor"] is not None and a["floor"] == b["floor"] and a["total_floors"] == b["total_floors"]
    price_close = a["price_usd"] and b["price_usd"] and _close(a["price_usd"], b["price_usd"], 0.15)
    return bool(area_close and floor_same and price_close)
