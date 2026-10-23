// Shell: top bar (breadcrumb, search, mode, bell, date), Approvals, Connections, Settings.
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const { esc } = AgentChat;
  const social = AgentChat.api("/api/social"), meta = AgentChat.api("/api/meta");
  const safe = async (p) => { try { return await p; } catch (e) { return null; } };
  const main = document.querySelector("main");
  const TITLES = { home: "Overview", social: "Social Publisher", meta: "Meta Ads", seo: "Keyword Research", approvals: "Approvals", spend: "Spend & models", connections: "Connections", settings: "Settings" };

  // ---------------------------------------------------------------- top bar
  $("#top-date").textContent = new Date().toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  function crumbs() { $("#crumb").textContent = TITLES[main.dataset.view] || "Overview"; }
  new MutationObserver(crumbs).observe(main, { attributes: true, attributeFilter: ["data-view"] });
  crumbs();

  $("#top-search").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const q = e.target.value.trim(); if (!q) return;
    e.target.value = "";
    const ads = /\b(ad|ads|campaign|budget|cpc|ctr|spend|audience|קמפיין|מודעה|תקציב)\b/i.test(q);
    window.dmtShow(ads ? "meta" : "social"); window.dmtPane(ads ? "meta" : "social", "chat");
    setTimeout(() => (ads ? window.dmtMetaChat : window.dmtSocialChat).fill(q), 80);
  });
  $("#bell").onclick = () => window.dmtShow("approvals");

  async function refreshMode() {
    const [so, me] = await Promise.all([safe(social("/status")), safe(meta("/status"))]);
    const pill = $("#mode-pill");
    if (!so && !me) { pill.className = "mode-pill off"; pill.textContent = "Offline"; return; }
    const live = (so && so.mode === "live") || (me && me.mode === "live");
    pill.className = "mode-pill " + (live ? "sandbox" : "off");
    pill.innerHTML = live ? `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 3h6M10 3v6l-5 8a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-8V3"/></svg>Sandbox` : "Dry run";
    window.dmtStatus = { social: so, meta: me };
  }

  // ---------------------------------------------------------------- approvals
  const AP = { timer: null };
  async function pendingList() {
    const [s, m] = await Promise.all([safe(social("/turn")), safe(meta("/turn"))]);
    const out = [];
    if (s && s.pending) out.push({ svc: "social", label: "Social Publisher", ...s.pending });
    if (m && m.pending) out.push({ svc: "meta", label: "Meta Ads", ...m.pending });
    return out;
  }
  function badge(n) {
    const c = $("#nav-approvals .count"); c.textContent = n; c.classList.toggle("hot", n > 0);
    const b = $("#bell .badge"); b.textContent = n; b.classList.toggle("on", n > 0);
  }
  async function renderApprovals() {
    const items = await pendingList();
    badge(items.length);
    const box = $("#ap-list"); if (!box) return;
    if (!items.length) {
      box.innerHTML = `<div class="card ap-empty"><svg width="72" height="72" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/></svg><h4>Nothing waiting for you</h4><p>When an agent wants to publish, schedule or spend, the request appears here and in its chat.</p></div>`;
      return;
    }
    box.innerHTML = items.map((p) => `<div class="ap-card" data-svc="${p.svc}" data-id="${esc(p.id)}">
        <div class="who"><b>${esc(p.action)}</b> → ${esc(p.account)} <span class="ma-badge dry">${esc(p.label)}</span></div>
        <div class="acts"><button class="ma-btn ok">Approve</button><button class="ma-btn no">Decline</button></div>
        <pre>${esc(JSON.stringify(p.details, null, 2))}</pre>
        ${p.money && p.money.length ? `<div class="money">${p.money.map(esc).join("\n")}</div>` : ""}
      </div>`).join("");
    box.querySelectorAll(".ap-card").forEach((card) => {
      const api = card.dataset.svc === "social" ? social : meta;
      const decide = async (ok) => { card.querySelectorAll("button").forEach((b) => (b.disabled = true)); try { await api("/approve", { method: "POST", body: JSON.stringify({ id: card.dataset.id, approve: ok }) }); } catch (e) {} renderApprovals(); };
      card.querySelector(".ok").onclick = () => decide(true);
      card.querySelector(".no").onclick = () => decide(false);
    });
  }
  function loopApprovals() { clearTimeout(AP.timer); renderApprovals(); AP.timer = setTimeout(loopApprovals, main.dataset.view === "approvals" ? 1500 : 6000); }

  // ---------------------------------------------------------------- connections
  const GUIDE_URL = "https://claude.ai/code/artifact/9f1c3d62-cd00-4694-b08b-ebce0c1b9a5c";
  function connections(so, me) {
    const st = (ok, warn) => ok ? ["ok", "Connected"] : warn ? ["warn", warn] : ["off", "Not connected"];
    return [
      { id: "ai", title: "AI assistants", desc: "Claude runs the agents; OpenAI writes images and, when picked, text.", where: "ANTHROPIC_API_KEY · OPENAI_API_KEY", state: st(!!(so && so.openai_key), so ? null : "Service offline"), sub: so && so.openai_key ? "Claude and OpenAI connected" : "OpenAI key missing", icon: "spark", actions: [] },
      { id: "ads", title: "Meta Ads sandbox", desc: "Read any ad account the token sees; create and edit only on the sandbox.", where: "META_ACCESS_TOKEN · META_WRITE_ACCOUNT_IDS", state: st(me && me.mode === "live" && me.write_accounts.length), sub: me && me.mode === "live" ? `Sandbox ${me.write_accounts.join(", ")}` : "Token not set", icon: "chart", actions: [["Guide", "meta-ads-agent/SETUP.md", null]] },
      { id: "social", title: "Facebook & Instagram", desc: "A Page and a professional Instagram account the publisher may post to.", where: "SOCIAL_META_USER_TOKEN · SOCIAL_PUBLISH_TARGETS", state: st(so && so.mode === "live"), sub: so && so.mode === "live" ? so.publish_targets.join(", ") : "Running on a fake Page", icon: "ig", actions: [["Connect", GUIDE_URL, "link"]] },
      { id: "media", title: "Media hosting", desc: "Instagram needs media at a public URL; local files are uploaded to Cloudflare R2.", where: "R2_ENDPOINT · R2_BUCKET · R2_PUBLIC_BASE_URL", state: st(so && so.media_configured && so.mode === "live", so && so.mode !== "live" ? "Fake in dry run" : null), sub: so && so.mode !== "live" ? "Fake hosting while in dry run" : "R2 bucket", icon: "db", actions: [["Set up", "social-agent/SETUP.md · part 4", null]] },
    ];
  }
  const ICON = {
    spark: '<svg viewBox="0 0 24 24"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/></svg>',
    chart: '<svg viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
    ig: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1" fill="currentColor"/></svg>',
    db: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
  };
  window.dmtConnections = connections; window.dmtIcon = ICON;
  async function renderConnections() {
    const [so, me] = await Promise.all([safe(social("/status")), safe(meta("/status"))]);
    const box = $("#cx-grid"); if (!box) return;
    box.innerHTML = connections(so, me).map((c) => `<div class="card cx">
        <div class="ico ${c.icon === "ig" ? "ig" : ""}">${ICON[c.icon]}</div>
        <div><h4>${esc(c.title)} <span class="st ${c.state[0]}">${esc(c.state[1])}</span></h4><p>${esc(c.desc)}</p><div class="where">${esc(c.where)}</div>
          <div class="row2">${c.actions.map(([label, target, kind]) => kind === "link" ? `<a class="btn alt sm" href="${esc(target)}" target="_blank" rel="noopener">${esc(label)} ↗</a>` : `<span class="btn alt sm" title="Open this file in the repo">${esc(label)}: <code style="margin-left:6px">${esc(target)}</code></span>`).join("")}</div>
        </div></div>`).join("");
  }

  // ---------------------------------------------------------------- settings
  async function renderSettings() {
    const [so, me, pr] = await Promise.all([safe(social("/status")), safe(meta("/status")), safe(social("/pricing")) || safe(meta("/pricing"))]);
    const box = $("#st-list"); if (!box) return;
    const row = (k, v) => `<li><span class="k">${esc(k)}</span><span class="v">${v}</span></li>`;
    box.innerHTML = [
      row("Workspace", "client <code>" + esc((so && so.client) || (me && me.client) || "agency") + "</code>"),
      row("Default model (pickers pre-select)", `<code>${esc((so && so.model) || (me && me.model) || "—")}</code> · effort <code>${esc((so && so.effort) || "—")}</code> · change with CLAUDE_MODEL / CLAUDE_EFFORT`),
      row("Monthly API cap", `$${so ? so.monthly_cap_usd.toFixed(0) : "—"} · MONTHLY_API_CAP_USD`),
      row("Publishing cap", `${so ? so.max_posts_per_day : "—"} posts / day · SOCIAL_MAX_POSTS_PER_DAY`),
      row("Image cap", `${so ? so.max_images_per_day : "—"} images / day · ${so ? esc(so.image_model) : ""} · SOCIAL_MAX_IMAGES_PER_DAY`),
      row("Ads budget cap", `${me ? (me.max_daily_budget_minor / 100).toFixed(2) : "—"} per day (minor units) · MAX_DAILY_BUDGET_MINOR`),
      row("Price book", pr ? `checked ${esc(pr.checked)} · <code>pricing.json</code>` : "—"),
      row("Services", `Social Publisher :4412 · Meta Ads :4411 · started by <code>npm start</code>`),
      row("Where keys live", `<code>.env</code> in the hub root — one file for every tool`),
    ].join("");
  }

  // ---------------------------------------------------------------- boot
  new MutationObserver(() => {
    const v = main.dataset.view;
    if (v === "approvals") loopApprovals();
    if (v === "connections") renderConnections();
    if (v === "settings") renderSettings();
  }).observe(main, { attributes: true, attributeFilter: ["data-view"] });
  refreshMode(); loopApprovals(); setInterval(refreshMode, 30000);
  if (main.dataset.view === "connections") renderConnections();
  if (main.dataset.view === "settings") renderSettings();
})();
