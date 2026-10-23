"""Image generation for posts and ads.

Live: OpenAI Images API (`client.images.generate`, model from IMAGE_MODEL,
default gpt-image-2; verified against developers.openai.com on 2026-09-06:
sizes 1024x1024 / 1536x1024 / 1024x1536 / 2048x2048 and larger, quality
low | medium | high | auto, base64 in `data[0].b64_json`, token `usage`).
Cost is estimated from the returned usage with the IMAGE_PRICE_* rates.

Fake: writes a solid-colour PNG so the whole flow runs without a key.

Files land in <media_dir>/generated/ and are then treated like any other
media item (uploaded to hosting for Instagram, direct for Facebook).
"""

from __future__ import annotations

import base64
import re
import struct
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Settings

SIZES = ("1024x1024", "1536x1024", "1024x1536", "2048x2048")
QUALITIES = ("low", "medium", "high")


class ImageError(Exception):
    pass


def _slug(text: str, n: int = 32) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n] or "image"


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Minimal valid PNG without Pillow: one colour, 8-bit RGB."""
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


class BaseGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.out_dir = settings.media_dir / "generated"

    def _target_path(self, prompt: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self.out_dir / f"{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6]}-{_slug(prompt)}.png"

    @staticmethod
    def validate(size: str, quality: str) -> None:
        if size not in SIZES:
            raise ImageError(f"size must be one of {SIZES}")
        if quality not in QUALITIES:
            raise ImageError(f"quality must be one of {QUALITIES}")

    def estimate_usd(self, usage: dict[str, Any]) -> float:
        s = self.settings
        details = usage.get("input_tokens_details") or {}
        text_in = details.get("text_tokens", usage.get("input_tokens", 0))
        image_in = details.get("image_tokens", 0)
        out = usage.get("output_tokens", 0)
        return round((text_in * s.image_price_text_in + image_in * s.image_price_image_in
                      + out * s.image_price_image_out) / 1_000_000, 4)


class OpenAIImageGenerator(BaseGenerator):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        from openai import OpenAI

        self.client = OpenAI(api_key=settings.openai_api_key)

    def generate(self, prompt: str, size: str = "1024x1024", quality: str = "medium") -> dict[str, Any]:
        self.validate(size, quality)
        try:
            result = self.client.images.generate(model=self.settings.image_model, prompt=prompt,
                                                 size=size, quality=quality, n=1, output_format="png")
        except Exception as e:  # openai.APIError and friends: surface the message
            raise ImageError(f"OpenAI image error: {type(e).__name__}: {e}") from e
        item = result.data[0]
        if not getattr(item, "b64_json", None):
            raise ImageError("OpenAI returned no image data.")
        path = self._target_path(prompt)
        path.write_bytes(base64.b64decode(item.b64_json))
        usage = result.usage.model_dump() if getattr(result, "usage", None) else {}
        return {"path": path, "model": self.settings.image_model, "size": size, "quality": quality,
                "revised_prompt": getattr(item, "revised_prompt", None), "usage": usage,
                "cost_usd": self.estimate_usd(usage) if usage else None}


class FakeImageGenerator(BaseGenerator):
    def generate(self, prompt: str, size: str = "1024x1024", quality: str = "medium") -> dict[str, Any]:
        self.validate(size, quality)
        w, h = (int(x) for x in size.split("x"))
        h_ = sum(ord(c) for c in prompt)
        rgb = (60 + h_ % 150, 90 + (h_ // 7) % 130, 120 + (h_ // 13) % 120)
        path = self._target_path(prompt)
        path.write_bytes(_solid_png(min(w, 256), min(h, 256), rgb))  # small; enough for a preview
        usage = {"input_tokens": 20, "input_tokens_details": {"text_tokens": 20, "image_tokens": 0},
                 "output_tokens": {"low": 272, "medium": 1056, "high": 4160}[quality]}
        return {"path": path, "model": "fake-image", "size": size, "quality": quality,
                "revised_prompt": None, "usage": usage, "cost_usd": self.estimate_usd(usage)}


class NoImageGenerator(BaseGenerator):
    def generate(self, prompt: str, size: str = "1024x1024", quality: str = "medium") -> dict[str, Any]:
        raise ImageError("Image generation is not configured: set OPENAI_API_KEY in .env (see SETUP.md).")


def make_image_generator(settings: Settings):
    import os

    # Escape hatch for tests and demos: never call OpenAI, whatever keys exist.
    if os.getenv("SOCIAL_IMAGE_BACKEND", "").lower() == "fake":
        return FakeImageGenerator(settings)
    if settings.openai_api_key:
        return OpenAIImageGenerator(settings)
    return FakeImageGenerator(settings) if settings.dry_run else NoImageGenerator(settings)


def images_in_last_day(guard) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    return sum(1 for e in guard.tail(2000) if e.get("action") == "generate_image"
               and e.get("approved") is not False and e.get("ts", "") >= since)
