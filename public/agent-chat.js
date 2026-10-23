// Shared chat + approval panel for the Python agent services.
// Usage:  const chat = AgentChat({ base: "/api/meta", messagesEl, inputEl, sendBtn, bannerEl,
//                                  newBtn, retryBtn, suggestEl, onStatus(status), onIdle(snap), onOffline(e) });
//         chat.boot();
// Exposes AgentChat.esc / AgentChat.md / AgentChat.api helpers for the views.
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const num = (v, d = 0) => v == null || v === "" ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d });

  // Tiny markdown: paragraphs, bullets, **bold**, `code`. Input is escaped first.
  function md(text) {
    const lines = esc(text).split(/\r?\n/);
    let html = "", inList = false, para = [];
    const flush = () => { if (para.length) { html += `<p>${para.join("<br>")}</p>`; para = []; } };
    for (const raw of lines) {
      const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
      const m = line.match(/^\s*(?:[-•*]|\d+[.)])\s+(.*)$/);
      if (m) { flush(); if (!inList) { html += "<ul>"; inList = true; } html += `<li>${m[1]}</li>`; continue; }
      if (inList) { html += "</ul>"; inList = false; }
      if (!line.trim()) { flush(); continue; }
      para.push(line);
    }
    if (inList) html += "</ul>";
    flush();
    return html;
  }

  function makeApi(base) {
    return async function api(path, opts = {}) {
      const headers = opts.body instanceof FormData ? {} : { "content-type": "application/json" };
      const r = await fetch(base + path, { headers, ...opts });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { const e = new Error(j.error || r.statusText); e.status = r.status; e.hint = j.hint; throw e; }
      return j;
    };
  }

  function AgentChat(o) {
    const api = makeApi(o.base);
    const S = { rendered: new Set(), busy: false, polling: null, retry: null, booted: false, status: null };
    const messages = o.messagesEl, input = o.inputEl, sendBtn = o.sendBtn, banner = o.bannerEl;

    function setBanner(text) { if (!banner) return; banner.textContent = text || ""; banner.style.display = text ? "block" : "none"; }
    function scrollDown() { messages.scrollTop = messages.scrollHeight; }

    function renderEvent(ev) {
      if (ev.type === "text") { const n = el("div", "ma-narr"); n.dir = "auto"; n.textContent = ev.text; return n; }
      if (ev.type === "tool") {
        const args = Object.entries(ev.input || {}).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ");
        const n = el("div", "ma-chip", `⚙ ${ev.name} ${args}`); n.title = `${ev.name} ${args}`; return n;
      }
      if (ev.type === "refused") return el("div", "ma-chip refused", `✕ ${ev.action}: ${ev.note}`);
      if (ev.type === "approval") {
        const n = el("div", "ma-approval");
        n.innerHTML = `<div class="ma-ap-title">Approve this action?</div>
          <div><b>${esc(ev.action)}</b> → <b>${esc(ev.account)}</b></div>
          <pre>${esc(JSON.stringify(ev.details, null, 2))}</pre>
          ${ev.money && ev.money.length ? `<div class="ma-money">${ev.money.map(esc).join("<br>")}</div>` : ""}
          <div class="ma-ap-actions"><button class="ma-btn ok">Approve</button><button class="ma-btn no">Decline</button></div>
          <div class="ma-decided"></div>`;
        n.querySelector(".ok").onclick = () => decide(ev.id, true, n);
        n.querySelector(".no").onclick = () => decide(ev.id, false, n);
        updateApproval(n, ev);
        return n;
      }
      return el("div", "ma-chip", JSON.stringify(ev));
    }

    function updateApproval(node, ev) {
      const done = ev.decision !== null && ev.decision !== undefined;
      node.querySelectorAll("button").forEach((b) => (b.disabled = done));
      const d = node.querySelector(".ma-decided");
      d.className = "ma-decided " + (ev.decision ? "yes" : "no");
      d.textContent = !done ? "" : ev.decision ? "✓ Approved" : ev.timed_out ? "✕ Timed out (treated as declined)" : "✕ Declined";
    }

    async function decide(id, approve, node) {
      node.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try { await api("/approve", { method: "POST", body: JSON.stringify({ id, approve }) }); }
      catch (e) { node.querySelector(".ma-decided").textContent = e.message; }
      poll();
    }

    function renderTurns(snap) {
      let empty = messages.querySelector(".ma-empty-chat");
      if (!snap.turns.length) {
        if (!empty && o.emptyHint) { empty = el("div", "ma-empty-chat"); empty.innerHTML = o.emptyHint; messages.appendChild(empty); }
      } else if (empty) empty.remove();
      for (const t of snap.turns) {
        if (!S.rendered.has(t.id)) {
          const u = el("div", "ma-msg user"); u.dir = "auto"; u.textContent = t.text;
          messages.appendChild(u); S.rendered.add(t.id);
        }
        for (const [i, ev] of t.events.entries()) {
          const key = `${t.id}:${i}`;
          let node = messages.querySelector(`[data-ev="${key}"]`);
          if (!node) { node = renderEvent(ev); node.dataset.ev = key; messages.appendChild(node); }
          else if (ev.type === "approval") updateApproval(node, ev);
        }
        const doneKey = `${t.id}:done`;
        if (t.status !== "running" && !messages.querySelector(`[data-ev="${doneKey}"]`)) {
          let node;
          if (t.status === "error") node = el("div", "ma-msg error", t.error);
          else { node = el("div", "ma-msg agent"); node.dir = "auto"; node.innerHTML = md(t.reply || "(no reply)"); }
          node.dataset.ev = doneKey; messages.appendChild(node);
          if (t.usage) {
            const c = el("div", "ma-cost", `${t.model ? t.model + " · " : ""}${num(t.usage.input + t.usage.cache_read + t.usage.cache_write)} in / ${num(t.usage.output)} out · $${t.usage.usd.toFixed(4)}`);
            c.dataset.ev = doneKey + ":cost"; messages.appendChild(c);
          }
        }
      }
      const running = snap.current && snap.current.status === "running";
      let typing = messages.querySelector(".ma-typing");
      if (running && !snap.pending) { if (!typing) typing = el("div", "ma-typing", "thinking"); messages.appendChild(typing); }
      else if (typing) typing.remove();
      sendBtn.disabled = running; input.disabled = running;
      if (o.onUsage) o.onUsage(snap.session_usd || 0);
      scrollDown();
      return running || !!snap.pending;
    }

    async function poll() {
      let snap;
      try { snap = await api("/turn"); setBanner(""); }
      catch (e) { offline(e); return; }
      const wasBusy = S.busy;
      const busy = renderTurns(snap);
      S.busy = busy;
      clearTimeout(S.polling);
      if (busy) S.polling = setTimeout(poll, 700);
      else if (wasBusy && o.onIdle) o.onIdle(snap);
    }

    function offline(e) {
      S.status = null;
      setBanner(e.status === 503 ? `${e.message}. ${e.hint || ""}` : `Cannot reach the agent: ${e.message}`);
      sendBtn.disabled = true;
      if (o.onOffline) o.onOffline(e);
      clearTimeout(S.retry);
      S.retry = setTimeout(() => { S.booted = false; boot(); }, 3000);
    }

    // ---- model picker: the operator chooses; the .env value is only the starting point
    const storeKey = "dmt-model:" + o.base;
    function chosenModel() { try { return localStorage.getItem(storeKey) || ""; } catch (e) { return ""; } }
    async function renderModelPicker() {
      if (!o.modelEl) return;
      let rows = [];
      try { rows = (await api("/models?kind=text")).rows; } catch (e) { return; }
      const current = chosenModel() || (S.status && ("anthropic/" + S.status.model)) || "";
      o.modelEl.innerHTML = rows.map((m) => {
        const p = m.per_million || {};
        const price = "$" + p.input + " / $" + p.output + " per 1M";
        return `<option value="${esc(m.key)}" ${m.available ? "" : "disabled"} ${m.key === current ? "selected" : ""}>${esc(m.label)} · ${price}${m.available ? "" : " · soon"}</option>`;
      }).join("");
      if (!o.modelEl.value && rows.length) o.modelEl.value = (rows.find((m) => m.available) || rows[0]).key;
      o.modelEl.onchange = () => { try { localStorage.setItem(storeKey, o.modelEl.value); } catch (e) {} };
    }

    async function send(textOverride) {
      const text = (textOverride ?? input.value).trim();
      if (!text) return;
      input.value = "";
      const model = o.modelEl ? o.modelEl.value : undefined;
      try { await api("/chat", { method: "POST", body: JSON.stringify({ text, model }) }); }
      catch (e) {
        if (e.status === 409) setBanner("A turn is already running.");
        else if (e.status === 402 || e.status === 400) setBanner(e.message);
        else offline(e);
        return;
      }
      poll();
    }

    async function reset() {
      try { await api("/reset", { method: "POST" }); messages.innerHTML = ""; S.rendered.clear(); poll(); }
      catch (e) { setBanner(e.message); }
    }

    async function boot() {
      if (S.booted) return; S.booted = true;
      try { S.status = await api("/status"); setBanner(""); sendBtn.disabled = false; }
      catch (e) { offline(e); return false; }
      if (o.onStatus) await o.onStatus(S.status);
      await Promise.all([renderModelPicker(), poll()]);
      return true;
    }

    sendBtn.onclick = () => send();
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
    if (o.newBtn) o.newBtn.onclick = reset;
    if (o.retryBtn) o.retryBtn.onclick = () => { S.booted = false; boot(); };
    if (o.suggestEl) o.suggestEl.querySelectorAll("button[data-q]").forEach((b) => (b.onclick = () => { input.value = b.dataset.q; input.focus(); }));

    return { api, boot, poll, send, reset, setBanner, state: S, fill: (q) => { input.value = q; input.focus(); } };
  }

  AgentChat.esc = esc; AgentChat.md = md; AgentChat.num = num; AgentChat.api = makeApi; AgentChat.el = el;
  window.AgentChat = AgentChat;

  // Sidebar: any .tool[data-view] switches <main data-view>; views boot lazily via a MutationObserver.
  // Tool tabs: .tool-tabs button[data-pane] sets data-pane on the enclosing section; CSS shows one pane.
  const main = document.querySelector("main");
  const links = () => document.querySelectorAll("aside .tool[data-view]");
  let activeLink = null;
  function show(view, link) {
    main.dataset.view = view || "home";
    activeLink = link || [...links()].find((a) => a.dataset.view === main.dataset.view && !a.dataset.pane) || null;
    links().forEach((a) => a.classList.toggle("active", a === activeLink));
    try { localStorage.setItem("dmt-view", main.dataset.view); } catch (e) {}
    window.scrollTo({ top: 0 });
  }
  function setPane(viewId, pane) {
    const section = document.getElementById("view-" + viewId);
    if (!section) return;
    section.dataset.pane = pane;
    section.querySelectorAll(".tool-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.pane === pane));
  }
  window.dmtShow = show;
  window.dmtPane = setPane;
  document.addEventListener("DOMContentLoaded", () => {
    links().forEach((a) => a.addEventListener("click", () => { show(a.dataset.view, a); if (a.dataset.pane) setPane(a.dataset.view, a.dataset.pane); }));
    document.querySelectorAll(".tool-tabs button[data-pane]").forEach((b) => b.addEventListener("click", () => setPane(b.closest("section").id.replace("view-", ""), b.dataset.pane)));
    let saved = null; try { saved = localStorage.getItem("dmt-view"); } catch (e) {}
    show(saved && document.getElementById("view-" + saved) ? saved : "home");
  });
})();
