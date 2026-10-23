"""State transitions that touch the platform. Used by the Claude tools, the
UI endpoints and the scheduler, so every path behaves the same. Policy
(allowlist, cap, approval) is the caller's job via Guard; this module only
executes and records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .channels.base import ChannelError
from .store import Store, now_iso, to_utc_iso

MIN_LEAD = timedelta(minutes=10)
MAX_LEAD_FACEBOOK = timedelta(days=30)


class PublishError(Exception):
    pass


class Publisher:
    def __init__(self, channel, store: Store) -> None:
        self.channel = channel
        self.store = store

    def _post(self, post_id: str) -> dict[str, Any]:
        post = self.store.get(post_id)
        if post is None:
            raise PublishError(f"Post {post_id} not found.")
        return post

    def publish_now(self, post_id: str, source: str | None = None) -> dict[str, Any]:
        post = self._post(post_id)
        if post["status"] not in ("draft", "scheduled", "failed"):
            raise PublishError(f"Post {post_id} is {post['status']}; only drafts, scheduled or failed posts can be published.")
        target = self.channel.target(post["target_id"])
        if post["status"] == "scheduled" and post.get("scheduler") == "native" and post.get("remote_id"):
            # Was sitting in the platform's queue: pull it out first, then post fresh.
            try:
                self.channel.cancel(target, post["remote_id"])
            except ChannelError:
                pass
        self.store.update(post_id, status="publishing", error=None)
        try:
            result = self.channel.publish(target, post)
        except ChannelError as e:
            return self.store.update(post_id, status="failed", error=str(e))
        return self.store.update(post_id, status="published", remote_id=result.get("remote_id"),
                                 permalink=result.get("permalink"), published_at=now_iso(),
                                 error=None, **({"source": source} if source else {}))

    def schedule(self, post_id: str, when_iso: str | None = None) -> dict[str, Any]:
        post = self._post(post_id)
        if post["status"] not in ("draft", "failed"):
            raise PublishError(f"Post {post_id} is {post['status']}; only drafts can be scheduled.")
        when_iso = to_utc_iso(when_iso or post.get("scheduled_at") or "")
        when = datetime.fromisoformat(when_iso)
        now = datetime.now(timezone.utc)
        if when < now + MIN_LEAD:
            raise PublishError("Schedule at least 10 minutes ahead.")
        target = self.channel.target(post["target_id"])
        if target.type == "facebook_page" and when > now + MAX_LEAD_FACEBOOK:
            raise PublishError("Facebook allows scheduling at most 30 days ahead.")
        try:
            native = self.channel.schedule(target, post, when)
        except ChannelError as e:
            return self.store.update(post_id, status="failed", error=str(e), scheduled_at=when_iso)
        if native:
            return self.store.update(post_id, status="scheduled", scheduler="native", scheduled_at=when_iso,
                                     remote_id=native.get("remote_id"), error=None)
        return self.store.update(post_id, status="scheduled", scheduler="local", scheduled_at=when_iso, error=None)

    def cancel(self, post_id: str) -> dict[str, Any]:
        post = self._post(post_id)
        if post["status"] != "scheduled":
            raise PublishError(f"Post {post_id} is {post['status']}; only scheduled posts can be cancelled.")
        if post.get("scheduler") == "native" and post.get("remote_id"):
            self.channel.cancel(self.channel.target(post["target_id"]), post["remote_id"])
        return self.store.update(post_id, status="cancelled", remote_id=None)

    def refresh_native(self) -> int:
        """Mark platform-scheduled posts as published once the platform says so."""
        n = 0
        for post in self.store.native_pending():
            try:
                st = self.channel.status(self.channel.target(post["target_id"]), post["remote_id"])
            except ChannelError as e:
                self.store.update(post["id"], error=str(e))
                continue
            if st.get("published"):
                self.store.update(post["id"], status="published", published_at=now_iso(),
                                  permalink=st.get("permalink") or post.get("permalink"), error=None)
                n += 1
        return n
