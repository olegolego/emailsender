/* EmailManager UI - core, Home, Replies. Pages for batches/sequences/settings live in pages.js. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const page = () => $("#page");
const ROUTES = {};
let STATE = null, DIRTY = false, refreshTimer = null;

async function api(path, method = "GET", body) {
  const opt = { method, headers: { "X-EmailManager": "1" } };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch("/api" + path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok || (data && data.ok === false)) throw new Error((data && data.error) || `Error ${r.status}`);
  return data;
}
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 3200);
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, "error"); throw e; }
}

// ---- time helpers
const D = (iso) => (iso ? new Date(iso) : null);
function when(iso) {
  if (!iso) return "-";
  const t = D(iso), now = new Date();
  const time = t.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const days = Math.round((new Date(t.toDateString()) - new Date(now.toDateString())) / 864e5);
  if (days === 0) return "Today " + time;
  if (days === 1) return "Tomorrow " + time;
  if (days === -1) return "Yesterday " + time;
  return t.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) + ", " + time;
}
function dur(ms) {
  const s = Math.floor(Math.abs(ms) / 1000), m = Math.floor(s / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d >= 1) return `${d}d ${h % 24}h`;
  if (h >= 1) return `${h}h ${m % 60}m`;
  if (m >= 1) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${s}s`;
}
function ago(iso) { if (!iso) return "never"; const ms = Date.now() - D(iso); return ms < 50000 ? "just now" : dur(ms).split(" ")[0] + " ago"; }
const cd = (iso) => (iso ? `<span class="cd" data-cd="${esc(iso)}"></span>` : "");
function tick() {
  const now = Date.now();
  $$("[data-cd]").forEach((el) => {
    const ms = D(el.dataset.cd) - now;
    el.textContent = ms > 0 ? "in " + dur(ms) : "sending soon";
    el.className = "cd " + (ms <= 0 ? "late" : ms < 3600e3 ? "soon" : "");
  });
}
setInterval(tick, 1000);
function toLocalInput(iso) {
  const t = iso ? D(iso) : new Date(Date.now() + 3600e3), p = (n) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}T${p(t.getHours())}:${p(t.getMinutes())}`;
}
const fromLocalInput = (v) => (v ? new Date(v).toISOString() : null);

// ---- labels
const CAT = {
  interested: ["Interested", "good"], question: ["Question", "info"], referral: ["Referral", "info"],
  not_interested: ["Not interested", "bad"], out_of_office: ["Out of office", ""], unsubscribe: ["Unsubscribe", "bad"],
  bounce: ["Bounced", "bad"], auto_reply: ["Auto-reply", ""], other: ["Reply", ""],
};
const catPill = (c) => `<span class="pill ${CAT[c]?.[1] ?? ""}">${esc(CAT[c]?.[0] ?? "Reply")}</span>`;
const fullName = (r) => [r.first_name, r.last_name].filter(Boolean).join(" ");
const who = (r, email) => `<div class="who"><b>${esc(fullName(r) || r.company || email)}</b><div>${esc(email)}${r.company && fullName(r) ? " · " + esc(r.company) : ""}</div></div>`;
const fromPill = (email) => email ? `<span class="pill from" title="Sent from this mailbox">✉ ${esc(email)}</span>` : "";

// ---- modal / tooltip / drawer
function modal(html, opts = {}) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal ${opts.wide ? "wide" : ""}">${html}</div>`;
  document.body.appendChild(back);
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  $$("[data-close]", back).forEach((b) => b.addEventListener("click", close));
  const m = back.firstElementChild; m.close = close; return m;
}
const mhead = (t) => `<div class="modal-head"><h2>${t}</h2><button class="ghost x" data-close>✕</button></div>`;
const tip = $("#tooltip");
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest("[data-tip]");
  if (!t) { tip.style.display = "none"; return; }
  tip.innerHTML = t.dataset.tip; tip.style.display = "block";
  tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px"; tip.style.top = e.clientY + 16 + "px";
});

async function openNotifications() {
  const rows = await api("/notifications");
  const el = document.createElement("div");
  el.className = "drawer";
  el.innerHTML = `<div class="dh"><h2>Notifications</h2><button class="ghost" style="margin-left:auto" id="nclose">✕</button></div>
    <div class="db">${rows.length ? rows.map((n) => `<a class="nf ${n.read ? "" : "unread"}" href="${esc((n.link || "#/").replace("#/inbox", "#/replies").replace("#/queue/needs_approval", "#/").replace("#/queue/failed", "#/"))}">
      <b>${esc(n.title)}</b><span>${esc(n.body)}</span><small>${when(n.ts)}</small></a>`).join("") : `<div class="empty">Nothing yet.</div>`}</div>`;
  document.body.appendChild(el);
  const close = () => el.remove();
  $("#nclose", el).onclick = close;
  $$("a.nf", el).forEach((a) => a.addEventListener("click", close));
  setTimeout(() => document.addEventListener("mousedown", function h(e) { if (!el.contains(e.target)) { close(); document.removeEventListener("mousedown", h); } }), 0);
  api("/notifications/read", "POST").then(refreshState);
}

// ---- header state
async function refreshState() {
  try { STATE = await api("/state"); } catch (e) { return; }
  const s = STATE, c = s.counts;
  const b = $("#c-replies");
  b.hidden = !c.unhandled; b.textContent = c.unhandled || "";
  $("#hdr-right").innerHTML = `
    ${s.settings.test_mode ? `<a class="pill warn" href="#/settings" data-tip="Every email goes to ${esc(s.settings.test_address || "your test address")} instead of the real person">Test mode</a>` : ""}
    <button class="sm ${s.settings.sending_paused ? "primary" : ""}" id="btn-pause" data-tip="${s.settings.sending_paused ? "Nothing is being sent" : "Stop every email from going out"}">${s.settings.sending_paused ? "▶ Resume sending" : "⏸ Pause all"}</button>
    <button class="sm bell ghost" id="btn-bell">🔔${c.unread_notifications ? `<span class="badge">${c.unread_notifications}</span>` : ""}</button>`;
  $("#btn-pause").onclick = () => act(() => api("/worker/pause", "POST", { paused: !s.settings.sending_paused }), s.settings.sending_paused ? "Sending resumed" : "All sending paused").then(refreshState);
  $("#btn-bell").onclick = openNotifications;
}

// ---- router
function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const r = parts[0] || "home";
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.r === r));
  clearInterval(refreshTimer);
  tip.style.display = "none";
  page().innerHTML = `<div class="empty">Loading…</div>`;
  (ROUTES[r] || ROUTES.home)(...parts.slice(1)).catch((e) => { page().innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; });
  window.scrollTo(0, 0);
}
let skipHash = false;
window.addEventListener("hashchange", () => {
  if (skipHash) { skipHash = false; return; }
  if (DIRTY && !confirm("You have unsaved changes. Leave without saving?")) { skipHash = true; history.back(); return; }
  DIRTY = false; route();
});
window.addEventListener("beforeunload", (e) => { if (DIRTY) { e.preventDefault(); e.returnValue = ""; } });
const autoRefresh = (fn, ms = 15000) => { refreshTimer = setInterval(() => { if (!$(".modal-back") && !document.hidden) fn(); }, ms); };

// ================================================================ HOME
ROUTES.home = async function (tab = "upcoming") {
  await refreshState();
  const draw = async () => {
    const [s, q] = [STATE, await api("/queue?status=" + tab)];
    const c = s.counts, w = s.week;
    const limit = s.accounts.reduce((a, b) => a + (b.active ? b.daily_limit : 0), 0);
    const k = q.counts;
    const T = [["upcoming", "Scheduled", (k.pending || 0) + (k.needs_approval || 0)], ["needs_approval", "Needs your OK", k.needs_approval || 0], ["sent", "Sent", k.sent || 0], ["failed", "Failed", k.failed || 0]];
    page().innerHTML = `
      <div class="head"><div><h1>Home</h1><div class="sub">What's going out and who answered.</div></div>
        <div class="actions"><a class="btn primary big" href="#/batches/new">+ New batch</a></div></div>
      ${!s.accounts.length ? `<div class="banner bad">No mailbox connected yet. <a class="right btn sm" href="#/settings">Connect mailbox</a></div>` : ""}
      ${s.settings.sending_paused ? `<div class="banner bad">All sending is paused - nothing goes out until you resume.</div>` : ""}
      ${s.settings.test_mode ? `<div class="banner warn">Test mode is on: emails go to ${esc(s.settings.test_address || "(no test address set)")} instead of the real people. <a class="right" href="#/settings">Turn off in Settings</a></div>` : ""}
      <div class="tiles">
        <div class="tile"><div class="l">Sent today</div><div class="v">${c.sent_today}<span class="muted" style="font-size:16px;font-weight:500"> / ${limit}</span></div><div class="h">daily limit</div></div>
        <a class="tile" href="#/"><div class="l">Scheduled</div><div class="v">${c.pending + c.needs_approval}</div><div class="h">${c.due_24h} in the next 24 h</div></a>
        <a class="tile" href="#/replies"><div class="l">New replies</div><div class="v" style="${c.unhandled ? "color:var(--accent)" : ""}">${c.unhandled}</div><div class="h">waiting for you</div></a>
        <div class="tile"><div class="l">Reply rate · 7 days</div><div class="v">${w.reply_rate}%</div><div class="h">${w.replied} of ${w.contacted} people</div></div>
      </div>
      <div class="card"><div class="card-head"><h2>Outbox</h2><div class="right"><div class="seg">${T.map(([t, l, n]) => `<a href="#/home/${t}" class="${t === tab ? "active" : ""}">${l}${n ? ` · ${n}` : ""}</a>`).join("")}</div></div></div>
          ${q.rows.length ? `<div class="olist">${q.rows.map((r) => outboxRow(r, tab)).join("")}</div>` : `<div class="empty">${tab === "upcoming" ? "Nothing scheduled. Start a batch to send emails." : "Nothing here."}</div>`}
      </div>
      <div style="margin-top:16px">
        <div class="card"><div class="card-head"><h2>Latest replies</h2><div class="right"><a href="#/replies">All →</a></div></div>
          ${s.recent.length ? `<div class="mini">${s.recent.map((m) => `<div style="cursor:pointer" onclick="location.hash='#/replies/${m.id}'">
            <div style="flex:1;min-width:0"><div class="row" style="gap:6px"><b>${esc(m.from_name || m.from_addr)}</b>${m.handled ? "" : '<span class="pill info">new</span>'}</div>
            <div class="muted" style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(m.summary || m.subject)}</div></div>
            ${catPill(m.category)}</div>`).join("")}</div>` : `<div class="empty">No replies yet.</div>`}
        </div>
      </div>`;
    tick();
    $$("[data-qa]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); queueAction(b.dataset.qa, +b.dataset.id, q.rows.find((r) => r.id == b.dataset.id), draw); }));
  };
  await draw();
  autoRefresh(async () => { await refreshState(); draw(); }, 20000);
};

const initials = (r, email) => ((fullName(r) || r.company || email || "?").split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("") || "?").toUpperCase();

function outboxRow(r, tab) {
  const editable = ["upcoming", "needs_approval", "failed"].includes(tab);
  const t = tab === "sent" ? r.sent_at : r.scheduled_at;
  const snippet = (r.body || "").replace(/\s+/g, " ").replace(/^(hi|hello|dear)\b[^,]*,\s*/i, "").slice(0, 140);
  const status = r.status === "sent" ? `<span class="ost good">✓ Sent</span>` : r.status === "needs_approval" ? `<span class="ost warn">Needs your OK</span>`
    : r.status === "failed" ? `<span class="ost bad">Failed</span>` : r.status === "cancelled" ? `<span class="ost">Skipped</span>` : cd(r.scheduled_at);
  const note = r.status === "needs_approval" && r.approval_reason ? `<div class="onote warn">${esc(r.approval_reason)}</div>`
    : r.status === "failed" && r.error ? `<div class="onote bad">${esc(r.error)}</div>` : "";
  return `<div class="orow" data-qa="open" data-id="${r.id}">
    <div class="av">${esc(initials(r, r.to_addr))}</div>
    <div class="oto"><b>${esc(fullName(r) || r.to_addr)}</b><span>${esc(fullName(r) ? r.to_addr : (r.company || ""))}</span></div>
    <div class="omsg"><b>${esc(r.subject || "(no subject)")}</b><span>${esc(snippet)}</span>
      <div class="otags"><span>${esc(r.step_name || "One-off email")}</span>${r.batch ? `<span>${esc(r.batch)}</span>` : ""}<span>from ${esc(r.account_email || "")}</span></div>${note}</div>
    <div class="owhen"><b>${when(t)}</b>${status}</div>
    <div class="oact">${r.status === "needs_approval" ? `<button class="sm" data-qa="approve" data-id="${r.id}">Approve</button>` : ""}
      ${editable && r.status !== "sending" ? `<button class="sm primary" data-qa="send" data-id="${r.id}">Send now</button>` : ""}</div>
  </div>`;
}

