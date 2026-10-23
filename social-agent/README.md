# social-agent

Publishing desk for social channels inside the Shivuk hub.
You describe a post in chat; Claude writes the caption and saves a **draft**
in the calendar; you approve, and it **publishes now or schedules** to a
Facebook Page or an Instagram professional account. Long-term direction:
Sprout-Social-like across Meta, LinkedIn and TikTok. Meta is first.

Meta and media setup (one-time) is in `SETUP.md`.

## Run

Part of the hub: `npm start` in the hub root launches this service (Flask)
and proxies `/api/social/*` to it. Sidebar → Social Publisher. Standalone:
`npm run social` (Windows path in `package.json`), or from this folder
`.venv/bin/python serve.py` (Windows: `.venv\Scripts\python serve.py`).

First time:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -e ../agentkit    # Windows: .venv\Scripts\pip ...
```

`-e ../agentkit` installs the shared `agentkit` package the service imports
(the Dockerfile does the same). Keys live in the hub's root `.env` (see its
`.env.example`). With no `SOCIAL_META_USER_TOKEN` it runs in **dry run** against a fake
Page and Instagram account. Claude calls are real and billed per turn.

Tests (no network): `.venv/bin/python -m pytest -q` (Windows: `.venv\Scripts\python -m pytest -q`)

## What it does (V1)

| Step | Who | Cost / risk |
|---|---|---|
| Draft: caption, media, link, intended time | Claude or the form | free, database only |
| Upload media to hosting (R2) for Instagram | tool or form | R2 storage, cents |
| Generate an image (OpenAI gpt-image-2) | tool or form | a few cents per image, daily cap |
| Publish now | approval card, or your click | public post |
| Schedule (Facebook: platform queue, 10 min–30 d; Instagram: our scheduler) | approval card, or your click | public post later |
| Cancel a scheduled post | approval if the platform holds it | none |

Guard rails, enforced in code (`social_agent/guard.py`): only targets in
`SOCIAL_PUBLISH_TARGETS` can be published to; `SOCIAL_MAX_POSTS_PER_DAY`
caps the rolling 24-hour count; every platform action is logged to
`logs/actions.jsonl`, approved or refused.

Post kinds: Facebook Page `text`, `link`, `photo`, `video`. Instagram
`photo`, `reel`, `story`, `carousel` (all need media at a public URL).
Not yet: Facebook stories and reels, comments and engagement, analytics,
LinkedIn, TikTok.

## Layout

```
social_agent/
  server.py        Flask API: posts CRUD, publish/schedule/cancel, media upload, chat + approvals
  agent.py         Claude tool-runner loop, system prompt, per-turn cost
  tools.py         11 Claude tools (drafting + images free-ish; platform actions guarded)
  guard.py         allowlist, daily cap, approval, audit log
  publisher.py     state transitions that hit the platform (shared by tools, UI, scheduler)
  scheduler.py     background thread for locally scheduled posts (Instagram)
  store.py         SQLite posts table (data/posts.sqlite)
  media.py         R2/S3 upload, path confinement to the media folder
  images.py        OpenAI image generation (+ fake), cost estimate from usage
  channels/        base.Channel protocol; meta.py = LiveMetaChannel + FakeMetaChannel
  check_setup.py   free token / Pages / Instagram / permissions check
serve.py           entry point used by `npm run social`
GUIDE-connect-facebook-instagram.md   click-by-click connection guide
tests/             flow + server tests against the fake channel
../public/social-agent.js, .css   the hub page
```
