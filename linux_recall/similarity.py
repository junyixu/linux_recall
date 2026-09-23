"""Perceptual image similarity via difference hash (dHash).

Shrink to ``(size+1) x size`` grayscale and record, per pixel, whether it is
brighter than its right neighbour. Cheap (~tens of ms on a 4K crop), robust
to re-encoding and tiny changes such as a blinking cursor, but a page scroll
or a new document flips many bits.
"""

from PIL import Image

HASH_SIZE = 16  # 16x16 = 256 bits


def dhash(image: Image.Image, size: int = HASH_SIZE) -> str:
    """Return the hash as a hex string (JSON-friendly)."""
    small = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = small.load()
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (px[x, y] > px[x + 1, y])
    return f"{bits:0{size * size // 4}x}"


def similarity(a: str, b: str) -> float:
    """1.0 for identical hashes, ~0.5 for unrelated images."""
    if len(a) != len(b):
        return 0.0
    return 1 - (int(a, 16) ^ int(b, 16)).bit_count() / (len(a) * 4)