async function queueAction(a, id, r, reload) {
  if (a === "send") { await act(() => api(`/queue/${id}/action`, "POST", { action: "send_now" }), "Sent ✓").catch(() => {}); }
  else if (a === "approve") { await act(() => api(`/queue/${id}/action`, "POST", { action: "approve" }), "Approved - it goes out at its time"); }
  else if (a === "open") return editEmail(r, reload);
  reload(); refreshState();
}

function editEmail(r, reload) {
  const ro = ["sent", "cancelled"].includes(r.status);
  const m = modal(`${mhead(ro ? "Sent email" : "Edit email")}
    <div class="row" style="margin-bottom:14px">${fromPill(r.account_email)} <span class="muted">→</span> <b>${esc(r.to_addr)}</b></div>
    <div style="display:grid;gap:12px">
      <label class="field"><span>Subject</span><input id="e-s" value="${esc(r.subject)}" ${ro ? "disabled" : ""}></label>
      <label class="field"><span>Message</span><textarea id="e-b" rows="14" ${ro ? "disabled" : ""}>${esc(r.body)}</textarea></label>
      ${ro ? `<div class="hint">Sent ${when(r.sent_at)}</div>` : `<label class="field" style="max-width:260px"><span>Send at</span><input type="datetime-local" id="e-t" value="${toLocalInput(r.scheduled_at)}"></label>`}
    </div>
    <div class="modal-foot">${ro ? `<button data-close>Close</button>` : `
      <button class="ghost danger" id="e-skip" style="margin-right:auto">Don't send this one</button>
      <button id="e-save">Save</button><button class="primary" id="e-send">Send now</button>`}</div>`);
  if (ro) return;
  const payload = () => ({ action: "edit", subject: $("#e-s", m).value, body: $("#e-b", m).value, scheduled_at: fromLocalInput($("#e-t", m).value) });
  $("#e-save", m).onclick = async () => {
    await act(() => api(`/queue/${r.id}/action`, "POST", payload()));
    if (r.status === "needs_approval") await act(() => api(`/queue/${r.id}/action`, "POST", { action: "approve" }));
    toast("Saved"); m.close(); reload();
  };
  $("#e-send", m).onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Sending…";
    try { await act(() => api(`/queue/${r.id}/action`, "POST", payload())); await act(() => api(`/queue/${r.id}/action`, "POST", { action: "send_now" }), "Sent ✓"); m.close(); }
    catch (err) { e.target.disabled = false; e.target.textContent = "Send now"; }
    reload(); refreshState();
  };
  $("#e-skip", m).onclick = async () => {
    if (!confirm("Skip this email? The next follow-up (if any) still happens as if they didn't reply.")) return;
    await act(() => api(`/queue/${r.id}/action`, "POST", { action: "cancel" }), "Skipped"); m.close(); reload(); refreshState();
  };
}

