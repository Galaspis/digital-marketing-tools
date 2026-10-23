"""Settings from .env. Nothing here talks to any API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
HUB_ROOT = ROOT.parent
# One .env for the whole hub (digital-marketing-tools/.env). It OVERRIDES any
# variable already set in the OS environment: an explicit line in the file beats a
# stale system-wide key (that bit us with OPENAI_API_KEY). A service-local .env,
# if present, overrides both — handy for a one-off experiment.
load_dotenv(HUB_ROOT / ".env", override=True)
load_dotenv(ROOT / ".env", override=True)


def _env(name: str, default: str = "") -> str:
    """os.getenv, but an EMPTY value counts as unset (the .env has placeholder lines)."""
    value = os.getenv(name)
    return default if value is None or value == "" else value

# Ids used by the in-memory fake channel (see channels/meta.py FakeMetaChannel).
FAKE_PAGE = "fbpage_1"
FAKE_IG = "ig_1"


def _list(raw: str | None) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    meta_app_id: str | None
    meta_app_secret: str | None
    meta_user_token: str | None
    graph_version: str
    publish_targets: list[str]
    max_posts_per_day: int
    r2_endpoint: str | None
    r2_access_key: str | None
    r2_secret_key: str | None
    r2_bucket: str | None
    r2_public_base: str | None
    media_dir: Path
    openai_api_key: str | None
    image_model: str
    max_images_per_day: int
    image_price_text_in: float     # USD per 1M tokens, for cost estimates
    image_price_image_in: float
    image_price_image_out: float
    model: str
    effort: str
    fallbacks: bool
    db_path: Path
    log_path: Path
    monthly_cap_usd: float
    client: str
    ledger_path: Path

    @property
    def dry_run(self) -> bool:
        """No Meta token means the in-memory fake channel."""
        return not self.meta_user_token

    @property
    def images_enabled(self) -> bool:
        return bool(self.openai_api_key) or self.dry_run

    @property
    def media_configured(self) -> bool:
        return bool(self.r2_endpoint and self.r2_access_key and self.r2_secret_key
                    and self.r2_bucket and self.r2_public_base)


def load_settings() -> Settings:
    token = _env("SOCIAL_META_USER_TOKEN") or None
    targets = _list(_env("SOCIAL_PUBLISH_TARGETS"))
    if token is None and not targets:
        targets = [FAKE_PAGE, FAKE_IG]
    media_dir = Path(_env("SOCIAL_MEDIA_DIR") or (ROOT / "media")).resolve()
    return Settings(
        meta_app_id=_env("META_APP_ID") or None,
        meta_app_secret=_env("META_APP_SECRET") or None,
        meta_user_token=token,
        graph_version=_env("META_GRAPH_VERSION", "v26.0"),
        publish_targets=targets,
        max_posts_per_day=int(_env("SOCIAL_MAX_POSTS_PER_DAY", "10")),
        r2_endpoint=_env("R2_ENDPOINT") or None,
        r2_access_key=_env("R2_ACCESS_KEY_ID") or None,
        r2_secret_key=_env("R2_SECRET_ACCESS_KEY") or None,
        r2_bucket=_env("R2_BUCKET") or None,
        r2_public_base=(_env("R2_PUBLIC_BASE_URL") or "").rstrip("/") or None,
        media_dir=media_dir,
        openai_api_key=_env("OPENAI_API_KEY") or None,
        image_model=_env("IMAGE_MODEL", "gpt-image-2"),
        max_images_per_day=int(_env("SOCIAL_MAX_IMAGES_PER_DAY", "20")),
        # Defaults are gpt-image-1 list prices (2025); override to match the model you use.
        image_price_text_in=float(_env("IMAGE_PRICE_TEXT_IN_PER_M", "5")),
        image_price_image_in=float(_env("IMAGE_PRICE_IMAGE_IN_PER_M", "10")),
        image_price_image_out=float(_env("IMAGE_PRICE_IMAGE_OUT_PER_M", "40")),
        model=_env("CLAUDE_MODEL", "claude-opus-5"),
        effort=_env("CLAUDE_EFFORT", "high"),
        fallbacks=_env("CLAUDE_FALLBACKS", "1") not in ("0", "false", "no"),
        db_path=Path(_env("SOCIAL_DB_PATH") or (ROOT / "data" / "posts.sqlite")),
        log_path=ROOT / "logs" / "actions.jsonl",
        monthly_cap_usd=float(_env("MONTHLY_API_CAP_USD", "100")),
        client=_env("CLIENT", "agency"),
        ledger_path=Path(_env("LEDGER_PATH") or (HUB_ROOT / "data" / "ledger.sqlite")),
    )
