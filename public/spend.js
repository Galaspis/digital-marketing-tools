// Spend & models: one ledger across services, read through whichever service answers.
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const { esc, num } = AgentChat;
  const BASES = ["/api/social", "/api/meta"];
  const usd = (v) => `$${Number(v || 0).toFixed(v >= 1 ? 2 : 4)}`;
  const S = { month: null, booted: false };

  async function ask(path) {
    let lastErr = null;
    for (const base of BASES) {
      try { return await AgentChat.api(base)(path); } catch (e) { lastErr = e; }
    }
    throw lastErr;
  }

  function fill(tableSel, rows, cells) {
    const body = $(tableSel + " tbody");
    body.innerHTML = rows.length ? rows.map((r) => `<tr>${cells(r).map((c, i) => `<td class="${typeof c === "number" || /^\$|^\d/.test(String(c)) ? "ma-num" : ""}">${c}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="6" class="ma-empty">Nothing yet.</td></tr>`;
  }

  async function load() {
    let s;
    try { s = await ask(`/ledger/summary${S.month ? `?month=${S.month}` : ""}`); $("#sp-banner").style.display = "none"; }
    catch (e) { $("#sp-banner").textContent = `Cannot read the ledger: ${e.message}`; $("#sp-banner").style.display = "block"; return; }
    S.month = s.month;
    const sel = $("#sp-month");
    const months = s.months.includes(s.month) ? s.months : [s.month, ...s.months];
    sel.innerHTML = months.map((m) => `<option value="${m}" ${m === s.month ? "selected" : ""}>${m}</option>`).join("");
    $("#sp-total").textContent = usd(s.total);
    const pct = s.monthly_cap_usd ? Math.min(100, (s.total / s.monthly_cap_usd) * 100) : 0;
    $("#sp-capline").innerHTML = `of <b>$${s.monthly_cap_usd.toFixed(0)}</b> monthly cap · ${pct.toFixed(1)}% used${s.cap_reached ? " · <b style=\"color:var(--red)\">cap reached, paid actions refused</b>" : ""}`;
    const bar = $("#sp-bar-fill"); bar.style.width = pct + "%"; bar.className = pct >= 100 ? "over" : pct >= 80 ? "warn" : "";
    $("#sp-head").innerHTML = `${s.month} · <b>${usd(s.total)}</b> of $${s.monthly_cap_usd.toFixed(0)}`;

    fill("#sp-model", s.by_model, (r) => [`${esc(r.provider)} · <b>${esc(r.model)}</b>`, num(r.n), `${num(r.tokens_in)} / ${num(r.tokens_out)}${r.images ? ` · ${r.images} img` : ""}`, usd(r.usd)]);
    fill("#sp-service", s.by_service, (r) => [esc(r.service), num(r.n), usd(r.usd)]);
    fill("#sp-action", s.by_action, (r) => [esc(r.action), num(r.n), usd(r.usd)]);
    fill("#sp-day", s.by_day, (r) => [esc(r.day), num(r.n), usd(r.usd)]);
    fill("#sp-post", s.per_post, (r) => [`<span class="sp-mono">${esc(r.post_id)}</span>`, num(r.n), num(r.images), usd(r.usd)]);
    fill("#sp-campaign", s.per_campaign, (r) => [`<span class="sp-mono">${esc(r.campaign_id)}</span>`, num(r.n), usd(r.usd)]);

    try {
      const rows = (await ask(`/ledger/rows?month=${s.month}&limit=40`)).rows;
      fill("#sp-rows", rows, (r) => [new Date(r.ts).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }), esc(r.service), esc(r.action), esc(r.model),
        `<span class="sp-mono">${esc(r.post_id ? "post " + r.post_id : r.campaign_id ? "campaign " + r.campaign_id : r.ref_id || "")}</span>`, usd(r.usd)]);
    } catch (e) {}
  }

  async function loadPricing() {
    try {
      const p = await ask("/pricing");
      $("#sp-checked").textContent = `checked ${p.checked}`;
      fill("#sp-pricing", p.models, (m) => {
        const pm = m.per_million || {};
        const inP = pm.input ?? pm.text_input, outP = pm.output ?? pm.image_output, cr = pm.cache_read;
        return [`${esc(m.label)} <span class="sp-mono">${esc(m.key)}</span>`, esc(m.kind), inP != null ? `$${inP}` : "—", outP != null ? `$${outP}` : "—", cr != null ? `$${cr}` : "—",
          `<a href="${esc(m.source)}" target="_blank" rel="noopener">source ↗</a>`];
      });
    } catch (e) {}
  }

  $("#sp-month").onchange = (e) => { S.month = e.target.value; load(); };
  $("#sp-refresh").onclick = () => { load(); loadPricing(); };

  const main = document.querySelector("main");
  function boot() { if (!S.booted) { S.booted = true; loadPricing(); } load(); }
  new MutationObserver(() => { if (main.dataset.view === "spend") boot(); }).observe(main, { attributes: true, attributeFilter: ["data-view"] });
  if (main.dataset.view === "spend") boot();
})();