// ================================================================ REPLIES
ROUTES.replies = async function (id) {
  const filter = sessionStorage.getItem("rf") === "all" ? "all" : "unhandled";
  const data = await api("/inbox?filter=" + filter);
  const F = [["unhandled", "New"], ["all", "All"]];
  page().innerHTML = `
    <div class="head"><div><h1>Replies</h1><div class="sub">Every answer is read and summarised for you.</div></div>
      <div class="actions"><div class="seg">${F.map(([k, l]) => `<a href="#" data-f="${k}" class="${k === filter ? "active" : ""}">${l}</a>`).join("")}</div></div></div>
    <div class="split"><div class="mlist">${data.rows.length ? data.rows.map((m) => `
      <div class="mi ${m.handled ? "" : "new"} ${String(m.id) === id ? "on" : ""}" data-id="${m.id}">
        <div class="top"><span class="nm">${esc(m.from_name || m.from_addr)}</span><span class="when">${ago(m.ts)}</span></div>
        <div class="row" style="gap:6px">${m.replied_to_id ? catPill(m.category) : `<span class="pill info">✉ Wrote first</span>`}<span class="muted" style="font-size:12.5px">${esc(m.company || "")}</span></div>
        <div class="snip">${esc(m.summary || m.snippet || m.subject)}</div></div>`).join("") : `<div class="empty">${filter === "unhandled" ? "You're all caught up 🎉" : "No replies yet."}</div>`}</div>
      <div id="detail">${id ? "" : `<div class="card empty">Pick a reply on the left.</div>`}</div></div>`;
  $$("[data-f]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); sessionStorage.setItem("rf", a.dataset.f); route(); }));
  $$(".mi").forEach((el) => (el.onclick = () => { history.replaceState(null, "", "#/replies/" + el.dataset.id); $$(".mi").forEach((x) => x.classList.toggle("on", x === el)); showReply(el.dataset.id); }));
  if (id) showReply(id);
};

async function showReply(id) {
  const box = $("#detail");
  box.innerHTML = `<div class="card empty">Loading…</div>`;
  const m = await api("/messages/" + id);
  const a = m.analysis || {};
  const acc = STATE?.accounts.find((x) => x.id === m.account_id);
  box.innerHTML = `
    <div class="card">
      <div class="row"><div class="grow"><h2>${esc(m.from_name || m.from_addr)}</h2>
        <div class="muted" style="font-size:13px">${esc(m.from_addr)}${m.contact?.company ? " · " + esc(m.contact.company) : ""} · ${when(m.ts)}</div></div>
        ${m.handled ? `<button class="sm" id="r-undo">Mark as new</button>` : `<button class="sm" id="r-done">✓ Mark as done</button>`}
        <button class="sm ghost danger" id="r-del">Delete</button></div>
      <div class="ai" style="margin-top:16px">
        <div class="row" style="gap:8px">${catPill(m.category)}${a.interest_score != null ? `<span class="muted" style="font-size:13px">Interest</span><span class="meter"><i style="width:${a.interest_score}%"></i></span><b class="num" style="font-size:13px">${a.interest_score}</b>` : ""}
          <span class="muted" style="margin-left:auto;font-size:12px">${m.analysis_source === "claude" ? "✨ analysed by Claude" : m.analysis_source === "rules" ? "keyword analysis" : ""}</span></div>
        <p style="margin:10px 0 0;font-weight:550">${esc(a.summary || "")}</p>
        ${a.requests?.length ? `<div style="margin-top:10px"><h3>They ask for</h3><ul>${a.requests.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
        ${a.next_action ? `<div style="margin-top:10px"><h3>Next step</h3>${esc(a.next_action)}</div>` : ""}
        ${m.enrollment ? `<div class="muted" style="margin-top:10px;font-size:13px">Sequence “${esc(m.enrollment.pipeline)}” stopped for this person, so no more automatic emails.</div>` : !m.replied_to_id ? `<div class="muted" style="margin-top:10px;font-size:13px">This person wrote to you first - not a reply to one of your emails.</div>` : ""}
      </div>
    </div>
    <div class="card"><h3>Conversation</h3><div class="thread">${m.thread.map((t) => `
      <div class="bubble ${t.direction}"><div class="meta"><b>${t.direction === "out" ? "You" : esc(t.from_addr)}</b><span>${when(t.ts)}</span><span>${esc(t.subject)}</span></div>
      <div class="txt">${esc(t.direction === "in" ? (t.reply_text || t.body) : t.body)}</div></div>`).join("")}</div></div>
    <div class="card"><div class="card-head"><h2>Answer</h2><div class="right">${fromPill(acc?.email)}</div></div>
      <textarea id="r-text" rows="8" placeholder="Write your answer…"></textarea>
      <div class="row" style="margin-top:10px">
        ${a.suggested_reply ? `<button class="sm" id="r-sug">Use suggested answer</button>` : ""}
        <input id="r-ins" class="grow" placeholder="Tell Claude what to say, e.g. “thank them, we'll send the application today”">
        <button class="sm" id="r-draft">✨ Write with Claude</button>
        <button class="primary" id="r-send">Send</button></div></div>`;
  const mark = (h) => act(() => api(`/messages/${id}/handled`, "POST", { handled: h }), h ? "Marked as done" : "Marked as new").then(() => { refreshState(); route(); });
  const dn = $("#r-done"), un = $("#r-undo");
  if (dn) dn.onclick = () => mark(true);
  if (un) un.onclick = () => mark(false);
  $("#r-del").onclick = () => {
    const stopped = m.enrollment && ["replied", "stopped"].includes(m.enrollment.state) && /^reply/.test(m.enrollment.stopped_reason || "");
    const firstContact = !m.replied_to_id;
    const dm = modal(`${mhead("Delete this conversation?")}
      <p style="margin-top:0">Removes the emails received from <b>${esc(m.from_addr)}</b> from EmailManager. Nothing is deleted in Gmail, and emails you sent stay in your history.</p>
      <label class="check" style="margin:8px 0"><input type="checkbox" id="dl-ign" ${firstContact ? "checked" : ""}> Ignore future emails from ${esc(m.from_addr)}</label>
      ${stopped ? `<label class="check" style="margin:8px 0"><input type="checkbox" id="dl-res"> Continue their email sequence (the next follow-up goes out as if they never replied)</label>` : ""}
      <div class="modal-foot"><button data-close>Cancel</button><button class="primary" style="background:var(--bad);border-color:var(--bad)" id="dl-go">Delete conversation</button></div>`);
    $("#dl-go", dm).onclick = async () => {
      const r = await act(() => api(`/messages/${id}/delete-conversation`, "POST", { ignore: $("#dl-ign", dm).checked, resume: !!$("#dl-res", dm)?.checked }));
      toast(`Deleted ${r.deleted} email${r.deleted === 1 ? "" : "s"}${r.resumed ? " · sequence continues" : ""}`);
      dm.close(); history.replaceState(null, "", "#/replies"); refreshState(); route();
    };
  };
  const sg = $("#r-sug");
  if (sg) sg.onclick = () => { $("#r-text").value = a.suggested_reply; };
  $("#r-draft").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Writing…";
    try { const r = await act(() => api(`/messages/${id}/draft`, "POST", { instructions: $("#r-ins").value })); $("#r-text").value = r.body; }
    finally { e.target.disabled = false; e.target.textContent = "✨ Write with Claude"; }
  };
  $("#r-send").onclick = async (e) => {
    const text = $("#r-text").value.trim();
    if (!text) return toast("Write an answer first");
    const subj = /^re:/i.test(m.subject || "") ? m.subject : "Re: " + (m.subject || "");
    e.target.disabled = true; e.target.textContent = "Sending…";
    try { await act(() => api("/send", "POST", { to: m.from_addr, subject: subj, body: text, reply_to_message_id: m.id, account_id: m.account_id, contact_id: m.contact_id }), "Answer sent ✓"); refreshState(); showReply(id); }
    finally { e.target.disabled = false; e.target.textContent = "Send"; }
  };
}
