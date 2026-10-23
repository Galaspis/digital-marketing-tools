"""Free connectivity check for the Social Publisher:

    social-agent\\.venv\\Scripts\\python -m social_agent.check_setup   (from social-agent/)

Talks only to Meta's Graph API (no Claude, no OpenAI). Tells you whether the
token works, which permissions it carries, which Pages and Instagram accounts
it can see, and what to put in SOCIAL_PUBLISH_TARGETS.
"""

from __future__ import annotations

import sys

from .channels.base import ChannelError
from .channels.meta import LiveMetaChannel, MetaGraph
from .config import load_settings

NEEDED = ["pages_show_list", "pages_read_engagement", "pages_manage_posts", "pages_manage_metadata",
          "instagram_basic", "instagram_content_publish", "business_management"]


def main() -> int:
    s = load_settings()
    if s.dry_run:
        print("SOCIAL_META_USER_TOKEN is empty in the root .env, so the publisher runs in DRY RUN.")
        print("Follow social-agent/GUIDE-connect-facebook-instagram.md, paste the token, then run this again.")
        return 1
    graph = MetaGraph(s.graph_version)

    try:
        me = graph.call("GET", "me", s.meta_user_token, {"fields": "id,name"})
    except ChannelError as e:
        print(f"Token rejected:\n  {e}\n  (expired token = error 190; regenerate it, see the guide, part E)")
        return 1
    print(f"Token OK. Acting as: {me.get('name', '?')} (id {me.get('id')})")

    try:
        perms = graph.call("GET", "me/permissions", s.meta_user_token)
        granted = {p["permission"] for p in perms.get("data", []) if p.get("status") == "granted"}
        print("Permissions:")
        missing = [n for n in NEEDED if n not in granted]
        for n in NEEDED:
            print(f"  {'ok     ' if n in granted else 'MISSING'}  {n}")
        if missing:
            print("  -> add the missing ones in the app's use cases (guide, part D), then generate a NEW token (part E).")
    except ChannelError as e:
        print(f"Could not list permissions ({e}); continuing.")

    try:
        targets = LiveMetaChannel(s, graph).list_targets()
    except ChannelError as e:
        print(f"Could not list Pages: {e}")
        return 1
    if not targets:
        print("\nThe token sees NO Pages. Usually: the system user was not assigned the Page (guide, part E, step 5),")
        print("or the Page is not in your business portfolio (part B).")
        return 1
    print(f"\n{len(targets)} target(s) visible:")
    for t in targets:
        tag = "PUBLISH allowed" if t.id in s.publish_targets else "draft only (not in SOCIAL_PUBLISH_TARGETS)"
        print(f"  {t.type:14} {t.id:20} {t.name:35} {tag}")
    if not any(t.type == "instagram" for t in targets):
        print("\nNo Instagram account is connected to any visible Page (guide, part C).")
    ids = ",".join(t.id for t in targets)
    if not s.publish_targets:
        print(f"\nTo allow publishing to all of them, set in the root .env:\n  SOCIAL_PUBLISH_TARGETS={ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
