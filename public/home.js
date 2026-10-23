// Overview: greeting, the create prompt, KPI strip, workspace lists, setup rail.
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const { esc } = AgentChat;
  const social = AgentChat.api("/api/social"), meta = AgentChat.api("/api/meta");
  const safe = async (p) => { try { return await p; } catch (e) { return null; } };
  const usd = (v) => `$${Number(v || 0).toFixed(v >= 1 ? 2 : v > 0 ? 3 : 0)}`;
  const fmtTime = (ts) => new Date(typeof ts === "number" ? ts * 1000 : ts).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  const S = { tab: "activity", posts: [], logs: [] };

  function goto(view, pane, prompt) {
    window.dmtShow(view);
    if (pane) window.dmtPane(view, pane);
    const chat = view === "social" ? window.dmtSocialChat : view === "meta" ? window.dmtMetaChat : null;
    if (prompt && chat) setTimeout(() => chat.fill(prompt), 80);
  }
  function route(text) {
    const ads = /\b(ad|ads|campaign|budget|cpc|ctr|spend|audience|קמפיין|מודעה|תקציב)\b/i.test(text);
    const kw = /\b(keyword|keywords|seo|search volume|מילות מפתח)\b/i.test(text);
    if (kw) { goto("seo"); const t = $("#kw-input"); if (t) { t.value = text; t.focus(); } return; }
    goto(ads ? "meta" : "social", "chat", text);
  }

  async function load() {
    const hr = new Date().getHours();
    $("#hm-greet").textContent = (hr < 5 ? "Working late." : hr < 12 ? "Good morning." : hr < 18 ? "Good afternoon." : "Good evening.");
    const [so, me, sum, posts, slog, mlog, st, mt] = await Promise.all([
      safe(social("/status")), safe(meta("/status")), safe(social("/ledger/summary")) || safe(meta("/ledger/summary")),
      safe(social("/posts")), safe(social("/log?n=10")), safe(meta("/log?n=10")), safe(social("/turn")), safe(meta("/turn")),
    ]);
    S.posts = posts ? posts.rows : [];
    const drafts = S.posts.filter((p) => p.status === "draft");
    const scheduled = S.posts.filter((p) => p.status === "scheduled");

    // ---- KPI strip
    $("#kpi-drafts").textContent = drafts.length;
    $("#kpi-scheduled").textContent = scheduled.length;
    $("#kpi-approvals").textContent = (st && st.pending ? 1 : 0) + (mt && mt.pending ? 1 : 0);
    $("#kpi-spend").innerHTML = `${sum ? usd(sum.total) : "$0"} <small>/ $${sum ? sum.monthly_cap_usd.toFixed(0) : "—"}</small>`;

    // ---- workspace lists
    S.logs = [...(slog ? slog.rows.map((r) => ({ ...r, svc: "Social" })) : []), ...(mlog ? mlog.rows.map((r) => ({ ...r, svc: "Ads" })) : [])]
      .sort((a, b) => (b.ts || "").localeCompare(a.ts || "")).slice(0, 8);
    renderWorkspace();

    // ---- setup rail (shares its definitions with the Connections page)
    const items = window.dmtConnections ? window.dmtConnections(so, me) : [];
    const checks = [
      ["Claude key", true], ["OpenAI key", !!(so && so.openai_key)], ["Meta ads token", !!(me && me.mode === "live")],
      ["Sandbox ad account", !!(me && me.write_accounts && me.write_accounts.length)], ["Facebook + Instagram", !!(so && so.mode === "live")],
      ["Media hosting", !!(so && so.media_configured && so.mode === "live")],
    ];
    const n = checks.filter((c) => c[1]).length;
    $("#ov-setup-count").textContent = `${n} of ${checks.length} connected`;
    $("#ov-progress").innerHTML = checks.map((c) => `<i class="${c[1] ? "on" : ""}"></i>`).join("");
    $("#ov-setup").innerHTML = items.map((c) => {
      const right = c.state[0] === "ok" ? `<span class="done">✓</span>`
        : c.actions.length && c.actions[0][2] === "link" ? `<a class="btn alt sm" href="${esc(c.actions[0][1])}" target="_blank" rel="noopener">${esc(c.actions[0][0])}</a>`
        : `<a class="link" data-go="connections">${esc(c.actions.length ? c.actions[0][0] : "Details")}</a>`;
      return `<li><span class="ico ${c.icon === "ig" ? "ig" : ""}">${window.dmtIcon[c.icon]}</span><span><b>${esc(c.title)}</b><small>${esc(c.sub)}</small></span>${right}</li>`;
    }).join("");
    $("#ov-setup").querySelectorAll("[data-go]").forEach((a) => (a.onclick = () => window.dmtShow(a.dataset.go)));
  }

  function renderWorkspace() {
    const box = $("#ov-ws");
    let rows = [];
    if (S.tab === "activity") rows = S.logs.map((e) => ({ t: `${e.svc} · ${e.action}${e.account || e.target ? " · " + (e.account || e.target) : ""}`, s: e.note ? String(e.note).slice(0, 90) : (e.approved === false ? "refused" : e.approved ? "approved" : ""), ts: e.ts }));
    if (S.tab === "drafts") rows = S.posts.filter((p) => p.status === "draft").map((p) => ({ t: (p.text || "").slice(0, 90) || `(${p.kind})`, s: `${p.target_name} · ${p.kind}`, ts: p.created_at, go: "social" }));
    if (S.tab === "scheduled") rows = S.posts.filter((p) => p.status === "scheduled").map((p) => ({ t: (p.text || "").slice(0, 90) || `(${p.kind})`, s: `${p.target_name} · goes out ${fmtTime(p.scheduled_at)}`, ts: p.scheduled_at, go: "social" }));
    if (!rows.length) {
      const msgs = { activity: ["A fresh start for your marketing", "Create your first draft and it will appear here.", "Create a draft"], drafts: ["No drafts yet", "Ask the agent for one, or write it by hand in the Social Publisher.", "Create a draft"], scheduled: ["Nothing scheduled", "Schedule a draft and it will wait here until it goes out.", "Open the calendar"] };
      const [h, p, cta] = msgs[S.tab];
      box.innerHTML = `<div class="ov-empty">
        <svg viewBox="0 0 120 100" fill="none"><rect x="30" y="18" width="52" height="66" rx="6" fill="#eef4ff" stroke="#c7d7fe"/><rect x="40" y="10" width="52" height="66" rx="6" fill="#fff" stroke="#c7d7fe"/><path d="M50 30h32M50 40h32M50 50h22" stroke="#c7d7fe" stroke-width="3" stroke-linecap="round"/><path d="M100 8l3 8M112 14l-8 3M104 24l6 6" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round"/></svg>
        <h4>${h}</h4><p>${p}</p><a class="link" id="ov-cta">${cta} →</a></div>`;
      $("#ov-cta").onclick = () => S.tab === "scheduled" ? goto("social", "calendar") : goto("social", "chat", "Draft a short Facebook post introducing our agency. Hebrew, friendly, one call to action.");
      return;
    }
    box.innerHTML = `<ul class="ov-list">${rows.map((r) => `<li ${r.go ? `data-go="${r.go}"` : ""}><span><div class="t" dir="auto">${esc(r.t)}</div><div class="s">${esc(r.s)}</div></span><time>${r.ts ? fmtTime(r.ts) : ""}</time></li>`).join("")}</ul>`;
    box.querySelectorAll("[data-go]").forEach((li) => (li.onclick = () => goto(li.dataset.go, "calendar")));
  }

  // ---- wiring
  $("#ov-go").onclick = () => { const t = $("#ov-input").value.trim(); if (t) { $("#ov-input").value = ""; route(t); } };
  $("#ov-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ov-go").click(); } });
  $("#ov-quick").querySelectorAll("button").forEach((b) => (b.onclick = () => b.dataset.q ? goto(b.dataset.view, b.dataset.pane || null, b.dataset.q) : goto(b.dataset.view)));
  $("#ov-tabs").querySelectorAll("button").forEach((b) => (b.onclick = () => { S.tab = b.dataset.tab; $("#ov-tabs").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b)); renderWorkspace(); }));
  $("#ov-promo-link").onclick = () => goto("social", "chat");

  const main = document.querySelector("main");
  new MutationObserver(() => { if (main.dataset.view === "home") load(); }).observe(main, { attributes: true, attributeFilter: ["data-view"] });
  if (main.dataset.view === "home") load();
})();
