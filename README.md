# Shivuk — marketing lab

Shivuk (Hebrew for marketing): a hub of marketing tools with one design system, one `.env` and a cost ledger that shows what every run spent. Runs as one Docker container on a small VPS; the hosted instance is private, behind a shared Google sign-in. https://shivuk.gallab.top

Tools today: **SEO Keyword Research** (DataForSEO volumes + Claude recommendations), a **Meta Ads** chat agent (Marketing API, sandbox-only writes) and a **Social Publisher** (drafts, approvals, scheduling for Facebook Pages and Instagram).

## Run

```bash
npm install     # first time only
npm start       # starts the hub and the two Python services; open the address the command prints
```

The Python services need a one-time venv each; see [Python services](#python-services) below.

## Credentials (one-time)

One `.env` at the repo root holds the keys for **every** tool (copy `.env.example` to `.env`; on Windows, `copy`). The hub reads it and hands it to the Python services it starts; the services also read it directly. With the Meta values empty, both agents run in **dry run** against fake data.

| Variable | Where to get it |
| --- | --- |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys. Agent chat and recommendations. |
| `CLAUDE_MODEL` | The Claude model the chats start with; the picker can change it per chat. |
| `OPENAI_API_KEY` | platform.openai.com → API keys. Adds OpenAI text models to the picker and enables image generation. |
| `DATAFORSEO_USERNAME` / `DATAFORSEO_PASSWORD` | app.dataforseo.com → API Access (API password ≠ account password). |
| `META_APP_ID` / `META_APP_SECRET` | Meta developer app → App settings → Basic (`meta-ads-agent/SETUP.md`). |
| `META_ACCESS_TOKEN` | System User token with `ads_read`, `ads_management`, `business_management`. |
| `META_READ_ACCOUNT_IDS` | Ad accounts the agent may read; empty = every account the token sees. |
| `META_WRITE_ACCOUNT_IDS` | Ad accounts the agent may write to. Put the **sandbox** account id only. |
| `SOCIAL_META_USER_TOKEN` | Token with Pages + Instagram publishing permissions (`social-agent/SETUP.md`). |
| `SOCIAL_PUBLISH_TARGETS` | Page id and Instagram account id that may be published to; empty = none. |
| `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL` | Cloudflare dashboard → R2: a public bucket and an API token (Instagram needs media at a public URL). |
| `MONTHLY_API_CAP_USD` | Your own hard stop for API spend per month; chats and images refuse once the ledger reaches it. |

The app runs without them but shows a setup banner and returns clear errors until they are set.

## Stack

- **Hub:** Node + Express 5, static pages with one shared stylesheet (`public/theme.css`), no build step.
- **Agents:** Python + Flask services (`meta-ads-agent/`, `social-agent/`) started by the hub and proxied under `/api/*`.
- **Models:** Anthropic SDK (tool runner) and OpenAI SDK (Responses API, images); a per-chat model picker.
- **Shared:** `agentkit/` Python package: price book, SQLite cost ledger, per-turn context.
- **Hosting:** one Docker container (Node + both venvs) behind Caddy on a small VPS.

## Status

Meta Ads writes are **sandbox-only** (enforced in `meta_agent/guard.py`, not by the prompt); real ad accounts are read-only. Social publishing runs in **dry run** until a token and publish targets are set, and only listed targets can ever be published to.

## Design system

`public/theme.css` is the single source of visual truth: palette tokens
(ink, muted, line, accent, semantic ok / warn / danger), one type ramp
(Heebo for UI and Hebrew, IBM Plex Mono for ids and numbers), one spacing
and radius scale, and the component looks (sidebar, cards, tables, tabs,
buttons, forms, chat bubbles, approval cards, pills). The per-tool
stylesheets only lay out their own screens; colours and type come from the
tokens. New screens should use the tokens and the existing classes
(`.ma-card`, `.ma-table`, `.ma-pill`, `.btn`, `.tool-tabs`).

## How the hub is arranged

The hub opens on **Overview**: a greeting, a create prompt that routes to
the right tool, quick actions, a KPI strip (drafts, awaiting approval,
scheduled, monthly spend), the workspace lists, a "finish setting up" rail
and a promo card. Sidebar: Overview, Social Publisher, Meta Ads, Keyword
Research, Approvals (live count), Calendar, Spend & models; below the line
Connections and Settings. Inside each agent tool the chat comes first, with
tabs for the hands-on parts. **Approvals** gathers every pending request
from both agents; **Connections** shows what is connected with links to the
guides; **Settings** shows the current limits read from the services.

## Tools

### SEO Keyword Research
- **Keyword Volumes** — paste keywords (Hebrew/English, one per line, up
  to 700), pick a market (IL/US/UK/DE/FR) → monthly volume, CPC,
  competition, top-of-page bid. Sortable columns.
- **Domain Ideas** — enter any root domain (yours or a competitor's) →
  Google Ads keyword ideas extracted from the site, with volumes
  included. One click sends them to the Volumes tab.
- **Ask Claude to recommend** — sends the result table to Claude for
  targeting priorities, page grouping, traps to avoid and missing-keyword
  ideas.
- **Costs** — every run shows its actual DataForSEO cost (from the API
  response) and Claude cost (computed from token usage at the price-book
  rates), plus session totals in the header.

### Meta Ads (`meta-ads-agent/`, Python)
A chat agent for Meta (Facebook / Instagram) ads on the Marketing API.
Reads any ad account the token can see; creates campaigns and ad sets,
pauses / reactivates them and changes budgets **only on the sandbox
account**, each write behind an approval and an audit log. Claude by
default, with a per-chat model picker that also offers OpenAI models; cost
shown per turn.

**In the hub** (sidebar → Meta Ads): chat with inline Approve / Decline
cards, plus a live workspace — account picker, campaign table, per-campaign
KPIs and ad sets for a chosen date range, one-click prompts ("analyse",
"pause", "activate"), and the audit log. The page talks to a small Python
API (Flask) that `npm start` launches automatically and stops with the hub,
so one command brings up every tool. The hub proxies `/api/meta/*` to it.
To run the API by hand instead: `npm run meta` (the hub detects and reuses
it), or set `META_AGENT_AUTOSTART=0` to turn autostart off. The terminal
version still works: `python -m meta_agent` from the service's venv.

Details, abilities table and example prompts: `meta-ads-agent/README.md`.
Meta setup (developer app, sandbox account, token): `meta-ads-agent/SETUP.md`.

### Social Publisher (`social-agent/`, Python)
Publishing desk for social channels, Meta first (Facebook Page +
Instagram professional account), long-term Sprout-Social-like across
LinkedIn and TikTok too. In chat, the model writes the caption and saves a
**draft**; you approve, and it **publishes now or schedules**. The hub
page (sidebar → Social Publisher) has the chat with approval cards, a
compose form with media upload, a calendar (drafts / scheduled /
published / failed) and the audit log.

Guard rails in code: only targets in `SOCIAL_PUBLISH_TARGETS` can be
published to, `SOCIAL_MAX_POSTS_PER_DAY` caps the rolling 24 h, every
platform action is logged. Facebook scheduling uses the platform's own
queue; Instagram cannot schedule natively, so the service's scheduler
publishes at the time (the hub must be running). Instagram needs media at
a public URL, so local files go to a Cloudflare R2 bucket first.

`npm start` launches this service too (Flask, proxied as `/api/social/*`);
by hand: `npm run social`. With no `SOCIAL_META_USER_TOKEN` it runs in dry
run against a fake Page and Instagram account. Setup (Page, Instagram,
token permissions, R2): `social-agent/SETUP.md`; details: `social-agent/README.md`.

### Spend & models (sidebar → Spend & models)
One ledger for every paid action across the services (`data/ledger.sqlite`,
written by the shared `agentkit` package): chat turns, images, later research
runs. The view shows this month against `MONTHLY_API_CAP_USD`, spend by model,
service, action and day, cost per post and per campaign, the price book and
the latest rows. Prices come only from `pricing.json`, each with its vendor
source and check date. Every chat has a **model picker** in its header (no
hidden default; the `.env` model is only the starting value): Claude models
run on the Anthropic API, OpenAI models on the Responses API (selectable once
`OPENAI_API_KEY` is set), same tools and approvals either way. Image quality
options show their estimated price. When the monthly cap is reached,
chats and image generation refuse until it is raised.

## Costs to expect

- DataForSEO search-volume call: ~$0.05–0.075 per run (up to 700 keywords).
- Claude recommendation: typically $0.05–0.25 per run depending on table size.
- Agent chat turns: cents per turn, shown live; images a few cents each (daily cap).

## Python services

Each service has its own venv, and both import the shared `agentkit` package, so
install it in editable mode alongside the requirements (this mirrors the
`Dockerfile`):

```bash
cd meta-ads-agent            # same for social-agent
python -m venv .venv
.venv/bin/pip install -r requirements.txt -e ../agentkit
```

On Windows the venv binaries are under `.venv\Scripts\` instead of `.venv/bin/`
(`.venv\Scripts\pip`, `.venv\Scripts\python`). The `npm run meta` and
`npm run social` scripts in `package.json` use the Windows paths; on macOS/Linux
run `.venv/bin/python serve.py` from the service folder, or just `npm start`,
which finds the venv itself.

## Tests

Free, no network, against the fake Meta / fake channel:

```bash
cd agentkit && python -m pytest -q
cd meta-ads-agent && .venv/bin/python -m pytest -q
cd social-agent && .venv/bin/python -m pytest -q
```

## Deploy

Push to `main`. The GitHub Action (`.github/workflows/deploy.yml`) joins the private network, syncs the repo to the server, rebuilds the Compose stack (`deploy/compose.yml`: one container, data and media in mounted folders), installs the Caddy site (`deploy/shivuk.caddy`) and runs a smoke test.

## Adding tools later

The sidebar is a hub. A JavaScript tool gets a sidebar link with
`data-view="<name>"`, a `<section id="view-<name>">` and endpoints in
`server.js`. A Python agent service follows the two existing ones: a
folder with `serve.py` + `.venv`, an entry in `SERVICES` in `server.js`
(mount path, port), a page that uses `public/agent-chat.js` for the chat
and approval cards.
