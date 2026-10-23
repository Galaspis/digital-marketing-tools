"""Claude-callable tools. Drafting is free (database only). Publishing,
scheduling and cancelling go through the Guard: allowlisted target, daily
cap, operator approval. Errors come back as text so Claude can explain."""

from __future__ import annotations

import json
from typing import Any

from anthropic import beta_tool

from agentkit import PriceBook, current_turn, note_post

from .channels.base import ChannelError
from .config import Settings
from .guard import Guard
from .images import QUALITIES, SIZES, ImageError, images_in_last_day
from .media import MediaError, confine, resolve_media
from .publisher import Publisher, PublishError
from .store import POST_KINDS, STATUSES, Store, to_utc_iso


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _summary(post: dict[str, Any]) -> list[str]:
    text = (post.get("text") or "").strip()
    lines = [f"to: {post.get('target_name')} ({post.get('target_type')})", f"kind: {post.get('kind')}"]
    if text:
        lines.append("text: " + (text if len(text) <= 300 else text[:300] + "…"))
    if post.get("link"):
        lines.append(f"link: {post['link']}")
    if post.get("media"):
        lines.append(f"media: {len(post['media'])} item(s)")
    if post.get("scheduled_at"):
        lines.append(f"when: {post['scheduled_at']} (UTC)")
    return lines


