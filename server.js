import dotenv from "dotenv";
// The hub's .env beats variables already set in the OS (a stale system-wide key
// must not shadow the one written in the file).
dotenv.config({ override: true });
import express from "express";
import fs from "node:fs";
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Anthropic from "@anthropic-ai/sdk";

const here = path.dirname(fileURLToPath(import.meta.url));
const app = express();
app.use(express.json({ limit: "1mb" }));
app.use(express.static(path.join(here, "public")));

const PORT = process.env.PORT || 4400;

// ---------------------------------------------------------------- Python agent services
// Each agent is a small Flask service. The hub proxies /api/<mount>/* to it so the
// page stays same-origin, and `npm start` launches any service that is not already
// answering (one started by hand is reused). Opt out per service with
// META_AGENT_AUTOSTART=0 / SOCIAL_AGENT_AUTOSTART=0.
const SERVICES = [
  { key: "META_AGENT", label: "meta-agent", mount: "/api/meta", dir: "meta-ads-agent",
    url: process.env.META_AGENT_URL || "http://127.0.0.1:4411" },
  { key: "SOCIAL_AGENT", label: "social-agent", mount: "/api/social", dir: "social-agent",
    url: process.env.SOCIAL_AGENT_URL || "http://127.0.0.1:4412" },
];
const children = [];

for (const svc of SERVICES) {
  svc.hint = "Starting… reload in a few seconds.";
  app.use(svc.mount, async (req, res) => {
    const target = svc.url + "/api" + req.url;
    const ctype = req.headers["content-type"] || "";
    try {
      const init = { method: req.method, headers: {} };
      if (!["GET", "HEAD"].includes(req.method)) {
        if (!ctype || ctype.includes("application/json")) {
          init.headers["content-type"] = "application/json";
          init.body = JSON.stringify(req.body ?? {});
        } else {
          // multipart (media upload) and anything else: stream the raw body through
          init.headers["content-type"] = ctype;
          init.body = req;
          init.duplex = "half";
        }
      }
      const upstream = await fetch(target, { ...init, redirect: "manual" });
      if (upstream.status >= 300 && upstream.status < 400 && upstream.headers.get("location")) {
        return res.redirect(upstream.status, upstream.headers.get("location"));  // media previews of public URLs
      }
      const ct = upstream.headers.get("content-type") || "application/json";
      const body = Buffer.from(await upstream.arrayBuffer());
      res.status(upstream.status).set("content-type", ct);
      if (upstream.headers.get("cache-control")) res.set("cache-control", upstream.headers.get("cache-control"));
      res.send(body);
    } catch {
      res.status(503).json({ error: `${svc.label} server is not running`, hint: svc.hint });
    }
  });
}

async function serviceUp(svc) {
  try {
    const r = await fetch(svc.url + "/api/status", { signal: AbortSignal.timeout(1500) });
    return r.ok;
  } catch {
    return false;
  }
}

async function startService(svc) {
  if (process.env[`${svc.key}_AUTOSTART`] === "0") {
    svc.hint = `Autostart is off (${svc.key}_AUTOSTART=0). Start it by hand from the ${svc.dir} folder.`;
    return;
  }
  if (await serviceUp(svc)) {
    console.log(`[${svc.label}] already running at ${svc.url} — reusing it`);
    return;
  }
  const dir = path.join(here, svc.dir);
  const python = process.platform === "win32"
    ? path.join(dir, ".venv", "Scripts", "python.exe")
    : path.join(dir, ".venv", "bin", "python");
  if (!fs.existsSync(python)) {
    svc.hint = `Python venv missing. Run once:  cd ${svc.dir} && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt -e ../agentkit  (macOS/Linux: .venv/bin/pip)`;
    console.warn(`[${svc.label}] ${svc.hint}`);
    return;
  }
  const child = spawn(python, [path.join(dir, "serve.py")], {
    cwd: dir,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  children.push(child);
  child.stdout.on("data", (d) => process.stdout.write(`[${svc.label}] ${d}`));
  child.stderr.on("data", (d) => process.stderr.write(`[${svc.label}] ${d}`));
  child.on("exit", (code) => {
    svc.hint = `The Python API exited (code ${code}). Check the hub terminal for its error, then restart npm start.`;
    console.warn(`[${svc.label}] exited with code ${code}`);
  });
}

function startServices() {
  for (const svc of SERVICES) startService(svc);
}
const stopChildren = () => { for (const c of children) { try { c.kill(); } catch {} } };
process.on("exit", stopChildren);
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => { stopChildren(); process.exit(0); });
}

