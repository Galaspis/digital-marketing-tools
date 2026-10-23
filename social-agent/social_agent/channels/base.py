from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class ChannelError(Exception):
    """A platform error, flattened to one readable message."""


@dataclass
class Target:
    """Somewhere a post can go: a Facebook Page, an Instagram account, later a
    LinkedIn page or a TikTok account."""

    id: str
    name: str
    type: str            # facebook_page | instagram | ...
    channel: str         # meta | linkedin | tiktok
    extra: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "type": self.type, "channel": self.channel,
                **{k: v for k, v in self.extra.items() if k != "access_token"}}


class Channel(Protocol):
    name: str

    def list_targets(self) -> list[Target]: ...

    def publish(self, target: Target, post: dict[str, Any]) -> dict[str, Any]:
        """Publish now. Returns {"remote_id": ..., "permalink": ...}."""
        ...

    def schedule(self, target: Target, post: dict[str, Any], when: datetime) -> dict[str, Any] | None:
        """Ask the platform to publish at `when`. Return {"remote_id": ...} if it
        accepted, or None if this target cannot schedule natively (our local
        scheduler then takes over)."""
        ...

    def cancel(self, target: Target, remote_id: str) -> None:
        """Delete a natively scheduled post."""
        ...

    def status(self, target: Target, remote_id: str) -> dict[str, Any]:
        """{"published": bool, "permalink": str | None}."""
        ...
