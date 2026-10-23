"""Settings loaded from .env. Nothing here talks to any API."""

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

# The in-memory fake's sandbox account (see meta_client.FakeMeta). In dry run it
# is the default write target so the create/pause flow can be exercised.
FAKE_SANDBOX = "act_111"


def normalise_account_id(value: str) -> str:
    """Meta ad account ids are used as `act_<digits>` everywhere in the API."""
    value = value.strip()
    if not value:
        return value
    return value if value.startswith("act_") else f"act_{value}"


def _id_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [normalise_account_id(x) for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    meta_app_id: str | None
    meta_app_secret: str | None
    meta_access_token: str | None
    meta_api_version: str
    read_accounts: list[str]
    write_accounts: list[str]
    max_daily_budget_minor: int
    model: str
    effort: str
    fallbacks: bool
    log_path: Path
    monthly_cap_usd: float
    client: str
    ledger_path: Path
    openai_api_key: str | None

    @property
    def dry_run(self) -> bool:
        """No Meta token means we run against the in-memory fake backend."""
        return not self.meta_access_token


def load_settings() -> Settings:
    token = _env("META_ACCESS_TOKEN") or None
    write_accounts = _id_list(_env("META_WRITE_ACCOUNT_IDS"))
    if token is None and not write_accounts:
        write_accounts = [FAKE_SANDBOX]
    return Settings(
        meta_app_id=_env("META_APP_ID") or None,
        meta_app_secret=_env("META_APP_SECRET") or None,
        meta_access_token=token,
        # Empty = whatever the installed facebook_business SDK targets (v26.0 for SDK 26.x).
        meta_api_version=_env("META_API_VERSION") or "",
        read_accounts=_id_list(_env("META_READ_ACCOUNT_IDS")),
        write_accounts=write_accounts,
        max_daily_budget_minor=int(_env("MAX_DAILY_BUDGET_MINOR", "5000")),
        model=_env("CLAUDE_MODEL", "claude-opus-5"),
        effort=_env("CLAUDE_EFFORT", "high"),
        fallbacks=_env("CLAUDE_FALLBACKS", "1") not in ("0", "false", "no"),
        log_path=ROOT / "logs" / "actions.jsonl",
        monthly_cap_usd=float(_env("MONTHLY_API_CAP_USD", "100")),
        client=_env("CLIENT", "agency"),
        ledger_path=Path(_env("LEDGER_PATH") or (HUB_ROOT / "data" / "ledger.sqlite")),
        openai_api_key=_env("OPENAI_API_KEY") or None,
    )
