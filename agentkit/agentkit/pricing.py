"""The price book: every model we pay for, with the price per million tokens
(or per image), where the number came from and when it was checked.

The book is `pricing.json` at the hub root. Nothing in code carries a price;
if a vendor changes prices, that file changes, and old ledger rows keep the
cost they were recorded at.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HUB_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_BOOK = HUB_ROOT / "pricing.json"


class PriceBook:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_BOOK
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.checked = data.get("checked")
        self.models: dict[str, dict[str, Any]] = data["models"]

    # -- lookup ------------------------------------------------------------

    def get(self, key: str) -> dict[str, Any] | None:
        """`key` is "provider/model" (e.g. "openai/gpt-5.6-sol") or a bare model id."""
        if key in self.models:
            return {"key": key, **self.models[key]}
        for k, m in self.models.items():
            if m.get("model") == key:
                return {"key": k, **m}
        return None

    def list(self, kind: str | None = None, provider: str | None = None) -> list[dict[str, Any]]:
        out = []
        for k, m in self.models.items():
            if kind and m.get("kind") != kind:
                continue
            if provider and m.get("provider") != provider:
                continue
            out.append({"key": k, **m})
        return out

    # -- cost --------------------------------------------------------------

    def text_cost(self, key: str, input_tokens: int = 0, output_tokens: int = 0,
                  cache_read: int = 0, cache_write: int = 0) -> float:
        m = self.get(key)
        if m is None:
            return 0.0
        p = m["per_million"]
        usd = (input_tokens * p.get("input", 0) + output_tokens * p.get("output", 0)
               + cache_read * p.get("cache_read", 0) + cache_write * p.get("cache_write", 0)) / 1_000_000
        return round(usd, 6)

    def image_cost(self, key: str, usage: dict[str, Any]) -> float:
        """From the usage object the Images API returns."""
        m = self.get(key)
        if m is None:
            return 0.0
        p = m["per_million"]
        details = usage.get("input_tokens_details") or {}
        text_in = details.get("text_tokens", usage.get("input_tokens", 0) or 0)
        image_in = details.get("image_tokens", 0) or 0
        out = usage.get("output_tokens", 0) or 0
        usd = (text_in * p.get("text_input", 0) + image_in * p.get("image_input", 0)
               + out * p.get("image_output", 0)) / 1_000_000
        return round(usd, 6)

    def image_typical_cost(self, key: str, quality: str, size: str = "1024x1024") -> float | None:
        """Rough per-image estimate before generating, for the picker labels."""
        m = self.get(key)
        if m is None:
            return None
        tokens = (m.get("typical_output_tokens") or {}).get(quality)
        if tokens is None:
            return None
        w, h = (int(x) for x in size.split("x"))
        scale = (w * h) / (1024 * 1024)
        return round(tokens * scale * m["per_million"].get("image_output", 0) / 1_000_000, 4)
