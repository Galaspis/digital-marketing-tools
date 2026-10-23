// Meta Ads Agent view: workspace (accounts, campaigns, insights) around the shared chat panel.
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const { esc, num } = AgentChat;
  const S = { status: null, accounts: [], account: null, campaigns: [], campaign: null, currency: "" };

  const chat = AgentChat({
    base: "/api/meta",
    messagesEl: $("#ma-messages"), inputEl: $("#ma-input"), sendBtn: $("#ma-send"), bannerEl: $("#ma-banner"),
    newBtn: $("#ma-new"), retryBtn: $("#ma-retry"), suggestEl: $("#ma-suggest"), modelEl: $("#ma-model"),
    emptyHint: "<b>Ask about your ads</b>What is in the account, what is wasting money, or create a test campaign on the sandbox. Writes always ask you first.",
    onStatus: async (status) => { S.status = status; renderHeader(); await Promise.all([loadAccounts(), loadLog()]); },
    onIdle: () => { loadLog(); refreshCampaigns(); },
    onOffline: () => { S.status = null; renderHeader(); },
    onUsage: (usd) => { const b = $("#ma-session-usd"); if (b) b.textContent = `$${Number(usd).toFixed(4)}`; },
  });
  const api = chat.api;
  window.dmtMetaChat = chat;

  const money = (minor) => minor == null || minor === "" ? "—" : `${(Number(minor) / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${S.currency}`.trim();
  const fmtTime = (ts) => new Date(typeof ts === "number" ? ts * 1000 : ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  function renderHeader() {
    const h = $("#ma-head");
    if (!S.status) { h.innerHTML = `<span class="ma-badge off">offline</span>`; return; }
    const live = S.status.mode === "live";
    h.innerHTML = `<span class="ma-badge ${live ? "live" : "dry"}">${live ? "live" : "dry run"}</span>` +
      ` Writes allowed on <b>${esc(S.status.write_accounts.join(", ") || "none")}</b> · cap <b>${money(S.status.max_daily_budget_minor)}/day</b>` +
      ` · AI session <b id="ma-session-usd">$${Number(S.status.session_usd || 0).toFixed(4)}</b>`;
  }

  async function loadAccounts() {
    const sel = $("#ma-account");
    sel.innerHTML = `<option>loading…</option>`;
    try { S.accounts = (await api("/accounts")).rows; }
    catch (e) { sel.innerHTML = `<option>error</option>`; chat.setBanner(e.message); return; }
    sel.innerHTML = S.accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.name || a.id)} · ${esc(a.currency || "")}${a.writable ? " · writable" : " · read-only"}</option>`).join("") || `<option value="">no accounts</option>`;
    const keep = S.account && S.accounts.find((a) => a.id === S.account) ? S.account : (S.accounts[0] && S.accounts[0].id);
    sel.value = keep || "";
    selectAccount(keep);
  }

  function selectAccount(id) {
    S.account = id || null;
    const a = S.accounts.find((x) => x.id === id);
    S.currency = a ? a.currency || "" : "";
    S.campaign = null;
    renderDetail(null);
    refreshCampaigns();
  }

  async function refreshCampaigns() {
    const body = $("#ma-campaigns tbody");
    if (!S.account) { body.innerHTML = `<tr><td colspan="4" class="ma-empty">No account</td></tr>`; return; }
    body.innerHTML = `<tr><td colspan="4" class="ma-empty">loading… ${S.status && S.status.mode === "live" ? "(Meta's development tier allows one call per 30 s, so this can take a moment)" : ""}</td></tr>`;
    try { S.campaigns = (await api(`/campaigns?account_id=${encodeURIComponent(S.account)}${$("#ma-archived").checked ? "&include_archived=1" : ""}`)).rows; }
    catch (e) { body.innerHTML = `<tr><td colspan="4" class="ma-empty">${esc(e.message)}</td></tr>`; return; }
    if (!S.campaigns.length) { body.innerHTML = `<tr><td colspan="4" class="ma-empty">No campaigns in this account yet. Ask the agent to create one.</td></tr>`; return; }
    body.innerHTML = S.campaigns.map((c) => `<tr data-id="${esc(c.id)}" class="${S.campaign === c.id ? "sel" : ""}">
        <td><div>${esc(c.name)}</div><div class="ma-lock">${esc(c.id)}</div></td>
        <td><span class="ma-pill ${esc(c.effective_status || c.status)}">${esc(c.effective_status || c.status)}</span></td>
        <td>${esc((c.objective || "").replace("OUTCOME_", "").toLowerCase())}</td>
        <td class="ma-num">${c.daily_budget ? money(c.daily_budget) + "/day" : c.lifetime_budget ? money(c.lifetime_budget) + " total" : "—"}</td>
      </tr>`).join("");
    body.querySelectorAll("tr[data-id]").forEach((tr) => (tr.onclick = () => selectCampaign(tr.dataset.id)));
    if (S.campaign && !S.campaigns.find((c) => c.id === S.campaign)) renderDetail(null);
  }

  function selectCampaign(id) {
    S.campaign = id;
    $("#ma-campaigns tbody").querySelectorAll("tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.id === id));
    renderDetail(S.campaigns.find((c) => c.id === id));
  }

  async function renderDetail(c) {
    const box = $("#ma-detail");
    if (!c) { box.innerHTML = `<div class="ma-empty">Select a campaign to see its ad sets and numbers.</div>`; return; }
    const preset = $("#ma-preset").value;
    box.innerHTML = `<div class="ma-detail-title">${esc(c.name)}</div><div class="ma-empty">loading…</div>`;
    let insights = [], adsets = [], err = "";
    try {
      insights = (await api(`/insights?object_type=campaign&object_id=${encodeURIComponent(c.id)}&date_preset=${preset}`)).rows;
      adsets = (await api(`/adsets?campaign_id=${encodeURIComponent(c.id)}`)).rows;
    } catch (e) { err = e.message; }
    if (S.campaign !== c.id) return;
    const r = insights[0] || {};
    const act = (type) => { const a = (r.actions || []).find((x) => x.action_type === type); return a ? a.value : null; };
    const kpis = [["Spend", r.spend != null ? `${num(r.spend, 2)} ${S.currency}` : "—"], ["Impressions", num(r.impressions)], ["Reach", num(r.reach)],
      ["Clicks", num(r.clicks)], ["CTR", r.ctr != null ? num(r.ctr, 2) + "%" : "—"], ["CPC", r.cpc != null ? num(r.cpc, 2) : "—"],
      ["CPM", r.cpm != null ? num(r.cpm, 2) : "—"], ["Frequency", r.frequency != null ? num(r.frequency, 2) : "—"], ["Leads", num(act("lead"))]];
    box.innerHTML = `<div class="ma-detail-title">${esc(c.name)} <span class="ma-lock">· ${esc(preset.replace(/_/g, " "))}</span></div>
      ${err ? `<div class="ma-empty">${esc(err)}</div>` : ""}
      ${!insights.length && !err ? `<div class="ma-empty">No delivery data for this range${S.status && S.status.write_accounts.includes(S.account) ? " (sandbox ads never deliver)" : ""}.</div>` : ""}
      <div class="ma-kpis">${kpis.map(([l, v]) => `<div class="ma-kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("")}</div>
      <table class="ma-table"><thead><tr><th>Ad set</th><th>Status</th><th>Goal</th><th class="ma-num">Budget</th></tr></thead><tbody>
        ${adsets.length ? adsets.map((a) => `<tr><td><div>${esc(a.name)}</div><div class="ma-lock">${esc(a.id)} · ${esc(((a.targeting || {}).geo_locations || {}).countries || [])} · ${esc(a.targeting && a.targeting.age_min)}–${esc(a.targeting && a.targeting.age_max)}</div></td>
          <td><span class="ma-pill ${esc(a.effective_status || a.status)}">${esc(a.effective_status || a.status)}</span></td>
          <td>${esc((a.optimization_goal || "").toLowerCase().replace(/_/g, " "))}</td>
          <td class="ma-num">${a.daily_budget ? money(a.daily_budget) + "/day" : a.lifetime_budget ? money(a.lifetime_budget) : "inherits"}</td></tr>`).join("")
        : `<tr><td colspan="4" class="ma-empty">No ad sets.</td></tr>`}
      </tbody></table>
      <div class="ma-suggest" style="padding:12px 16px">
        <button data-q="Analyse campaign &quot;${esc(c.name)}&quot; (id ${esc(c.id)}) for ${esc(preset.replace(/_/g, " "))}: what is working, what is wasting money, what would you change?">Ask Claude to analyse</button>
        <button data-q="Pause campaign ${esc(c.id)} (&quot;${esc(c.name)}&quot;).">Pause it</button>
        <button data-q="Activate campaign ${esc(c.id)} (&quot;${esc(c.name)}&quot;).">Activate it</button>
      </div>`;
    box.querySelectorAll(".ma-suggest button").forEach((b) => (b.onclick = () => chat.fill(b.dataset.q)));
  }

  async function loadLog() {
    const ul = $("#ma-log");
    try {
      const rows = (await api("/log?n=12")).rows.slice().reverse();
      ul.innerHTML = rows.length ? rows.map((e) => `<li><time>${fmtTime(e.ts)}</time><span class="${e.approved === false ? "ko" : e.approved ? "ok" : ""}">${e.approved === false ? "✕" : e.approved ? "✓" : "·"}</span><span>${esc(e.action)} ${esc(e.account || "")}${e.note ? " — " + esc(e.note) : ""}</span></li>`).join("")
        : `<li class="ma-empty">Nothing yet.</li>`;
    } catch (e) { ul.innerHTML = `<li class="ma-empty">${esc(e.message)}</li>`; }
  }

  $("#ma-account").onchange = (e) => selectAccount(e.target.value);
  $("#ma-archived").onchange = refreshCampaigns;
  $("#ma-refresh").onclick = () => { refreshCampaigns(); loadLog(); };
  $("#ma-preset").onchange = () => renderDetail(S.campaigns.find((c) => c.id === S.campaign));

  const main = document.querySelector("main");
  new MutationObserver(() => { if (main.dataset.view === "meta") chat.boot(); }).observe(main, { attributes: true, attributeFilter: ["data-view"] });
  if (main.dataset.view === "meta") chat.boot();
})();
