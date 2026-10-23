"""Media hosting. Instagram fetches media from a public URL, so local files
are uploaded to an S3-compatible bucket (Cloudflare R2) first.

Only files inside SOCIAL_MEDIA_DIR may be uploaded: the path comes from the
model or the browser and is untrusted.
"""

from __future__ import annotations

import mimetypes
import uuid
from datetime import date
from pathlib import Path

from .config import Settings


class MediaError(Exception):
    pass


def confine(settings: Settings, path_str: str) -> Path:
    """Resolve a user/model supplied path and make sure it stays in media_dir."""
    root = settings.media_dir.resolve()
    p = (root / path_str).resolve() if not Path(path_str).is_absolute() else Path(path_str).resolve()
    if not p.is_relative_to(root):
        raise MediaError(f"Only files under {root} can be used as media.")
    if not p.is_file():
        raise MediaError(f"No such file: {p}")
    return p


class R2MediaStore:
    def __init__(self, settings: Settings) -> None:
        import boto3

        self.settings = settings
        self.client = boto3.client(
            "s3", endpoint_url=settings.r2_endpoint, region_name="auto",
            aws_access_key_id=settings.r2_access_key, aws_secret_access_key=settings.r2_secret_key,
        )

    def upload(self, local: Path) -> str:
        key = f"{date.today():%Y/%m}/{uuid.uuid4().hex[:12]}{local.suffix.lower()}"
        ctype = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        with local.open("rb") as fh:
            self.client.put_object(Bucket=self.settings.r2_bucket, Key=key, Body=fh, ContentType=ctype)
        return f"{self.settings.r2_public_base}/{key}"


class FakeMediaStore:
    """Pretends to host files. Remembers url -> local path so previews work."""

    def __init__(self) -> None:
        self.files: dict[str, Path] = {}

    def upload(self, local: Path) -> str:
        url = f"https://media.fake.local/{uuid.uuid4().hex[:8]}/{local.name}"
        self.files[url] = local
        return url

    def local_for(self, url: str) -> Path | None:
        return self.files.get(url)


class NoMediaStore:
    def upload(self, local: Path) -> str:
        raise MediaError("Media hosting is not configured (R2_* in .env, see SETUP.md). "
                         "Use a public URL instead, or a local file for Facebook only.")


def make_media_store(settings: Settings):
    if settings.media_configured:
        return R2MediaStore(settings)
    return FakeMediaStore() if settings.dry_run else NoMediaStore()


def resolve_media(settings: Settings, store, items: list[str] | None, need_urls: bool) -> list[str]:
    """Turn a mixed list of URLs and local paths into what the channel wants.
    With need_urls (Instagram) every local file is uploaded; otherwise local
    files under media_dir are kept as paths (Facebook uploads them directly)."""
    out: list[str] = []
    for item in items or []:
        item = item.strip()
        if not item:
            continue
        if item.startswith(("http://", "https://")):
            out.append(item)
            continue
        local = confine(settings, item)
        out.append(store.upload(local) if need_urls else str(local))
    return out
