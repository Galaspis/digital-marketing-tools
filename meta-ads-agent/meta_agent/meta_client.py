"""Two backends behind one interface.

`LiveMeta` wraps the official `facebook_business` SDK.
`FakeMeta` is an in-memory stand-in with one sandbox account and one
read-only account, so the whole agent can be exercised before any Meta
credentials exist. Which one runs is decided by `Settings.dry_run`.

All methods return plain dicts/lists so tool results serialise to JSON.
"""

from __future__ import annotations

import random
import time
from datetime import date, timedelta
from typing import Any, Protocol

from .config import FAKE_SANDBOX, Settings

OBJECT_TYPES = ("account", "campaign", "adset", "ad")

CAMPAIGN_FIELDS = [
    "id", "name", "status", "effective_status", "objective",
    "daily_budget", "lifetime_budget", "created_time", "start_time", "stop_time",
]
ADSET_FIELDS = [
    "id", "name", "campaign_id", "status", "effective_status",
    "daily_budget", "lifetime_budget", "optimization_goal", "billing_event",
    "bid_strategy", "bid_amount", "targeting", "start_time", "end_time",
]
AD_FIELDS = ["id", "name", "adset_id", "status", "effective_status", "created_time"]
# Campaign effective_status values that are not ARCHIVED/DELETED. IN_PROCESS shows
# up for a while after any edit (seen live 2026-09-05).
LIVE_STATUSES = ("ACTIVE", "PAUSED", "IN_PROCESS", "WITH_ISSUES")
DEFAULT_INSIGHT_FIELDS = [
    "spend", "impressions", "reach", "clicks", "ctr", "cpc", "cpm",
    "frequency", "actions", "cost_per_action_type",
]


class MetaError(Exception):
    """A Meta API error, already flattened to a readable message."""


class MetaBackend(Protocol):
    def list_ad_accounts(self) -> list[dict]: ...
    def list_campaigns(self, account_id: str, include_archived: bool, limit: int) -> list[dict]: ...
    def list_adsets(self, campaign_id: str, limit: int) -> list[dict]: ...
    def list_ads(self, adset_id: str, limit: int) -> list[dict]: ...
    def get_insights(self, object_type: str, object_id: str, params: dict) -> list[dict]: ...
    def owner_account(self, object_type: str, object_id: str) -> str: ...
    def create_campaign(self, account_id: str, params: dict) -> dict: ...
    def create_adset(self, account_id: str, params: dict) -> dict: ...
    def update_object(self, object_type: str, object_id: str, params: dict) -> dict: ...


# --------------------------------------------------------------------------- live


