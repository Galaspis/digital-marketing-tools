"""Meta channel: Facebook Pages + Instagram professional accounts.

Live: the Graph API over HTTPS (`requests`). One user/system-user token
with Pages + Instagram permissions; Page tokens are fetched from
/me/accounts and cached. Instagram publishes through the container flow
and needs media at a public URL. Facebook accepts a URL or a local file.

Fake: in-memory Page + Instagram account so the whole agent runs before a
Page exists.

Verified against developers.facebook.com on 2026-09-06:
  * Page posts: POST /{page}/feed (message, link), /{page}/photos (url|source),
    /{page}/videos (file_url|source). Scheduling: published=false +
    scheduled_publish_time, 10 minutes to 30 days ahead.
  * Instagram: POST /{ig}/media (image_url | video_url, caption, media_type
    IMAGE default | REELS | STORIES | CAROUSEL) -> container; poll
    status_code; POST /{ig}/media_publish creation_id. 100 API posts / 24 h.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import FAKE_IG, FAKE_PAGE, Settings
from .base import ChannelError, Target

RATE_LIMIT_CODES = {4, 17, 32, 613, 80001, 80002, 80003, 80004, 80005, 80006}


class MetaGraph:
    """Thin Graph API caller with rate-limit retries and readable errors."""

    def __init__(self, version: str, retry_wait: float = 31.0, max_attempts: int = 4) -> None:
        import requests

        self.base = f"https://graph.facebook.com/{version}"
        self.http = requests.Session()
        self.retry_wait = retry_wait
        self.max_attempts = max_attempts

    def call(self, method: str, path: str, token: str, params: dict[str, Any] | None = None,
             files: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        params["access_token"] = token
        attempt = 0
        while True:
            attempt += 1
            if method == "GET":
                r = self.http.get(f"{self.base}/{path}", params=params, timeout=120)
            elif method == "DELETE":
                r = self.http.delete(f"{self.base}/{path}", params=params, timeout=120)
            else:
                r = self.http.post(f"{self.base}/{path}", data=params, files=files, timeout=600)
            try:
                body = r.json()
            except ValueError:
                body = {"error": {"message": r.text[:300], "code": r.status_code}}
            err = body.get("error") if isinstance(body, dict) else None
            if err is None and r.ok:
                return body
            err = err or {"message": r.text[:300], "code": r.status_code}
            if err.get("code") in RATE_LIMIT_CODES and attempt < self.max_attempts:
                time.sleep(self.retry_wait)
                continue
            parts = [f"Meta API error {err.get('code')}"]
            if err.get("error_subcode"):
                parts.append(f"subcode {err['error_subcode']}")
            parts.append(f"({err.get('type', 'error')}): {err.get('message')}")
            for k in ("error_user_title", "error_user_msg"):
                if err.get(k):
                    parts.append(f"| {err[k]}")
            raise ChannelError(" ".join(parts))


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


class LiveMetaChannel:
    name = "meta"

    def __init__(self, settings: Settings, graph: MetaGraph | None = None) -> None:
        self.settings = settings
        self.graph = graph or MetaGraph(settings.graph_version)
        self._targets: dict[str, Target] | None = None

    # -- targets -----------------------------------------------------------

    def list_targets(self) -> list[Target]:
        data = self.graph.call("GET", "me/accounts", self.settings.meta_user_token, {
            "fields": "id,name,access_token,instagram_business_account{id,username,name}",
            "limit": 100,
        })
        targets: list[Target] = []
        for page in data.get("data", []):
            targets.append(Target(page["id"], page["name"], "facebook_page", "meta",
                                  {"access_token": page["access_token"]}))
            ig = page.get("instagram_business_account")
            if ig:
                targets.append(Target(ig["id"], f"@{ig.get('username', ig['id'])}", "instagram", "meta",
                                      {"access_token": page["access_token"], "page_id": page["id"],
                                       "page_name": page["name"]}))
        self._targets = {t.id: t for t in targets}
        return targets

    def target(self, target_id: str) -> Target:
        if self._targets is None or target_id not in self._targets:
            self.list_targets()
        try:
            return self._targets[target_id]  # type: ignore[index]
        except KeyError:
            raise ChannelError(f"Target {target_id} is not a Page or Instagram account this token can see.") from None

    # -- Facebook Page -----------------------------------------------------

    def _fb_post(self, target: Target, post: dict[str, Any], when: datetime | None) -> dict[str, Any]:
        token = target.extra["access_token"]
        sched = {} if when is None else {"published": "false", "scheduled_publish_time": int(when.timestamp())}
        kind, media = post["kind"], post.get("media") or []
        if kind in ("text", "link"):
            params = {"message": post.get("text") or "", **sched}
            if post.get("link"):
                params["link"] = post["link"]
            return self.graph.call("POST", f"{target.id}/feed", token, params)
        if kind == "photo":
            if not media:
                raise ChannelError("A photo post needs one image.")
            params = {"message": post.get("text") or "", **sched}
            if _is_url(media[0]):
                params["url"] = media[0]
                return self.graph.call("POST", f"{target.id}/photos", token, params)
            with open(media[0], "rb") as fh:
                return self.graph.call("POST", f"{target.id}/photos", token, params, files={"source": fh})
        if kind in ("video", "reel"):
            if not media:
                raise ChannelError("A video post needs one video.")
            params = {"description": post.get("text") or "", **sched}
            if _is_url(media[0]):
                params["file_url"] = media[0]
                return self.graph.call("POST", f"{target.id}/videos", token, params)
            with open(media[0], "rb") as fh:
                return self.graph.call("POST", f"{target.id}/videos", token, params, files={"source": fh})
        raise ChannelError(f"Facebook Pages: post kind '{kind}' is not supported in this version "
                           "(stories and carousels come later).")

    # -- Instagram ---------------------------------------------------------

    def _ig_container(self, target: Target, params: dict[str, Any]) -> str:
        data = self.graph.call("POST", f"{target.id}/media", target.extra["access_token"], params)
        return data["id"]

    def _ig_wait(self, target: Target, container_id: str, timeout: float = 300) -> None:
        token = target.extra["access_token"]
        deadline = time.time() + timeout
        while True:
            data = self.graph.call("GET", container_id, token, {"fields": "status_code,status"})
            code = data.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise ChannelError(f"Instagram could not process the media: {data.get('status')}")
            if time.time() > deadline:
                raise ChannelError("Instagram media processing timed out.")
            time.sleep(5)

    def _ig_publish(self, target: Target, post: dict[str, Any]) -> dict[str, Any]:
        kind, media = post["kind"], post.get("media") or []
        caption = post.get("text") or ""
        if not media:
            raise ChannelError("Instagram posts need at least one image or video.")
        for m in media:
            if not _is_url(m):
                raise ChannelError("Instagram only accepts media at a public URL. Upload it first (upload_media).")
        if kind in ("photo", "text", "link"):
            cid = self._ig_container(target, {"image_url": media[0], "caption": caption})
        elif kind in ("video", "reel"):
            cid = self._ig_container(target, {"media_type": "REELS", "video_url": media[0], "caption": caption})
            self._ig_wait(target, cid)
        elif kind == "story":
            is_video = media[0].lower().split("?")[0].endswith((".mp4", ".mov"))
            cid = self._ig_container(target, {"media_type": "STORIES",
                                              ("video_url" if is_video else "image_url"): media[0]})
            if is_video:
                self._ig_wait(target, cid)
        elif kind == "carousel":
            if len(media) < 2:
                raise ChannelError("A carousel needs 2 to 10 items.")
            children = [self._ig_container(target, {"image_url": m, "is_carousel_item": "true"}) for m in media[:10]]
            cid = self._ig_container(target, {"media_type": "CAROUSEL", "caption": caption,
                                              "children": ",".join(children)})
        else:
            raise ChannelError(f"Instagram: post kind '{kind}' is not supported.")
        token = target.extra["access_token"]
        pub = self.graph.call("POST", f"{target.id}/media_publish", token, {"creation_id": cid})
        media_id = pub["id"]
        permalink = None
        try:
            permalink = self.graph.call("GET", media_id, token, {"fields": "permalink"}).get("permalink")
        except ChannelError:
            pass
        return {"remote_id": media_id, "permalink": permalink}

    # -- Channel API -------------------------------------------------------

    def publish(self, target: Target, post: dict[str, Any]) -> dict[str, Any]:
        if target.type == "instagram":
            return self._ig_publish(target, post)
        data = self._fb_post(target, post, None)
        rid = data.get("post_id") or data.get("id")
        return {"remote_id": rid, "permalink": f"https://www.facebook.com/{rid}" if rid else None}

    def schedule(self, target: Target, post: dict[str, Any], when: datetime) -> dict[str, Any] | None:
        if target.type == "instagram":
            return None  # no native scheduling; the local scheduler publishes it
        data = self._fb_post(target, post, when)
        return {"remote_id": data.get("post_id") or data.get("id")}

    def cancel(self, target: Target, remote_id: str) -> None:
        self.graph.call("DELETE", remote_id, target.extra["access_token"])

    def status(self, target: Target, remote_id: str) -> dict[str, Any]:
        token = target.extra["access_token"]
        if target.type == "instagram":
            data = self.graph.call("GET", remote_id, token, {"fields": "permalink"})
            return {"published": True, "permalink": data.get("permalink")}
        data = self.graph.call("GET", remote_id, token, {"fields": "is_published,permalink_url"})
        return {"published": bool(data.get("is_published")), "permalink": data.get("permalink_url")}


class FakeMetaChannel:
    """In-memory Meta. Same behaviour rules as live: Instagram needs public
    media URLs and cannot schedule natively; Facebook can."""

    name = "meta"

    def __init__(self) -> None:
        self.targets = [
            Target(FAKE_PAGE, "Demo Page (fake)", "facebook_page", "meta"),
            Target(FAKE_IG, "@demo.fake", "instagram", "meta", {"page_id": FAKE_PAGE}),
        ]
        self.published: dict[str, dict[str, Any]] = {}
        self.scheduled: dict[str, dict[str, Any]] = {}
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n:04d}"

    def list_targets(self) -> list[Target]:
        return list(self.targets)

    def target(self, target_id: str) -> Target:
        for t in self.targets:
            if t.id == target_id:
                return t
        raise ChannelError(f"Target {target_id} is not a Page or Instagram account this token can see.")

    def _validate(self, target: Target, post: dict[str, Any]) -> None:
        kind, media = post["kind"], post.get("media") or []
        if target.type == "instagram":
            if not media:
                raise ChannelError("Instagram posts need at least one image or video.")
            if any(not _is_url(m) for m in media):
                raise ChannelError("Instagram only accepts media at a public URL. Upload it first (upload_media).")
            if kind == "carousel" and len(media) < 2:
                raise ChannelError("A carousel needs 2 to 10 items.")
        else:
            if kind in ("photo", "video", "reel") and not media:
                raise ChannelError(f"A {kind} post needs media.")
            if kind in ("story", "carousel"):
                raise ChannelError(f"Facebook Pages: post kind '{kind}' is not supported in this version.")
            if kind in ("text", "link") and not (post.get("text") or post.get("link")):
                raise ChannelError("A text post needs some text.")

    def publish(self, target: Target, post: dict[str, Any]) -> dict[str, Any]:
        self._validate(target, post)
        rid = self._id("fb" if target.type == "facebook_page" else "ig")
        self.published[rid] = {**post, "target": target.id, "at": datetime.now(timezone.utc).isoformat()}
        host = "www.facebook.com" if target.type == "facebook_page" else "www.instagram.com/p"
        return {"remote_id": rid, "permalink": f"https://{host}/{rid}"}

    def schedule(self, target: Target, post: dict[str, Any], when: datetime) -> dict[str, Any] | None:
        self._validate(target, post)
        if target.type == "instagram":
            return None
        rid = self._id("fbsched")
        self.scheduled[rid] = {**post, "target": target.id, "when": when.isoformat()}
        return {"remote_id": rid}

    def cancel(self, target: Target, remote_id: str) -> None:
        if remote_id not in self.scheduled:
            raise ChannelError(f"Meta API error 100: {remote_id} is not a scheduled post.")
        del self.scheduled[remote_id]

    def status(self, target: Target, remote_id: str) -> dict[str, Any]:
        if remote_id in self.published:
            return {"published": True, "permalink": f"https://www.facebook.com/{remote_id}"}
        s = self.scheduled.get(remote_id)
        if s and datetime.fromisoformat(s["when"]) <= datetime.now(timezone.utc):
            self.published[remote_id] = self.scheduled.pop(remote_id)
            return {"published": True, "permalink": f"https://www.facebook.com/{remote_id}"}
        return {"published": False, "permalink": None}


def make_channel(settings: Settings):
    return FakeMetaChannel() if settings.dry_run else LiveMetaChannel(settings)
