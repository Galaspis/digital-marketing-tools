"""Free connectivity check: `python -m meta_agent.check_setup`.

Talks only to Meta (no Claude call). Confirms the token works, lists the ad
accounts it can see, and tells you whether the configured write account
(the sandbox) is among them.
"""

from __future__ import annotations

import sys

from .config import load_settings
from .meta_client import LiveMeta, MetaError


def main() -> int:
    s = load_settings()
    if s.dry_run:
        print("META_ACCESS_TOKEN is not set. Copy .env.example to .env and fill it in (see SETUP.md).")
        return 1
    if not (s.meta_app_id and s.meta_app_secret):
        print("META_APP_ID and META_APP_SECRET must both be set.")
        return 1
    try:
        accounts = LiveMeta(s).list_ad_accounts()
    except MetaError as e:
        print(f"Token rejected or missing permissions:\n  {e}")
        return 1
    except Exception as e:  # SDK import/network problems
        print(f"Could not reach Meta: {type(e).__name__}: {e}")
        return 1

    print(f"Token OK. {len(accounts)} ad account(s) visible:")
    for a in accounts:
        tag = "WRITE (sandbox)" if a["id"] in s.write_accounts else "read-only"
        print(f"  {a['id']:<20} {a.get('name', ''):<40} status={a.get('account_status')} {a.get('currency', '')}  {tag}")
    missing = [w for w in s.write_accounts if w not in {a["id"] for a in accounts}]
    if missing:
        print(f"\nWarning: META_WRITE_ACCOUNT_IDS contains {missing} but the token cannot see it.")
    if not s.write_accounts:
        print("\nNo write account configured: the agent is fully read-only. Set META_WRITE_ACCOUNT_IDS "
              "to the sandbox account id to enable creating/pausing there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