// ---------------------------------------------------------------- run history
// Every paid API result is stored locally; identical searches are served
// from history for free. File: data/runs.json
const DATA_DIR = path.join(here, "data");
const RUNS_FILE = path.join(DATA_DIR, "runs.json");
fs.mkdirSync(DATA_DIR, { recursive: true });

function loadRuns() {
  try {
    return JSON.parse(fs.readFileSync(RUNS_FILE, "utf-8"));
  } catch {
    return [];
  }
}
function saveRuns(runs) {
  fs.writeFileSync(RUNS_FILE, JSON.stringify(runs, null, 2), "utf-8");
}
function runKey(params) {
  return crypto.createHash("sha256").update(JSON.stringify(params)).digest("hex").slice(0, 16);
}
function newRunId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

// ---------------------------------------------------------------- shortlist
// Keywords the user starred — the curated set that will actually be used
// on the website. File: data/shortlist.json
const SHORTLIST_FILE = path.join(DATA_DIR, "shortlist.json");
function loadShortlist() {
  try {
    return JSON.parse(fs.readFileSync(SHORTLIST_FILE, "utf-8"));
  } catch {
    return [];
  }
}
function saveShortlist(items) {
  // Keep a rolling backup of the previous state before every write
  try {
    if (fs.existsSync(SHORTLIST_FILE)) {
      fs.copyFileSync(SHORTLIST_FILE, SHORTLIST_FILE + ".bak");
    }
  } catch {}
  fs.writeFileSync(SHORTLIST_FILE, JSON.stringify(items, null, 2), "utf-8");
}
// Identity = keyword + market only. The language filter used to FIND the
// keyword doesn't create a different keyword — prevents duplicates when the
// same term is starred from different search modes.
function shortlistId(keyword, location_code) {
  return crypto
    .createHash("sha256")
    .update(`${keyword.toLowerCase()}|${location_code}`)
    .digest("hex")
    .slice(0, 12);
}

app.get("/api/shortlist", (_req, res) => {
  res.json({ items: loadShortlist() });
});

// Add (or re-add) one starred keyword with its data snapshot
app.post("/api/shortlist", (req, res) => {
  const { row, location_code, language_code, source_run_id } = req.body;
  if (!row?.keyword) return res.status(400).json({ error: "No keyword row" });
  const items = loadShortlist();
  const id = shortlistId(row.keyword, location_code);
  if (!items.find((i) => i.id === id)) {
    items.push({
      id,
      keyword: row.keyword,
      volume: row.volume ?? null,
      cpc: row.cpc ?? null,
      competition_index: row.competition_index ?? row.competition ?? null,
      low_bid: row.low_bid ?? null,
      high_bid: row.high_bid ?? null,
      location_code: Number(location_code) || null,
      language_code: language_code || null,
      source_run_id: source_run_id || null,
      page: "",
      note: "",
      added_at: new Date().toISOString(),
    });
    saveShortlist(items);
  }
  res.json({ ok: true, id });
});

app.patch("/api/shortlist/:id", (req, res) => {
  const items = loadShortlist();
  const item = items.find((i) => i.id === req.params.id);
  if (!item) return res.status(404).json({ error: "Not found" });
  if (typeof req.body.page === "string") item.page = req.body.page;
  if (typeof req.body.note === "string") item.note = req.body.note;
  saveShortlist(items);
  res.json({ ok: true });
});

// Restore a full shortlist (recovery from browser memory / backup)
app.post("/api/shortlist/restore", (req, res) => {
  const incoming = Array.isArray(req.body.items) ? req.body.items : [];
  const valid = incoming.filter((i) => i && i.id && i.keyword);
  if (!valid.length) return res.status(400).json({ error: "No valid items to restore" });
  const items = loadShortlist();
  let added = 0;
  for (const i of valid) {
    if (!items.find((x) => x.id === i.id)) {
      items.push(i);
      added++;
    }
  }
  saveShortlist(items);
  res.json({ ok: true, restored: added, total: items.length });
});