class LiveMeta:
    def __init__(self, settings: Settings) -> None:
        from facebook_business.api import FacebookAdsApi

        FacebookAdsApi.init(
            settings.meta_app_id,
            settings.meta_app_secret,
            settings.meta_access_token,
            api_version=settings.meta_api_version or None,
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _obj(object_type: str, object_id: str):
        from facebook_business.adobjects.ad import Ad
        from facebook_business.adobjects.adaccount import AdAccount
        from facebook_business.adobjects.adset import AdSet
        from facebook_business.adobjects.campaign import Campaign

        cls = {"account": AdAccount, "campaign": Campaign, "adset": AdSet, "ad": Ad}[object_type]
        return cls(object_id)

    # Meta error codes that mean "slow down" rather than "you are wrong".
    RATE_LIMIT_CODES = {4, 17, 32, 613, 80000, 80001, 80002, 80003, 80004, 80005, 80006}
    RETRY_WAIT_SECONDS = 31  # dev tier: 1 call / 30 s per ad account
    MAX_ATTEMPTS = 4

    @classmethod
    def _run(cls, fn):
        from facebook_business.exceptions import FacebookRequestError

        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except FacebookRequestError as e:
                code = e.api_error_code()
                if code in cls.RATE_LIMIT_CODES and attempt < cls.MAX_ATTEMPTS:
                    time.sleep(cls.RETRY_WAIT_SECONDS)
                    continue
                raise MetaError(cls._describe(e)) from e

    @staticmethod
    def _describe(e) -> str:
        """Flatten a FacebookRequestError to one line, keeping Meta's own hints."""
        body = {}
        try:
            body = (e.body() or {}).get("error", {}) or {}
        except Exception:
            pass
        parts = [f"Meta API error {e.api_error_code()}"]
        if body.get("error_subcode"):
            parts.append(f"subcode {body['error_subcode']}")
        parts.append(f"({e.api_error_type()}): {e.api_error_message()}")
        for key in ("error_user_title", "error_user_msg"):
            if body.get(key):
                parts.append(f"| {body[key]}")
        if body.get("error_data"):
            parts.append(f"| data: {body['error_data']}")
        return " ".join(parts)

    @staticmethod
    def _rows(cursor) -> list[dict]:
        return [dict(item.export_all_data()) for item in cursor]

    # -- reads -------------------------------------------------------------

    def list_ad_accounts(self) -> list[dict]:
        from facebook_business.adobjects.user import User

        return self._run(lambda: self._rows(
            User(fbid="me").get_ad_accounts(
                fields=["id", "name", "account_status", "currency", "timezone_name"]
            )
        ))

    def list_campaigns(self, account_id: str, include_archived: bool, limit: int) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if not include_archived:
            params["effective_status"] = list(LIVE_STATUSES)
        return self._run(lambda: self._rows(
            self._obj("account", account_id).get_campaigns(fields=CAMPAIGN_FIELDS, params=params)
        ))

    def list_adsets(self, campaign_id: str, limit: int) -> list[dict]:
        return self._run(lambda: self._rows(
            self._obj("campaign", campaign_id).get_ad_sets(fields=ADSET_FIELDS, params={"limit": limit})
        ))

    def list_ads(self, adset_id: str, limit: int) -> list[dict]:
        return self._run(lambda: self._rows(
            self._obj("adset", adset_id).get_ads(fields=AD_FIELDS, params={"limit": limit})
        ))

    def get_insights(self, object_type: str, object_id: str, params: dict) -> list[dict]:
        fields = params.pop("fields", DEFAULT_INSIGHT_FIELDS)
        return self._run(lambda: self._rows(
            self._obj(object_type, object_id).get_insights(fields=fields, params=params)
        ))

    def owner_account(self, object_type: str, object_id: str) -> str:
        if object_type == "account":
            return object_id
        data = self._run(lambda: self._obj(object_type, object_id).api_get(fields=["account_id"]))
        return f"act_{data['account_id']}"

    # -- writes ------------------------------------------------------------

    def create_campaign(self, account_id: str, params: dict) -> dict:
        obj = self._run(lambda: self._obj("account", account_id).create_campaign(params=params))
        return dict(obj.export_all_data())

    def create_adset(self, account_id: str, params: dict) -> dict:
        obj = self._run(lambda: self._obj("account", account_id).create_ad_set(params=params))
        return dict(obj.export_all_data())

    def update_object(self, object_type: str, object_id: str, params: dict) -> dict:
        self._run(lambda: self._obj(object_type, object_id).api_update(params=params))
        return {"id": object_id, "updated": params}


# --------------------------------------------------------------------------- fake


class FakeMeta:
    """In-memory Meta. `act_111` behaves like the sandbox (writes allowed,
    insights all zero because sandbox ads never deliver); `act_222` is a
    pretend client account with real-looking numbers and is read-only."""

    SANDBOX = FAKE_SANDBOX
    CLIENT = "act_222"

    def __init__(self) -> None:
        self._next_id = 1000
        self.accounts = {
            self.SANDBOX: {"id": self.SANDBOX, "name": "Sandbox (fake)", "account_status": 1, "currency": "ILS"},
            self.CLIENT: {"id": self.CLIENT, "name": "Demo Client (fake, read-only)", "account_status": 1, "currency": "ILS"},
        }
        self.campaigns: dict[str, dict] = {}
        self.adsets: dict[str, dict] = {}
        self.ads: dict[str, dict] = {}
        self._seed()

    # -- seed --------------------------------------------------------------

    def _new_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def _seed(self) -> None:
        c1 = self._add_campaign(self.CLIENT, "Remarketing - site visitors", "OUTCOME_SALES", 15000, "ACTIVE")
        c2 = self._add_campaign(self.CLIENT, "Lead gen - customs brokerage", "OUTCOME_LEADS", 8000, "ACTIVE")
        c3 = self._add_campaign(self.CLIENT, "Old brand awareness", "OUTCOME_AWARENESS", 3000, "PAUSED")
        for cid, name, goal in (
            (c1, "Visitors 7d - IL", "OFFSITE_CONVERSIONS"),
            (c1, "Visitors 30d - IL", "OFFSITE_CONVERSIONS"),
            (c2, "Importers - IL 25-55", "LEAD_GENERATION"),
            (c3, "Broad - IL", "REACH"),
        ):
            aid = self._add_adset(self.CLIENT, cid, name, 4000, goal, "IMPRESSIONS", ["IL"], 25, 55)
            ad_id = self._new_id()
            self.ads[ad_id] = {"id": ad_id, "name": f"{name} - ad 1", "adset_id": aid,
                               "status": "ACTIVE", "effective_status": "ACTIVE",
                               "created_time": "2026-08-01T09:00:00+0000"}
        self._add_campaign(self.SANDBOX, "Sandbox test campaign", "OUTCOME_TRAFFIC", 1000, "PAUSED")

    def _add_campaign(self, account_id: str, name: str, objective: str, daily: int, status: str) -> str:
        cid = self._new_id()
        self.campaigns[cid] = {
            "id": cid, "account_id": account_id, "name": name, "status": status,
            "effective_status": status, "objective": objective,
            "daily_budget": str(daily), "lifetime_budget": None,
            "created_time": "2026-08-01T09:00:00+0000", "start_time": "2026-08-01T09:00:00+0000",
            "stop_time": None,
        }
        return cid

    def _add_adset(self, account_id, campaign_id, name, daily, goal, billing, countries, age_min, age_max) -> str:
        aid = self._new_id()
        self.adsets[aid] = {
            "id": aid, "account_id": account_id, "campaign_id": campaign_id, "name": name,
            "status": "ACTIVE", "effective_status": "ACTIVE", "daily_budget": str(daily),
            "lifetime_budget": None, "optimization_goal": goal, "billing_event": billing,
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "bid_amount": None,
            "targeting": {"geo_locations": {"countries": countries}, "age_min": age_min, "age_max": age_max},
            "start_time": "2026-08-01T09:00:00+0000", "end_time": None,
        }
        return aid

    # -- helpers -----------------------------------------------------------

    def _store(self, object_type: str) -> dict[str, dict]:
        return {"campaign": self.campaigns, "adset": self.adsets, "ad": self.ads}[object_type]

    def _get(self, object_type: str, object_id: str) -> dict:
        if object_type == "account":
            if object_id not in self.accounts:
                raise MetaError(f"Meta API error 100: Unsupported get request. Object {object_id} does not exist")
            return self.accounts[object_id]
        try:
            return self._store(object_type)[object_id]
        except KeyError:
            raise MetaError(f"Meta API error 100: Unsupported get request. Object {object_id} does not exist") from None

    @staticmethod
    def _public(row: dict, fields: list[str]) -> dict:
        return {k: row.get(k) for k in fields}

    # -- reads -------------------------------------------------------------

    def list_ad_accounts(self) -> list[dict]:
        return list(self.accounts.values())

    def list_campaigns(self, account_id: str, include_archived: bool, limit: int) -> list[dict]:
        self._get("account", account_id)
        rows = [c for c in self.campaigns.values() if c["account_id"] == account_id]
        if not include_archived:
            rows = [c for c in rows if c["effective_status"] in LIVE_STATUSES]
        return [self._public(c, CAMPAIGN_FIELDS) for c in rows[:limit]]

    def list_adsets(self, campaign_id: str, limit: int) -> list[dict]:
        self._get("campaign", campaign_id)
        rows = [a for a in self.adsets.values() if a["campaign_id"] == campaign_id]
        return [self._public(a, ADSET_FIELDS) for a in rows[:limit]]

    def list_ads(self, adset_id: str, limit: int) -> list[dict]:
        self._get("adset", adset_id)
        rows = [a for a in self.ads.values() if a["adset_id"] == adset_id]
        return [self._public(a, AD_FIELDS) for a in rows[:limit]]

    def get_insights(self, object_type: str, object_id: str, params: dict) -> list[dict]:
        obj = self._get(object_type, object_id)
        account = object_id if object_type == "account" else obj["account_id"]
        days = {"today": 1, "yesterday": 1, "last_3d": 3, "last_7d": 7, "last_14d": 14,
                "last_28d": 28, "last_30d": 30, "this_month": date.today().day,
                "last_month": 30, "maximum": 35}.get(params.get("date_preset", "last_7d"), 7)
        increment = params.get("time_increment")
        buckets = days if increment == "1" else 1
        rng = random.Random(f"{object_id}-{params.get('date_preset')}")
        rows = []
        end = date.today() - timedelta(days=1)
        for i in range(buckets):
            span = 1 if buckets > 1 else days
            zero = account == self.SANDBOX or obj.get("status") == "PAUSED" and object_type != "account"
            impressions = 0 if zero else rng.randint(800, 2500) * span
            clicks = 0 if zero else int(impressions * rng.uniform(0.008, 0.03))
            spend = 0.0 if zero else round(clicks * rng.uniform(1.8, 4.5), 2)
            leads = 0 if zero else int(clicks * rng.uniform(0.02, 0.08))
            stop = end - timedelta(days=(buckets - 1 - i) if buckets > 1 else 0)
            start = stop - timedelta(days=span - 1)
            rows.append({
                "date_start": start.isoformat(), "date_stop": stop.isoformat(),
                "spend": f"{spend:.2f}", "impressions": str(impressions),
                "reach": str(int(impressions * 0.7)), "clicks": str(clicks),
                "ctr": f"{(clicks / impressions * 100) if impressions else 0:.4f}",
                "cpc": f"{(spend / clicks) if clicks else 0:.2f}",
                "cpm": f"{(spend / impressions * 1000) if impressions else 0:.2f}",
                "frequency": "1.43" if impressions else "0",
                "actions": [{"action_type": "lead", "value": str(leads)}] if leads else [],
                "cost_per_action_type": [{"action_type": "lead", "value": f"{spend / leads:.2f}"}] if leads else [],
            })
        return rows

    def owner_account(self, object_type: str, object_id: str) -> str:
        if object_type == "account":
            self._get("account", object_id)
            return object_id
        return self._get(object_type, object_id)["account_id"]

    # -- writes ------------------------------------------------------------

    def create_campaign(self, account_id: str, params: dict) -> dict:
        self._get("account", account_id)
        cid = self._add_campaign(account_id, params["name"], params["objective"],
                                 int(params.get("daily_budget") or 0), params.get("status", "PAUSED"))
        if params.get("lifetime_budget"):
            self.campaigns[cid]["lifetime_budget"] = str(params["lifetime_budget"])
        return self._public(self.campaigns[cid], CAMPAIGN_FIELDS)

    def create_adset(self, account_id: str, params: dict) -> dict:
        self._get("account", account_id)
        self._get("campaign", params["campaign_id"])
        t = params["targeting"]
        aid = self._add_adset(account_id, params["campaign_id"], params["name"],
                              int(params.get("daily_budget") or 0), params["optimization_goal"],
                              params["billing_event"], t["geo_locations"]["countries"],
                              t.get("age_min", 18), t.get("age_max", 65))
        self.adsets[aid]["status"] = self.adsets[aid]["effective_status"] = params.get("status", "PAUSED")
        return self._public(self.adsets[aid], ADSET_FIELDS)

    def update_object(self, object_type: str, object_id: str, params: dict) -> dict:
        row = self._get(object_type, object_id)
        for k, v in params.items():
            row[k] = str(v) if k.endswith("_budget") else v
            if k == "status":
                row["effective_status"] = v
        return {"id": object_id, "updated": params}


def make_backend(settings: Settings) -> MetaBackend:
    return FakeMeta() if settings.dry_run else LiveMeta(settings)