def build_tools(channel, store: Store, guard: Guard, media_store, settings: Settings, publisher: Publisher,
                image_generator=None, ledger=None) -> list:
    book = PriceBook()

    def attach(post_id: str, media_items: list[str] | None) -> None:
        """Tell the turn context and the ledger which post this work belongs to."""
        note_post(post_id)
        if ledger is None:
            return
        keys = list(media_items or [])
        local_for = getattr(media_store, "local_for", None)
        for item in media_items or []:
            p = local_for(item) if local_for else None
            if p is not None:
                keys += ["generated/" + p.name, "uploads/" + p.name, p.as_posix()]
        ctx = current_turn()
        ledger.attach_post(post_id, ref_ids=keys, turn_id=ctx["turn_id"] if ctx else None)

    def run(fn):
        try:
            return _json(fn())
        except (ChannelError, PublishError, MediaError, ImageError) as e:
            return _json({"error": str(e)})
        except (KeyError, ValueError, TypeError) as e:
            return _json({"error": f"Bad input: {e}"})

    def prepared(post_id: str) -> dict[str, Any]:
        post = store.get(post_id)
        if post is None:
            raise PublishError(f"Post {post_id} not found.")
        return post

    # ------------------------------------------------------------ reads

    @beta_tool
    def list_targets() -> str:
        """List the places posts can go (Facebook Pages and Instagram accounts the
        token can see), with id, name and type. Only targets in the allowlist can be
        published to; drafts can be made for any."""
        def go():
            allowed = set(settings.publish_targets)
            return [{**t.public(), "publish_allowed": t.id in allowed} for t in channel.list_targets()]
        return run(go)

    @beta_tool
    def list_posts(status: str | None = None, target_id: str | None = None, limit: int = 30) -> str:
        """List posts in the local calendar, newest first.

        Args:
            status: Optional filter: draft, scheduled, publishing, published, failed, cancelled.
            target_id: Optional filter by target id.
            limit: Max rows (1-200).
        """
        def go():
            if status and status not in STATUSES:
                return {"error": f"status must be one of {STATUSES}"}
            return store.list(status, target_id, max(1, min(limit, 200)))
        return run(go)

    @beta_tool
    def get_post(post_id: str) -> str:
        """Fetch one post with its full text, media, status, schedule and permalink.

        Args:
            post_id: The post id from list_posts or create_draft.
        """
        return run(lambda: prepared(post_id))

    # ------------------------------------------------------------ drafting (free)

    @beta_tool
    def create_draft(
        target_id: str,
        kind: str,
        text: str = "",
        media: list[str] | None = None,
        link: str | None = None,
        scheduled_at: str | None = None,
    ) -> str:
        """Save a new draft post. Free: nothing is published. Write the caption
        yourself in `text` (in the operator's language; hashtags only if they fit).

        Args:
            target_id: Where it goes (from list_targets).
            kind: text | link | photo | video | reel | story | carousel.
                Facebook Page: text, link, photo, video. Instagram: photo, reel, story,
                carousel (all need media; text-only is impossible on Instagram).
            text: The post text / caption.
            media: Public URLs, or file names inside the media folder (local files are
                uploaded to hosting when the target is Instagram).
            link: Optional link for a Facebook link post.
            scheduled_at: Optional intended time, ISO-8601 (e.g. 2026-09-10T18:00). Local
                time if no offset. Only stored; schedule_post actually books it.
        """
        def go():
            if kind not in POST_KINDS:
                return {"error": f"kind must be one of {POST_KINDS}"}
            target = channel.target(target_id)
            if target.type == "instagram" and kind in ("text", "link"):
                return {"error": "Instagram has no text-only posts. Use photo, reel, story or carousel with media."}
            if target.type == "instagram" and not media:
                return {"error": "Instagram posts need at least one image or video in `media`."}
            resolved = resolve_media(settings, media_store, media, need_urls=(target.type == "instagram"))
            post = store.create(
                target_id=target.id, target_name=target.name, target_type=target.type, channel=channel.name,
                kind=kind, text=text, link=link, media=resolved,
                scheduled_at=to_utc_iso(scheduled_at) if scheduled_at else None, source="agent",
            )
            attach(post["id"], [*(media or []), *resolved])
            return post
        return run(go)

    @beta_tool
    def update_draft(post_id: str, text: str | None = None, media: list[str] | None = None,
                     link: str | None = None, kind: str | None = None, scheduled_at: str | None = None) -> str:
        """Edit a draft (only drafts and failed posts can be edited). Pass only the
        fields to change.

        Args:
            post_id: The draft's id.
            text: New text / caption.
            media: New full media list (URLs or media-folder file names).
            link: New link.
            kind: New kind.
            scheduled_at: New intended time (ISO-8601).
        """
        def go():
            post = prepared(post_id)
            if post["status"] not in ("draft", "failed"):
                return {"error": f"Post is {post['status']}; only drafts can be edited. Cancel a scheduled post first."}
            fields: dict[str, Any] = {}
            if text is not None:
                fields["text"] = text
            if link is not None:
                fields["link"] = link
            if kind is not None:
                if kind not in POST_KINDS:
                    return {"error": f"kind must be one of {POST_KINDS}"}
                fields["kind"] = kind
            if media is not None:
                fields["media"] = resolve_media(settings, media_store, media,
                                                need_urls=(post["target_type"] == "instagram"))
            if scheduled_at is not None:
                fields["scheduled_at"] = to_utc_iso(scheduled_at) if scheduled_at else None
            fields["status"] = "draft"
            fields["error"] = None
            updated = store.update(post_id, **fields)
            attach(post_id, [*(media or []), *fields.get("media", [])])
            return updated
        return run(go)

    @beta_tool
    def delete_draft(post_id: str) -> str:
        """Delete a draft, failed or cancelled post from the calendar. Published and
        scheduled posts cannot be deleted this way.

        Args:
            post_id: The post id.
        """
        def go():
            post = prepared(post_id)
            if post["status"] not in ("draft", "failed", "cancelled"):
                return {"error": f"Post is {post['status']}; cancel it first."}
            store.delete(post_id)
            guard.log("delete_draft", post["target_id"], {"post_id": post_id})
            return {"deleted": post_id}
        return run(go)

    @beta_tool
    def upload_media(file_name: str) -> str:
        """Upload a file from the media folder to public hosting and return its URL.
        Needed for Instagram; Facebook can take local files directly.

        Args:
            file_name: File name (or relative path) inside the media folder.
        """
        def go():
            local = confine(settings, file_name)
            url = media_store.upload(local)
            guard.log("upload_media", None, {"file": local.name, "url": url})
            return {"url": url}
        return run(go)

    @beta_tool
    def generate_image(prompt: str, size: str = "1024x1024", quality: str = "medium") -> str:
        """Generate an image with the image model and save it to the media folder.
        Returns the file name to use in `media` for create_draft / update_draft (it is
        uploaded to hosting automatically when the target is Instagram). Costs money per
        image (roughly a few cents at medium quality); there is a daily cap. Write a
        concrete visual prompt: subject, setting, style, mood, colours, no text unless
        the operator wants text in the image.

        Args:
            prompt: What the image should show, in English, detailed.
            size: 1024x1024 (square, feed), 1536x1024 (landscape), 1024x1536 (portrait,
                stories/reels cover), 2048x2048.
            quality: low (draft), medium (default), high (final, ~4x the cost).
        """
        def go():
            if image_generator is None:
                return {"error": "Image generation is not available in this configuration."}
            n = images_in_last_day(guard)
            if n >= settings.max_images_per_day:
                return {"error": f"Daily image cap reached ({n}/{settings.max_images_per_day}). Raise SOCIAL_MAX_IMAGES_PER_DAY or wait."}
            result = image_generator.generate(prompt, size, quality)
            rel = result["path"].relative_to(settings.media_dir).as_posix()
            u = result.get("usage") or {}
            usd = book.image_cost(f"openai/{settings.image_model}", u) or (result.get("cost_usd") or 0)
            guard.log("generate_image", None, {"prompt": prompt[:200], "size": size, "quality": quality,
                                                "model": result["model"], "file": rel, "usage": u, "cost_usd": usd})
            if ledger is not None:
                ctx = current_turn()
                ledger.record(service="social", action="generate_image", provider="openai", model=settings.image_model,
                              usd=usd, client=settings.client, input_tokens=u.get("input_tokens", 0),
                              output_tokens=u.get("output_tokens", 0), images=1, ref_type="image", ref_id=rel,
                              turn_id=ctx["turn_id"] if ctx else None, note=f"{size} {quality} · {prompt[:80]}")
            return {"file": rel, "model": result["model"], "size": size, "quality": quality,
                    "estimated_cost_usd": usd, "revised_prompt": result.get("revised_prompt"),
                    "note": "Pass this file name in `media` when creating or updating the draft."}
        return run(go)

    # ------------------------------------------------------------ platform actions (approval)

    @beta_tool
    def publish_post(post_id: str) -> str:
        """Publish a draft NOW to its target. Requires operator approval; the target
        must be allowlisted and the daily cap not exceeded. Show the operator the
        final text before calling this.

        Args:
            post_id: The draft's id.
        """
        def go():
            post = prepared(post_id)
            details = {"post_id": post_id, "kind": post["kind"], "target": post["target_name"]}
            if (why := guard.authorize("publish", post["target_id"], details, _summary(post))):
                return {"error": why}
            return publisher.publish_now(post_id, source="agent")
        return run(go)

    @beta_tool
    def schedule_post(post_id: str, scheduled_at: str | None = None) -> str:
        """Book a draft for later. Requires operator approval. Facebook keeps the post
        in its own queue (10 minutes to 30 days ahead); Instagram cannot schedule
        natively, so our server publishes it at the time (the hub must be running).

        Args:
            post_id: The draft's id.
            scheduled_at: ISO-8601 time; local time if no offset. Defaults to the
                draft's stored scheduled_at.
        """
        def go():
            post = prepared(post_id)
            when = scheduled_at or post.get("scheduled_at")
            if not when:
                return {"error": "Give scheduled_at (ISO-8601)."}
            when_iso = to_utc_iso(when)
            details = {"post_id": post_id, "kind": post["kind"], "target": post["target_name"], "when_utc": when_iso}
            if (why := guard.authorize("schedule", post["target_id"], details, _summary({**post, "scheduled_at": when_iso}))):
                return {"error": why}
            return publisher.schedule(post_id, when_iso)
        return run(go)

    @beta_tool
    def cancel_post(post_id: str) -> str:
        """Take a scheduled post out of the queue (it becomes cancelled; edit and
        reschedule if needed). Requires operator approval when the platform holds it.

        Args:
            post_id: The scheduled post's id.
        """
        def go():
            post = prepared(post_id)
            details = {"post_id": post_id, "target": post["target_name"], "scheduler": post.get("scheduler")}
            if post.get("scheduler") == "native":
                if (why := guard.authorize("cancel", post["target_id"], details)):
                    return {"error": why}
            else:
                guard.log("cancel", post["target_id"], details, approved=True, note="local schedule, no platform call")
            return publisher.cancel(post_id)
        return run(go)

    return [list_targets, list_posts, get_post, create_draft, update_draft, delete_draft,
            upload_media, generate_image, publish_post, schedule_post, cancel_post]