app.delete("/api/shortlist/:id", (req, res) => {
  const items = loadShortlist();
  const idx = items.findIndex((i) => i.id === req.params.id);
  if (idx === -1) return res.status(404).json({ error: "Not found" });
  items.splice(idx, 1);
  saveShortlist(items);
  res.json({ ok: true });
});

// ---------------------------------------------------------------- credentials
const DFS_USER = process.env.DATAFORSEO_USERNAME;
const DFS_PASS = process.env.DATAFORSEO_PASSWORD;
const dfsAuth = () =>
  "Basic " + Buffer.from(`${DFS_USER}:${DFS_PASS}`).toString("base64");

// Claude pricing (USD per 1M tokens) — Claude Opus 5
const CLAUDE_MODEL = "claude-opus-5";
const CLAUDE_INPUT_PER_M = 5.0;
const CLAUDE_OUTPUT_PER_M = 25.0;

const anthropic = process.env.ANTHROPIC_API_KEY ? new Anthropic() : null;

app.get("/api/status", (_req, res) => {
  res.json({
    dataforseo: Boolean(DFS_USER && DFS_PASS),
    claude: Boolean(anthropic),
  });
});

// ---------------------------------------------------------------- DataForSEO
async function dfsPost(endpoint, payload) {
  if (!DFS_USER || !DFS_PASS) {
    const err = new Error(
      "DataForSEO credentials missing. Set DATAFORSEO_USERNAME and DATAFORSEO_PASSWORD in the .env file, then restart the app.",
    );
    err.status = 503;
    throw err;
  }
  const resp = await fetch(`https://api.dataforseo.com/v3/${endpoint}`, {
    method: "POST",
    headers: { Authorization: dfsAuth(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = new Error(`DataForSEO HTTP ${resp.status}`);
    err.status = 502;
    throw err;
  }
  const data = await resp.json();
  if (data.status_code !== 20000) {
    const err = new Error(`DataForSEO error: ${data.status_message}`);
    err.status = 502;
    throw err;
  }
  const task = data.tasks?.[0];
  if (!task || task.status_code !== 20000) {
    const err = new Error(
      `DataForSEO task error: ${task?.status_message ?? "no task returned"}`,
    );
    err.status = 502;
    throw err;
  }
  return { result: task.result ?? [], cost: data.cost ?? 0 };
}

// Google Ads uses legacy language codes for some languages (Hebrew = "iw")
function gadsLang(code) {
  if (!code) return undefined;
  return { he: "iw" }[code] ?? code;
}

function mapKeywordRow(item) {
  return {
    keyword: item.keyword,
    volume: item.search_volume ?? null,
    cpc: item.cpc ?? null,
    competition: item.competition ?? null,
    competition_index: item.competition_index ?? null,
    low_bid: item.low_top_of_page_bid ?? null,
    high_bid: item.high_top_of_page_bid ?? null,
  };
}

function serveCachedOrNull(res, key, force) {
  if (force) return null;
  const hit = loadRuns().find((r) => r.key === key);
  if (!hit) return null;
  res.json({
    rows: hit.rows,
    dataforseo_cost: 0,
    cached: true,
    cached_at: hit.at,
    run_id: hit.id,
    reco: hit.reco ?? null,
    domain: hit.params.target ?? undefined,
  });
  return true;
}

function storeRun(type, label, params, key, rows, cost) {
  const runs = loadRuns();
  const run = {
    id: newRunId(),
    key,
    type,
    label,
    params,
    rows,
    dataforseo_cost: cost,
    at: new Date().toISOString(),
  };
  runs.push(run);
  saveRuns(runs);
  return run;
}

// Latest known data per keyword for a given market+language, across all past runs
function keywordIndex(loc, lang) {
  const idx = new Map();
  for (const r of loadRuns()) {
    if (r.type !== "volumes") continue;
    if (r.params.location_code !== loc) continue;
    if ((r.params.language_code || null) !== (lang || null)) continue;
    for (const row of r.rows) idx.set(row.keyword.toLowerCase(), row);
  }
  return idx;
}

// Keyword volumes: POST { keywords[], location_code, language_code?, force? }
// Keyword-level reuse: keywords already searched (same market+language) are
// served from history; only genuinely new ones trigger an API call.
app.post("/api/volumes", async (req, res) => {
  try {
    const { keywords, location_code, language_code, force } = req.body;
    if (!Array.isArray(keywords) || keywords.length === 0) {
      return res.status(400).json({ error: "No keywords provided" });
    }
    if (keywords.length > 700) {
      return res.status(400).json({ error: "Max 700 keywords per run" });
    }
    const loc = Number(location_code) || 2376;
    const lang = language_code || null;
    const keyParams = {
      type: "volumes",
      keywords: [...keywords].map((k) => k.toLowerCase()).sort(),
      loc,
      lang,
    };
    const key = runKey(keyParams);
    if (serveCachedOrNull(res, key, force)) return;

    // Split into already-known vs. new keywords
    const idx = force ? new Map() : keywordIndex(loc, lang);
    const knownRows = [];
    const missing = [];
    for (const kw of keywords) {
      const hit = idx.get(kw.toLowerCase());
      if (hit) knownRows.push(hit);
      else missing.push(kw);
    }

    let fetchedRows = [];
    let cost = 0;
    if (missing.length > 0) {
      const task = { keywords: missing, location_code: loc };
      if (lang) task.language_code = gadsLang(lang);
      const r = await dfsPost("keywords_data/google_ads/search_volume/live", [task]);
      fetchedRows = r.result.map(mapKeywordRow);
      cost = r.cost;
    }

    const rows = [...knownRows, ...fetchedRows];
    const label =
      `${keywords.length} keywords · ${keywords.slice(0, 3).join(", ")}${keywords.length > 3 ? "…" : ""}` +
      (missing.length === 0 ? " (from history)" : "");
    const run = storeRun(
      "volumes",
      label,
      { keywords, location_code: loc, language_code: lang },
      key,
      rows,
      cost,
    );
    res.json({
      rows,
      dataforseo_cost: cost,
      run_id: run.id,
      served_from_history: knownRows.length,
      fetched: fetchedRows.length,
      cached: missing.length === 0 || undefined,
      cached_at: missing.length === 0 ? new Date().toISOString() : undefined,
    });
  } catch (e) {
    res.status(e.status || 500).json({ error: e.message });
  }
});

// Domain keyword ideas: POST { target, location_code, language_code?, scope?, force? }
// scope "site" = ideas from the whole domain's content; "page" = that page only.
app.post("/api/site-keywords", async (req, res) => {
  try {
    const { target, location_code, language_code, scope, force } = req.body;
    const targetType = scope === "page" ? "page" : "site";
    let domain = String(target || "").trim().replace(/^https?:\/\//, "");
    if (targetType === "site") domain = domain.replace(/\/.*$/, "");
    if (!domain) return res.status(400).json({ error: "No domain or page URL provided" });
    const loc = Number(location_code) || 2376;
    const keyParams = { type: "domain", domain: domain.toLowerCase(), loc, lang: language_code || null, targetType };
    const key = runKey(keyParams);
    if (serveCachedOrNull(res, key, force)) return;

    const task = { target: domain, location_code: loc, target_type: targetType };
    if (language_code) task.language_code = gadsLang(language_code);
    const { result, cost } = await dfsPost(
      "keywords_data/google_ads/keywords_for_site/live",
      [task],
    );
    const rows = result
      .map(mapKeywordRow)
      .sort((a, b) => (b.volume ?? 0) - (a.volume ?? 0))
      .slice(0, 300);
    const run = storeRun("domain", `${domain}${targetType === "page" ? " (page)" : ""}`, { target: domain, location_code: loc, language_code: language_code || null, scope: targetType }, key, rows, cost);
    res.json({ domain, rows, dataforseo_cost: cost, run_id: run.id });
  } catch (e) {
    res.status(e.status || 500).json({ error: e.message });
  }
});

// ---------------------------------------------------------------- history
app.get("/api/history", (_req, res) => {
  const runs = loadRuns()
    .map((r) => ({
      id: r.id,
      type: r.type,
      label: r.label,
      at: r.at,
      rows: r.rows.length,
      dataforseo_cost: r.dataforseo_cost,
      has_reco: Boolean(r.reco),
      location_code: r.params.location_code,
    }))
    .reverse();
  res.json({ runs });
});

app.get("/api/history/:id", (req, res) => {
  const run = loadRuns().find((r) => r.id === req.params.id);
  if (!run) return res.status(404).json({ error: "Run not found" });
  res.json(run);
});

app.delete("/api/history/:id", (req, res) => {
  const runs = loadRuns();
  const idx = runs.findIndex((r) => r.id === req.params.id);
  if (idx === -1) return res.status(404).json({ error: "Run not found" });
  runs.splice(idx, 1);
  saveRuns(runs);
  res.json({ ok: true });
});

// ---------------------------------------------------------------- Claude
// POST { mode, context, rows, run_id? }
app.post("/api/recommend", async (req, res) => {
  try {
    if (!anthropic) {
      return res.status(503).json({
        error:
          "ANTHROPIC_API_KEY missing. Add it to the .env file, then restart the app.",
      });
    }
    const { mode, context, rows, run_id } = req.body;
    if (!Array.isArray(rows) || rows.length === 0) {
      return res.status(400).json({ error: "No data rows to analyze" });
    }
    const table = rows
      .slice(0, 300)
      .map(
        (r) =>
          `${r.keyword}\tvol:${r.volume ?? "-"}\tcpc:$${r.cpc ?? "-"}\tcomp:${r.competition_index ?? r.competition ?? "-"}`,
      )
      .join("\n");

    const system = `You are an SEO strategist for an Israeli digital-marketing consultant. You analyze Google Ads keyword data (volume, CPC in USD, competition 0-100) and give sharp, practical recommendations. Hebrew keywords are as important as English ones. Answer in English, but keep Hebrew keywords in Hebrew. Be concise and decisive: recommend which keywords to target, group them by intent/page, flag traps (high CPC + low volume, ambiguous intent), and suggest 2-3 keyword ideas the list is missing. Use short headings and bullet lists.`;

    const userMsg =
      mode === "domain"
        ? `These keyword ideas were extracted for the domain and context below. Recommend which to target and how to organize them into pages.\n\nContext: ${context || "(none)"}\n\nKeyword data:\n${table}`
        : `Analyze this keyword research data and recommend targeting priorities.\n\nContext: ${context || "(none)"}\n\nKeyword data:\n${table}`;

    const response = await anthropic.beta.messages.create({
      model: CLAUDE_MODEL,
      max_tokens: 16000,
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      system,
      messages: [{ role: "user", content: userMsg }],
    });

    if (response.stop_reason === "refusal") {
      return res.status(502).json({
        error: `Claude declined this request${response.stop_details?.explanation ? `: ${response.stop_details.explanation}` : "."}`,
      });
    }

    const text = response.content
      .filter((b) => b.type === "text")
      .map((b) => b.text)
      .join("\n");

    const inTok = response.usage.input_tokens ?? 0;
    const outTok = response.usage.output_tokens ?? 0;
    const claudeCost =
      (inTok / 1e6) * CLAUDE_INPUT_PER_M + (outTok / 1e6) * CLAUDE_OUTPUT_PER_M;

    // Attach the recommendation to the stored run so it's never re-bought
    if (run_id) {
      const runs = loadRuns();
      const run = runs.find((r) => r.id === run_id);
      if (run) {
        run.reco = { text, claude_cost: claudeCost, at: new Date().toISOString(), context: context || null };
        saveRuns(runs);
      }
    }

    res.json({
      text,
      usage: { input_tokens: inTok, output_tokens: outTok },
      claude_cost: claudeCost,
      model: response.model,
    });
  } catch (e) {
    if (e instanceof Anthropic.AuthenticationError) {
      return res.status(401).json({ error: "Invalid Anthropic API key." });
    }
    if (e instanceof Anthropic.RateLimitError) {
      return res.status(429).json({ error: "Claude rate limit — try again in a minute." });
    }
    if (e instanceof Anthropic.APIError) {
      return res.status(e.status || 500).json({ error: `Claude API error: ${e.message}` });
    }
    res.status(e.status || 500).json({ error: e.message });
  }
});

app.listen(PORT, () => {
  console.log(`Digital Marketing Tools running at http://localhost:${PORT}`);
  if (!DFS_USER) console.log("  ! DATAFORSEO_USERNAME / DATAFORSEO_PASSWORD not set");
  if (!anthropic) console.log("  ! ANTHROPIC_API_KEY not set");
  startServices();
});
