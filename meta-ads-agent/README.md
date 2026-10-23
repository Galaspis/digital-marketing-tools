# meta-ads-agent

A terminal chat agent for Meta (Facebook / Instagram) ads. You type in plain
language; Claude reads campaigns, ad sets, ads and insights through the Meta
Marketing API, explains what it sees, and — on the **sandbox** account only —
creates campaigns and ad sets, pauses / reactivates them and changes budgets.
Every write asks you y/n first and is written to an audit log.

`SETUP.md` explains the one-time Meta setup (developer app, sandbox ad account, token).

## Run

Two front ends share the same agent, tools and guard:

**Web UI** (inside the Shivuk hub). From the hub root:

```bash
npm start        # starts the hub and this Python service with it → sidebar → Meta Ads
```

The hub proxies `/api/meta/*` to the service. To run the service by hand
instead, `npm run meta` (Windows path in `package.json`; on macOS/Linux run
`.venv/bin/python serve.py` from this folder) and the hub reuses it.

**Terminal chat:**

```bash
cd meta-ads-agent
.venv/bin/python -m meta_agent          # Windows: .venv\Scripts\python -m meta_agent
```

With no `META_ACCESS_TOKEN` in the hub's root `.env` the agent runs in **dry run** against an
in-memory fake Meta (one sandbox account, one read-only "client" account with
made-up numbers). Same code path, no Meta calls, but **Claude calls are real
and cost money** — the terminal prints the cost of every turn.

First-time setup:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -e ../agentkit    # Windows: .venv\Scripts\pip ...
```

The `-e ../agentkit` part installs the shared `agentkit` package (ledger, price
book, per-turn context) the service imports; the Dockerfile does the same.
All keys go in one file, the hub's root `.env` (see `.env.example` there).

Tests (free, no network):

```bash
.venv/bin/python -m pytest -q           # Windows: .venv\Scripts\python -m pytest -q
```

## What it can do (V1)

| Ability | Real accounts | Sandbox account |
|---|---|---|
| List accounts, campaigns, ad sets, ads | yes | yes |
| Insights: spend, impressions, reach, clicks, CTR, CPC, CPM, frequency, actions; totals or per day; breakdowns | yes | yes (always zero — sandbox ads never deliver) |
| Create campaign / ad set (always created PAUSED) | **no** | yes, after y/n |
| Start / stop (pause) / archive | **no** | yes, after y/n |
| Change budgets (capped by `MAX_DAILY_BUDGET_MINOR`) | **no** | yes, after y/n |
| Create ads / creatives | not yet | not yet |

"Real accounts are read-only" is enforced in code (`meta_agent/guard.py`),
not by the prompt. Unlocking a real account means adding its id to
`META_WRITE_ACCOUNT_IDS` on purpose.

## Layout

```
meta_agent/
  cli.py          terminal loop, /commands, the y/n approval prompt
  server.py       Flask API for the web UI: chat turns, blocking approvals, reads
  agent.py        Claude tool-runner loop, system prompt, per-turn cost
  tools.py        the 9 tools Claude can call (typed, validated)
  guard.py        policy: sandbox-only writes, budget cap, approval, audit log
  meta_client.py  LiveMeta (facebook_business SDK) and FakeMeta (dry run)
  config.py       .env → Settings
  check_setup.py  free token check: python -m meta_agent.check_setup
serve.py          starts server.py from any cwd (used by `npm run meta`)
tests/            policy, tool and server tests against FakeMeta
../public/meta-agent.js, meta-agent.css   the hub page for this tool
logs/actions.jsonl  every tool call, approved or refused (gitignored)
```

## Terminal commands

```
/accounts   list ad accounts (no Claude call)
/cost       tokens and USD this session
/new        forget the conversation
/quit
```

## Example prompts

- "Which account is spending the most this month, and on what?"
- "Compare the last 7 days with the 7 before on campaign 1002."
- "Anything wasting money? Look for spend with no leads."
- "In the sandbox, create an OUTCOME_TRAFFIC campaign called 'Test IL' with 20 ILS/day and one ad set targeting Israel, 25–45."
- "Pause the sandbox campaign you just made."
