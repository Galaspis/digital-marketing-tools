// Social Publisher view: compose form + calendar around the shared chat panel.
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const { esc, el } = AgentChat;
  const S = { status: null, targets: [], posts: [], tab: "draft", editing: null, media: [] };
  const LIMITS = { instagram: 2200, facebook_page: 63206 };
  const KINDS = { facebook_page: ["text", "link", "photo", "video"], instagram: ["photo", "reel", "story", "carousel"] };

  const chat = AgentChat({
    base: "/api/social",
    messagesEl: $("#sa-messages"), inputEl: $("#sa-input"), sendBtn: $("#sa-send"), bannerEl: $("#sa-banner"),
    newBtn: $("#sa-new"), retryBtn: $("#sa-retry"), suggestEl: $("#sa-suggest"), modelEl: $("#sa-model"),
    emptyHint: "<b>Describe the post you want</b>Where it goes, what it says, when. The agent drafts it into the calendar; publishing waits for your approval.",
    onStatus: async (status) => { S.status = status; renderHeader(); await Promise.all([loadTargets(), loadPosts(), loadLog()]); },
    onIdle: () => { loadPosts(); loadLog(); },
    onOffline: () => { S.status = null; renderHeader(); },
    onUsage: (usd) => { const b = $("#sa-session-usd"); if (b) b.textContent = `$${Number(usd).toFixed(4)}`; },
  });
  const api = chat.api;
  window.dmtSocialChat = chat;

  const fmtWhen = (iso) => iso ? new Date(iso).toLocaleString([], { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const fmtTime = (ts) => new Date(typeof ts === "number" ? ts * 1000 : ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const toLocalInput = (iso) => { if (!iso) return ""; const d = new Date(iso); const p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
  const fromLocalInput = (v) => v ? new Date(v).toISOString() : null;
  const isImg = (u) => /\.(jpe?g|png|gif|webp)(\?|$)/i.test(u);
  const preview = (u) => `/api/social/media/preview?u=${encodeURIComponent(u)}`;
  const targetOf = (id) => S.targets.find((t) => t.id === id);

  // ---------------------------------------------------------------- header
  function renderHeader() {
    const h = $("#sa-head");
    const gen = $("#sa-gen");
    if (gen) { gen.style.display = S.status && S.status.images_enabled ? "" : "none"; }
    if (S.status) {
      $("#sa-gen-hint").textContent = `${S.status.image_model} · ${S.status.images_today}/${S.status.max_images_per_day} images today · this month $${Number(S.status.month_usd || 0).toFixed(2)} of $${Number(S.status.monthly_cap_usd || 0).toFixed(0)}`;
      priceQualityOptions();
    }
    if (!S.status) { h.innerHTML = `<span class="ma-badge off">offline</span>`; return; }
    const live = S.status.mode === "live";
    h.innerHTML = `<span class="ma-badge ${live ? "live" : "dry"}">${live ? "live" : "dry run"}</span>` +
      ` Publish allowed to <b>${esc(S.status.publish_targets.join(", ") || "none")}</b> · cap <b>${S.status.max_posts_per_day}/day</b>` +
      ` · media hosting <b>${S.status.media_configured ? "on" : "off"}</b>` +
      ` · AI session <b id="sa-session-usd">$${Number(S.status.session_usd || 0).toFixed(4)}</b>`;
  }

  async function priceQualityOptions() {
    try {
      const models = (await api("/models?kind=image")).rows;
      const m = models.find((x) => x.model === S.status.image_model) || models[0];
      if (!m || !m.typical_output_tokens) return;
      const size = $("#sa-gen-size").value || "1024x1024";
      const [w, h] = size.split("x").map(Number);
      const scale = (w * h) / (1024 * 1024);
      $("#sa-gen-quality").querySelectorAll("option").forEach((opt) => {
        const tok = m.typical_output_tokens[opt.value];
        if (tok) opt.textContent = `${opt.value} · ~$${(tok * scale * m.per_million.image_output / 1e6).toFixed(3)}`;
      });
    } catch (e) {}
  }
  $("#sa-gen-size").onchange = priceQualityOptions;

  // ---------------------------------------------------------------- targets + form
  async function loadTargets() {
    const sel = $("#sa-target");
    try { S.targets = (await api("/targets")).rows; }
    catch (e) { sel.innerHTML = `<option value="">error: ${esc(e.message)}</option>`; return; }
    sel.innerHTML = S.targets.map((t) => `<option value="${esc(t.id)}">${esc(t.name)} · ${t.type === "instagram" ? "Instagram" : "Facebook Page"}${t.publish_allowed ? "" : " · draft only"}</option>`).join("") || `<option value="">no targets</option>`;
    onTargetChange();
  }

  function onTargetChange() {
    const t = targetOf($("#sa-target").value);
    const kinds = t ? KINDS[t.type] || ["text"] : ["text"];
    const kindSel = $("#sa-kind");
    const keep = kindSel.value;
    kindSel.innerHTML = kinds.map((k) => `<option value="${k}">${k}</option>`).join("");
    kindSel.value = kinds.includes(keep) ? keep : kinds[0];
    $("#sa-link-wrap").style.display = t && t.type === "facebook_page" ? "" : "none";
    $("#sa-media-hint").textContent = t && t.type === "instagram"
      ? "Instagram needs at least one image or video at a public URL. Uploads go to hosting automatically."
      : "Optional. Files are uploaded to hosting; Facebook could also take them directly.";
    updateCount();
  }

  function updateCount() {
    const t = targetOf($("#sa-target").value);
    const limit = t ? LIMITS[t.type] : 63206;
    const n = $("#sa-text").value.length;
    const c = $("#sa-count");
    c.textContent = `${n.toLocaleString()} / ${limit.toLocaleString()}`;
    c.classList.toggle("over", n > limit);
  }

  function renderMedia() {
    const box = $("#sa-media-list");
    box.innerHTML = S.media.map((m, i) => `<span class="sa-media-item">${isImg(m) ? `<img src="${preview(m)}" alt="">` : ""}<span title="${esc(m)}">${esc(m.split("/").pop())}</span><button data-i="${i}" title="remove">✕</button></span>`).join("");
    box.querySelectorAll("button").forEach((b) => (b.onclick = () => { S.media.splice(Number(b.dataset.i), 1); renderMedia(); }));
  }

  async function uploadFiles(files) {
    for (const f of files) {
      const fd = new FormData(); fd.append("file", f);
      const label = el("span", "sa-media-item", `uploading ${f.name}…`);
      $("#sa-media-list").appendChild(label);
      try {
        const r = await api("/media", { method: "POST", body: fd });
        S.media.push(r.url || r.path);
      } catch (e) { chat.setBanner(`Upload failed: ${e.message}`); }
      renderMedia();
    }
  }

  function readForm() {
    return {
      target_id: $("#sa-target").value, kind: $("#sa-kind").value, text: $("#sa-text").value,
      link: $("#sa-link").value.trim() || null, media: S.media.slice(),
      scheduled_at: fromLocalInput($("#sa-when").value),
    };
  }

  function fillForm(post) {
    S.editing = post ? post.id : null;
    $("#sa-form-title").textContent = post ? `Editing draft ${post.id}` : "Compose";
    $("#sa-cancel-edit").style.display = post ? "" : "none";
    if (post) {
      $("#sa-target").value = post.target_id; onTargetChange();
      $("#sa-kind").value = post.kind; $("#sa-text").value = post.text || ""; $("#sa-link").value = post.link || "";
      S.media = (post.media || []).slice(); $("#sa-when").value = toLocalInput(post.scheduled_at);
    } else {
      $("#sa-text").value = ""; $("#sa-link").value = ""; S.media = []; $("#sa-when").value = "";
    }
    renderMedia(); updateCount();
    renderPosts();
  }

  async function saveDraft() {
    const body = readForm();
    if (!body.target_id) return chat.setBanner("Pick a target first.");
    try {
      const post = S.editing
        ? await api(`/posts/${S.editing}`, { method: "PATCH", body: JSON.stringify(body) })
        : await api("/posts", { method: "POST", body: JSON.stringify(body) });
      fillForm(null); await loadPosts(); loadLog();
      return post;
    } catch (e) { chat.setBanner(e.message); return null; }
  }

  async function saveThen(action) {
    const post = await saveDraft();
    if (!post) return;
    if (action === "publish") return act(post, "publish");
    if (action === "schedule") {
      if (!post.scheduled_at) return chat.setBanner("Set a time to schedule.");
      return act(post, "schedule", post.scheduled_at);
    }
  }

  // ---------------------------------------------------------------- calendar
  async function loadPosts() {
    try { S.posts = (await api("/posts")).rows; } catch (e) { chat.setBanner(e.message); return; }
    renderPosts();
  }

  const TABS = [["draft", "Drafts", (p) => p.status === "draft"], ["scheduled", "Scheduled", (p) => p.status === "scheduled" || p.status === "publishing"],
    ["published", "Published", (p) => p.status === "published"], ["other", "Failed / cancelled", (p) => p.status === "failed" || p.status === "cancelled"]];

  function renderPosts() {
    $("#sa-tabs").innerHTML = TABS.map(([k, label, f]) => `<button data-tab="${k}" class="${S.tab === k ? "active" : ""}">${label}<b>${S.posts.filter(f).length || ""}</b></button>`).join("");
    $("#sa-tabs").querySelectorAll("button").forEach((b) => (b.onclick = () => { S.tab = b.dataset.tab; renderPosts(); }));
    const filt = TABS.find(([k]) => k === S.tab)[2];
    let rows = S.posts.filter(filt);
    if (S.tab === "scheduled") rows = rows.slice().sort((a, b) => (a.scheduled_at || "").localeCompare(b.scheduled_at || ""));
    const list = $("#sa-list");
    if (!rows.length) { list.innerHTML = `<div class="ma-empty">${S.tab === "draft" ? "No drafts. Ask the agent for one, or use the form." : "Nothing here."}</div>`; return; }
    list.innerHTML = rows.map(card).join("");
    list.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => onAct(b.dataset.act, b.dataset.id)));
    list.querySelectorAll(".more").forEach((m) => (m.onclick = () => { m.previousElementSibling.classList.toggle("open"); m.textContent = m.previousElementSibling.classList.contains("open") ? "less" : "more"; }));
  }

  function card(p) {
    const when = p.status === "published" ? `published ${fmtWhen(p.published_at)}` : p.scheduled_at ? `${p.status === "scheduled" ? "goes out" : "planned"} ${fmtWhen(p.scheduled_at)}${p.scheduler === "local" ? " · our scheduler" : p.scheduler === "native" ? " · platform queue" : ""}` : `created ${fmtWhen(p.created_at)}`;
    const acts = [];
    if (p.status === "draft" || p.status === "failed") {
      acts.push(`<button data-act="edit" data-id="${p.id}">Edit</button>`);
      acts.push(`<button data-act="publish" data-id="${p.id}" class="primary">Publish now</button>`);
      acts.push(`<button data-act="schedule" data-id="${p.id}">${p.scheduled_at ? "Schedule" : "Schedule…"}</button>`);
      acts.push(`<button data-act="ask" data-id="${p.id}">Improve with Claude</button>`);
      acts.push(`<button data-act="delete" data-id="${p.id}" class="danger">Delete</button>`);
    } else if (p.status === "scheduled") {
      acts.push(`<button data-act="cancel" data-id="${p.id}" class="danger">Cancel</button>`);
    } else if (p.status === "published" && p.permalink) {
      acts.push(`<a href="${esc(p.permalink)}" target="_blank" rel="noopener">Open post ↗</a>`);
    } else if (p.status === "cancelled") {
      acts.push(`<button data-act="edit" data-id="${p.id}">Edit as draft</button>`, `<button data-act="delete" data-id="${p.id}" class="danger">Delete</button>`);
    }
    const long = (p.text || "").length > 280 || (p.text || "").split("\n").length > 5;
    return `<div class="sa-post ${S.editing === p.id ? "sel" : ""}">
      <div class="who"><span class="sa-kind ${esc(p.target_type)}">${esc(p.kind)}</span><b>${esc(p.target_name)}</b><span class="ma-pill ${esc(p.status)}">${esc(p.status)}</span><span class="ma-lock">${esc(p.id)}</span></div>
      <div class="when">${esc(when)}${p.cost_usd ? `<br><span title="Claude turns and images that went into this post">cost so far $${Number(p.cost_usd).toFixed(3)}</span>` : ""}</div>
      <div class="txt" dir="auto">${esc(p.text || "")}${p.link ? `\n${esc(p.link)}` : ""}</div>${long ? `<div class="more">more</div>` : ""}
      ${p.media && p.media.length ? `<div class="thumbs">${p.media.map((m) => isImg(m) ? `<a href="${preview(m)}" target="_blank" rel="noopener"><img src="${preview(m)}" alt=""></a>` : `<div class="file">${esc(m.split("/").pop())}</div>`).join("")}</div>` : ""}
      ${p.error ? `<div class="err">${esc(p.error)}</div>` : ""}
      <div class="acts">${acts.join("")}</div>
    </div>`;
  }

  async function act(post, action, when) {
    const t = targetOf(post.target_id);
    const label = action === "publish" ? `Publish now to ${post.target_name}?` : action === "schedule" ? `Schedule for ${fmtWhen(when)} on ${post.target_name}?` : `Cancel this scheduled post?`;
    if (!confirm(`${label}\n\n${(post.text || "").slice(0, 300)}${t && !t.publish_allowed ? "\n\n(This target is draft-only; the server will refuse.)" : ""}`)) return;
    try {
      await api(`/posts/${post.id}/${action}`, { method: "POST", body: JSON.stringify(when ? { scheduled_at: when } : {}) });
      chat.setBanner("");
    } catch (e) { chat.setBanner(e.message); }
    await loadPosts(); loadLog();
  }

  async function onAct(action, id) {
    const p = S.posts.find((x) => x.id === id);
    if (!p) return;
    if (action === "edit") { if (p.status === "cancelled") { try { await api(`/posts/${id}`, { method: "PATCH", body: JSON.stringify({}) }); } catch (e) {} } fillForm({ ...p, status: "draft" }); $("#sa-text").focus(); return; }
    if (action === "delete") { if (!confirm("Delete this draft?")) return; try { await api(`/posts/${id}`, { method: "DELETE" }); if (S.editing === id) fillForm(null); } catch (e) { chat.setBanner(e.message); } return loadPosts(); }
    if (action === "publish") return act(p, "publish");
    if (action === "cancel") return act(p, "cancel");
    if (action === "ask") return chat.fill(`Improve draft ${id}: keep the message, make it tighter and more engaging for ${p.target_type === "instagram" ? "Instagram" : "Facebook"}. Update the draft with update_draft and show me the new text.`);
    if (action === "schedule") {
      let when = p.scheduled_at;
      if (!when) {
        const v = prompt("When? (YYYY-MM-DD HH:MM, local time)", toLocalInput(new Date(Date.now() + 3600e3).toISOString()).replace("T", " "));
        if (!v) return;
        when = fromLocalInput(v.trim().replace(" ", "T"));
        if (!when || isNaN(new Date(when))) return chat.setBanner("Could not read that time.");
      }
      return act(p, "schedule", when);
    }
  }

  async function loadLog() {
    const ul = $("#sa-log");
    try {
      const rows = (await api("/log?n=12")).rows.slice().reverse();
      ul.innerHTML = rows.length ? rows.map((e) => `<li><time>${fmtTime(e.ts)}</time><span class="${e.approved === false ? "ko" : e.approved ? "ok" : ""}">${e.approved === false ? "✕" : e.approved ? "✓" : "·"}</span><span>${esc(e.action)} ${esc(e.target || "")}${e.note ? " — " + esc(e.note) : ""}</span></li>`).join("")
        : `<li class="ma-empty">Nothing yet.</li>`;
    } catch (e) { ul.innerHTML = `<li class="ma-empty">${esc(e.message)}</li>`; }
  }

  // ---------------------------------------------------------------- wiring
  $("#sa-target").onchange = onTargetChange;
  $("#sa-text").oninput = updateCount;
  $("#sa-file").onchange = (e) => { uploadFiles(Array.from(e.target.files || [])); e.target.value = ""; };
  $("#sa-add-url").onclick = () => { const u = prompt("Public media URL (image or video):"); if (u && /^https?:\/\//.test(u.trim())) { S.media.push(u.trim()); renderMedia(); } };
  $("#sa-gen-btn").onclick = async () => {
    const prompt = $("#sa-gen-prompt").value.trim();
    if (!prompt) return chat.setBanner("Describe the image first.");
    const box = $("#sa-gen"); box.classList.add("busy"); $("#sa-gen-hint").textContent = "Generating… (10–40 s)";
    try {
      const r = await api("/media/generate", { method: "POST", body: JSON.stringify({ prompt, size: $("#sa-gen-size").value, quality: $("#sa-gen-quality").value }) });
      S.media.push(r.url || r.path); renderMedia(); $("#sa-gen-prompt").value = "";
      $("#sa-gen-hint").textContent = `Added. ${r.model}${r.estimated_cost_usd != null ? ` · ~$${r.estimated_cost_usd.toFixed(3)}` : ""}`;
      S.status = await api("/status"); renderHeader();
    } catch (e) { $("#sa-gen-hint").textContent = e.message; }
    finally { box.classList.remove("busy"); }
  };
  $("#sa-save").onclick = () => saveDraft();
  $("#sa-save-schedule").onclick = () => saveThen("schedule");
  $("#sa-save-publish").onclick = () => saveThen("publish");
  $("#sa-cancel-edit").onclick = () => fillForm(null);
  $("#sa-refresh").onclick = () => { loadPosts(); loadLog(); };

  const main = document.querySelector("main");
  new MutationObserver(() => { if (main.dataset.view === "social") chat.boot(); }).observe(main, { attributes: true, attributeFilter: ["data-view"] });
  if (main.dataset.view === "social") chat.boot();
  setInterval(() => { if (main.dataset.view === "social" && S.status) loadPosts(); }, 30000);  // scheduler may publish
})();
