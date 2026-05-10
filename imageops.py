"""
Image manipulation for poster and still guessing rounds.
"""
import io
import random
from typing import Literal

import aiohttp
from PIL import Image, ImageFilter


Difficulty = Literal["easy", "medium", "hard"]


async def download_image(url: str) -> bytes | None:
    """Download an image from a URL and return its bytes."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
    except Exception:
        return None


def make_puzzle(image_bytes: bytes, difficulty: Difficulty = "medium") -> bytes:
    """
    Apply a crop or blur to a poster.

    - easy: gentle blur, full image visible
    - medium: small random crop (35% of poster)
    - hard: tiny random crop (20% of poster) with slight blur
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size

    if difficulty == "easy":
        result = img.filter(ImageFilter.GaussianBlur(radius=18))
    elif difficulty == "medium":
        crop_w = int(width * 0.35)
        crop_h = int(height * 0.35)
        x = random.randint(0, width - crop_w)
        y = random.randint(0, height - crop_h)
        result = img.crop((x, y, x + crop_w, y + crop_h))
        result = result.resize((crop_w * 2, crop_h * 2), Image.LANCZOS)
    else:  # hard
        crop_w = int(width * 0.20)
        crop_h = int(height * 0.20)
        x = random.randint(0, width - crop_w)
        y = random.randint(0, height - crop_h)
        result = img.crop((x, y, x + crop_w, y + crop_h))
        result = result.resize((crop_w * 3, crop_h * 3), Image.LANCZOS)
        result = result.filter(ImageFilter.GaussianBlur(radius=2))

    out = io.BytesIO()
    result.save(out, format="JPEG", quality=85)
    return out.getvalue()


def make_still_puzzle(image_bytes: bytes, difficulty: Difficulty = "medium") -> bytes:
    """
    Apply transformation to a movie still (backdrop image).

    Stills are inherently more recognizable than poster crops, so:
    - easy: unmodified backdrop
    - medium: moderate blur (still recognizable but fuzzy)
    - hard: small random crop (35% of the still)
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size

    if difficulty == "easy":
        result = img
    elif difficulty == "medium":
        result = img.filter(ImageFilter.GaussianBlur(radius=10))
    else:  # hard
        crop_w = int(width * 0.35)
        crop_h = int(height * 0.35)
        x = random.randint(0, width - crop_w)
        y = random.randint(0, height - crop_h)
        result = img.crop((x, y, x + crop_w, y + crop_h))
        result = result.resize((crop_w * 2, crop_h * 2), Image.LANCZOS)

    out = io.BytesIO()
    result.save(out, format="JPEG", quality=85)
    return out.getvalue()