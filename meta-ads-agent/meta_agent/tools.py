"""Claude-callable tools. Each one is a thin, typed wrapper: validate the
input, pass it through the Guard, call the backend, return JSON text.

Read tools never prompt. Write tools go through `Guard.authorize_write`,
which enforces the sandbox-only rule, the budget cap and the y/n approval.
Any policy refusal or Meta error is returned to Claude as text (never
raised), so Claude can explain it to the operator instead of crashing.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import beta_tool

from agentkit import note_campaign

from .config import normalise_account_id
from .guard import Guard
from .meta_client import DEFAULT_INSIGHT_FIELDS, OBJECT_TYPES, MetaBackend, MetaError

OBJECTIVES = ("OUTCOME_TRAFFIC", "OUTCOME_LEADS", "OUTCOME_SALES",
              "OUTCOME_AWARENESS", "OUTCOME_ENGAGEMENT", "OUTCOME_APP_PROMOTION")
DATE_PRESETS = ("today", "yesterday", "last_3d", "last_7d", "last_14d", "last_28d",
                "last_30d", "this_month", "last_month", "maximum")
STATUSES = ("ACTIVE", "PAUSED", "ARCHIVED")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _err(msg: str) -> str:
    return _json({"error": msg})


def build_tools(backend: MetaBackend, guard: Guard) -> list:
    """Return the list of @beta_tool functions bound to this backend + guard."""

    def run(fn):
        try:
            return _json(fn())
        except MetaError as e:
            return _err(str(e))
        except (KeyError, ValueError, TypeError) as e:
            return _err(f"Bad input: {e}")

    # ------------------------------------------------------------ reads

    @beta_tool
    def list_ad_accounts() -> str:
        """List the Meta ad accounts this token can see, with id, name, status and currency.
        Call this first when you do not know which account the operator means."""
        def go():
            rows = backend.list_ad_accounts()
            guard.log("list_ad_accounts", None, {"count": len(rows)})
            return rows
        return run(go)

    @beta_tool
    def list_campaigns(account_id: str, include_archived: bool = False, limit: int = 50) -> str:
        """List campaigns in an ad account with status, objective and budgets.

        Args:
            account_id: Ad account id, e.g. "act_123" (digits alone are accepted).
            include_archived: Also return ARCHIVED/DELETED campaigns. Default False.
            limit: Maximum rows to return (1-200).
        """
        def go():
            acct = normalise_account_id(account_id)
            if (why := guard.check_read(acct)):
                return {"error": why}
            rows = backend.list_campaigns(acct, include_archived, max(1, min(limit, 200)))
            guard.log("list_campaigns", acct, {"count": len(rows)})
            return rows
        return run(go)

    @beta_tool
    def list_adsets(campaign_id: str, limit: int = 50) -> str:
        """List the ad sets of a campaign with budgets, optimisation goal and targeting.

        Args:
            campaign_id: Campaign id as returned by list_campaigns.
            limit: Maximum rows to return (1-200).
        """
        def go():
            acct = backend.owner_account("campaign", campaign_id)
            if (why := guard.check_read(acct)):
                return {"error": why}
            rows = backend.list_adsets(campaign_id, max(1, min(limit, 200)))
            guard.log("list_adsets", acct, {"campaign_id": campaign_id, "count": len(rows)})
            return rows
        return run(go)

    @beta_tool
    def list_ads(adset_id: str, limit: int = 50) -> str:
        """List the ads inside an ad set.

        Args:
            adset_id: Ad set id as returned by list_adsets.
            limit: Maximum rows to return (1-200).
        """
        def go():
            acct = backend.owner_account("adset", adset_id)
            if (why := guard.check_read(acct)):
                return {"error": why}
            rows = backend.list_ads(adset_id, max(1, min(limit, 200)))
            guard.log("list_ads", acct, {"adset_id": adset_id, "count": len(rows)})
            return rows
        return run(go)

    @beta_tool
    def get_insights(
        object_type: str,
        object_id: str,
        date_preset: str = "last_7d",
        daily: bool = False,
        breakdowns: list[str] | None = None,
        fields: list[str] | None = None,
    ) -> str:
        """Performance metrics (spend, impressions, reach, clicks, ctr, cpc, cpm,
        frequency, actions, cost per action) for an account, campaign, ad set or ad.
        Money is in the account currency; budgets elsewhere are in minor units (agorot/cents).

        Args:
            object_type: One of "account", "campaign", "adset", "ad".
            object_id: The object's id ("act_..." for accounts).
            date_preset: One of today, yesterday, last_3d, last_7d, last_14d, last_28d,
                last_30d, this_month, last_month, maximum.
            daily: If true, return one row per day instead of one total row.
            breakdowns: Optional Meta breakdowns, e.g. ["age"], ["gender"], ["publisher_platform"],
                ["country"]. Leave empty for totals.
            fields: Optional list of insight fields to override the default set.
        """
        def go():
            if object_type not in OBJECT_TYPES:
                return {"error": f"object_type must be one of {OBJECT_TYPES}"}
            if date_preset not in DATE_PRESETS:
                return {"error": f"date_preset must be one of {DATE_PRESETS}"}
            oid = normalise_account_id(object_id) if object_type == "account" else object_id
            acct = backend.owner_account(object_type, oid)
            if (why := guard.check_read(acct)):
                return {"error": why}
            params: dict[str, Any] = {"date_preset": date_preset,
                                      "fields": fields or DEFAULT_INSIGHT_FIELDS}
            if daily:
                params["time_increment"] = "1"
            if breakdowns:
                params["breakdowns"] = breakdowns
            rows = backend.get_insights(object_type, oid, params)
            guard.log("get_insights", acct, {"object_type": object_type, "object_id": oid,
                                             "date_preset": date_preset, "rows": len(rows)})
            return rows
        return run(go)

    # ----------------------------------------------------------- writes

    @beta_tool
    def create_campaign(
        account_id: str,
        name: str,
        objective: str,
        daily_budget_minor: int | None = None,
        lifetime_budget_minor: int | None = None,
        special_ad_categories: list[str] | None = None,
    ) -> str:
        """Create a campaign. It is always created PAUSED; the operator activates it
        separately with set_status. Requires operator approval and is only allowed on
        the sandbox account. Give either a daily or a lifetime budget (minor units).

        Args:
            account_id: Ad account id ("act_...").
            name: Campaign name.
            objective: One of OUTCOME_TRAFFIC, OUTCOME_LEADS, OUTCOME_SALES,
                OUTCOME_AWARENESS, OUTCOME_ENGAGEMENT, OUTCOME_APP_PROMOTION.
            daily_budget_minor: Daily budget in minor currency units (e.g. 5000 = 50.00).
            lifetime_budget_minor: Lifetime budget in minor currency units.
            special_ad_categories: e.g. ["HOUSING"], ["CREDIT"], ["EMPLOYMENT"]. Empty list if none.
        """
        def go():
            if objective not in OBJECTIVES:
                return {"error": f"objective must be one of {OBJECTIVES}"}
            if bool(daily_budget_minor) == bool(lifetime_budget_minor):
                return {"error": "Give exactly one of daily_budget_minor or lifetime_budget_minor."}
            acct = normalise_account_id(account_id)
            params: dict[str, Any] = {
                "name": name, "objective": objective, "status": "PAUSED",
                "special_ad_categories": special_ad_categories or [],
                # Without this Meta defaults to LOWEST_COST_WITH_BID_CAP, and every ad set
                # then fails with "Bid amount required" (seen live 2026-09-05).
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            }
            if daily_budget_minor:
                params["daily_budget"] = int(daily_budget_minor)
            else:
                params["lifetime_budget"] = int(lifetime_budget_minor)
            if (why := guard.authorize_write("create_campaign", acct, params)):
                return {"error": why}
            created = backend.create_campaign(acct, params)
            if created.get("id"):
                note_campaign(str(created["id"]))
            return created
        return run(go)

    @beta_tool
    def create_adset(
        account_id: str,
        campaign_id: str,
        name: str,
        optimization_goal: str,
        billing_event: str,
        countries: list[str],
        daily_budget_minor: int | None = None,
        age_min: int = 18,
        age_max: int = 65,
        advantage_audience: bool = False,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> str:
        """Create an ad set (PAUSED) inside a campaign. Requires operator approval and
        is only allowed on the sandbox account. Bidding is inherited from the campaign.

        Args:
            account_id: Ad account id ("act_...").
            campaign_id: Parent campaign id.
            name: Ad set name.
            optimization_goal: e.g. LINK_CLICKS, LANDING_PAGE_VIEWS, LEAD_GENERATION,
                OFFSITE_CONVERSIONS, REACH, IMPRESSIONS.
            billing_event: Usually IMPRESSIONS; LINK_CLICKS is also possible for some goals.
            countries: ISO country codes to target, e.g. ["IL"].
            daily_budget_minor: Daily budget in minor units. LEAVE EMPTY when the campaign
                already has its own budget (campaigns made by create_campaign always do);
                Meta rejects a budget on both levels.
            age_min: Minimum age (13-65).
            age_max: Maximum age (13-65).
            advantage_audience: True lets Meta widen the audience beyond the targeting
                given (Advantage+ audience). False keeps targeting exactly as specified.
            start_time: Optional ISO-8601 start.
            end_time: Optional ISO-8601 end.
        """
        def go():
            acct = normalise_account_id(account_id)
            if not countries:
                return {"error": "countries must contain at least one ISO code."}
            if not (13 <= age_min <= age_max <= 65):
                return {"error": "ages must satisfy 13 <= age_min <= age_max <= 65."}
            params: dict[str, Any] = {
                "name": name, "campaign_id": campaign_id, "status": "PAUSED",
                "optimization_goal": optimization_goal, "billing_event": billing_event,
                "targeting": {
                    "geo_locations": {"countries": countries},
                    "age_min": age_min, "age_max": age_max,
                    # Mandatory since 2024: Meta refuses ad sets that do not state it.
                    "targeting_automation": {"advantage_audience": 1 if advantage_audience else 0},
                },
            }
            if daily_budget_minor:
                params["daily_budget"] = int(daily_budget_minor)
            if start_time:
                params["start_time"] = start_time
            if end_time:
                params["end_time"] = end_time
            if (why := guard.authorize_write("create_adset", acct, params)):
                return {"error": why}
            return backend.create_adset(acct, params)
        return run(go)

    @beta_tool
    def set_status(object_type: str, object_id: str, status: str) -> str:
        """Start, stop (pause) or archive a campaign, ad set or ad. PAUSED is the normal
        way to stop delivery and is reversible; ARCHIVED is not. Requires operator
        approval and is only allowed on the sandbox account.

        Args:
            object_type: "campaign", "adset" or "ad".
            object_id: The object's id.
            status: ACTIVE, PAUSED or ARCHIVED.
        """
        def go():
            if object_type not in ("campaign", "adset", "ad"):
                return {"error": 'object_type must be "campaign", "adset" or "ad".'}
            if status not in STATUSES:
                return {"error": f"status must be one of {STATUSES}"}
            acct = backend.owner_account(object_type, object_id)
            details = {"object_type": object_type, "object_id": object_id, "status": status}
            if (why := guard.authorize_write("set_status", acct, details)):
                return {"error": why}
            return backend.update_object(object_type, object_id, {"status": status})
        return run(go)

    @beta_tool
    def set_budget(object_type: str, object_id: str, daily_budget_minor: int | None = None,
                   lifetime_budget_minor: int | None = None) -> str:
        """Change the daily or lifetime budget of a campaign or ad set (minor currency
        units). Requires operator approval and is only allowed on the sandbox account.

        Args:
            object_type: "campaign" or "adset".
            object_id: The object's id.
            daily_budget_minor: New daily budget in minor units.
            lifetime_budget_minor: New lifetime budget in minor units.
        """
        def go():
            if object_type not in ("campaign", "adset"):
                return {"error": 'object_type must be "campaign" or "adset".'}
            if bool(daily_budget_minor) == bool(lifetime_budget_minor):
                return {"error": "Give exactly one of daily_budget_minor or lifetime_budget_minor."}
            acct = backend.owner_account(object_type, object_id)
            params: dict[str, Any] = {"object_type": object_type, "object_id": object_id}
            if daily_budget_minor:
                params["daily_budget"] = int(daily_budget_minor)
            else:
                params["lifetime_budget"] = int(lifetime_budget_minor)
            if (why := guard.authorize_write("set_budget", acct, params)):
                return {"error": why}
            update = {k: v for k, v in params.items() if k.endswith("_budget")}
            return backend.update_object(object_type, object_id, update)
        return run(go)

    return [list_ad_accounts, list_campaigns, list_adsets, list_ads, get_insights,
            create_campaign, create_adset, set_status, set_budget]
