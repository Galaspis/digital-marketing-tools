# Setup (one-time)

Nothing here is needed to try the tool: with the Meta lines empty in the hub's
root `.env` it runs in dry run against a fake Page and Instagram account. Facts below were checked on
developers.facebook.com on 2026-09-06; Meta's menu labels drift, the object
names do not.

## What you need

- The developer app with the Marketing API (shared with the ads agent).
- A Facebook Page.
- An Instagram professional account linked to that Page.
- A media hosting bucket (Cloudflare R2) for Instagram media.

> **Click-by-click version:** `GUIDE-connect-facebook-instagram.md` covers
> parts 1–3 and 5 below with exact menu labels and a check script
> (`python -m social_agent.check_setup`). Use it first.

## 1. A Facebook Page (and Instagram)

Create a Page from your Facebook profile (facebook.com/pages/create). For
testing, an **unpublished** Page works: the Pages API accepts posts to
unpublished Pages, and nobody sees them.

Instagram: convert (or create) an Instagram account as a **professional**
account (Business or Creator) and link it to the Page in the Page's
settings → Linked accounts. Only linked professional accounts can publish
through the API.

Sources: <https://developers.facebook.com/docs/pages-api/getting-started>,
<https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/content-publishing>

## 2. Add the Pages + Instagram use cases to the app

App dashboard → Build your app → Use cases → Add use cases →
**"Manage everything on your Page"** (Pages) and the Instagram content
publishing use case. Adding them attaches the permissions below.

## 3. A token with publishing permissions

The ads token does **not** cover publishing. Generate a new one with:

- `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
  `pages_manage_metadata`, `publish_video` (Facebook)
- `instagram_basic`, `instagram_content_publish` (Instagram)
- `business_management` (lets the token see Pages owned by the Business)

Preferred: a **System User** token in Business Settings (assign the Page
to the system user, then Generate New Token with the permissions above).
Quick test: Graph API Explorer user token (expires in ~1 hour).

You must have the **CREATE_CONTENT** task on the Page (admin does).
Paste into the root `.env` as `SOCIAL_META_USER_TOKEN`. The service exchanges it for
Page tokens itself via `/me/accounts`.

Limited-access apps can publish to Pages **you** admin without App Review.
Client Pages need App Review, same as the ads side.

## 4. Media hosting (Cloudflare R2)

Instagram fetches media from a public URL, so local files are uploaded to a
bucket first. Facebook accepts direct file uploads, so this is Instagram-only
in practice.

1. Cloudflare dashboard → R2 → Create bucket (e.g. `dmt-media`).
2. Bucket → Settings → **Public access**: enable the r2.dev subdomain (or
   connect a custom domain). Copy the public base URL.
3. R2 → Manage R2 API Tokens → Create token with **Object Read & Write** on
   that bucket. Copy Access Key ID, Secret Access Key, and the S3 endpoint
   `https://<account-id>.r2.cloudflarestorage.com`.

Into the root `.env`:

```
R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=dmt-media
R2_PUBLIC_BASE_URL=https://pub-xxxx.r2.dev
```

Anything uploaded is public by design; do not put private files there.

## 5. Image generation (OpenAI)

The `generate_image` tool and the "Generate" button in the compose form use
the OpenAI Images API (model `gpt-image-2` by default; verified on
developers.openai.com 2026-09-06: sizes 1024x1024 / 1536x1024 / 1024x1536 and
up, quality low / medium / high). Create a key at platform.openai.com → API
keys and put it in the root `.env` as `OPENAI_API_KEY`. Billing is per token; the
service estimates cost from the usage each call returns, using the
`IMAGE_PRICE_*` rates in `.env` — update those from the pricing page. A daily
cap (`SOCIAL_MAX_IMAGES_PER_DAY`, default 20) stops runaway loops. Generated
files land in `media/generated/` and count as normal media.

## 6. Allow publishing

Find the Page id and Instagram account id (Sidebar → Social Publisher lists
targets once the token works, or `GET /me/accounts` in the Explorer) and
set `SOCIAL_PUBLISH_TARGETS=<page id>,<ig id>`. Everything not listed can be
drafted but never published.
