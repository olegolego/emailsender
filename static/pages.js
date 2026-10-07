/* EmailManager UI - Batches, Sequences, Settings, boot. */
"use strict";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const dayWord = (n) => `${n} day${n == 1 ? "" : "s"}`;

function flowLine(steps) {
  return steps.map((s, i) => `<span class="e">${i ? "Follow-up " + i : "Email 1"}</span>${i < steps.length - 1 ? `<span class="w">→ ${dayWord(s.wait_days)} →</span>` : ""}`).join("") +
    `<span class="muted">· reply → notify me & stop</span>`;
}

// ================================================================ BATCHES
ROUTES.batches = async function (id) {
  if (id === "new") return newBatch();
  if (id) return batchPage(+id);
  const rows = await api("/batches");
  const live = rows.filter((b) => b.status !== "draft" && !b.is_done), ready = rows.filter((b) => b.status === "draft");
  const hist = rows.filter((b) => b.is_done).sort((x, y) => (y.done_at || "").localeCompare(x.done_at || ""));
  const groups = {};
  ready.forEach((b) => (groups[b.source || "Other"] ||= []).push(b));
  const tab = ["live", "ready", "history"].includes(sessionStorage.getItem("btab")) ? sessionStorage.getItem("btab") : (live.length ? "live" : "ready");
  const T = [["live", "Sending now", live.length], ["ready", "Ready to send", ready.length], ["history", "History", hist.length]];
  page().innerHTML = `
    <div class="head"><div><h1>Batches</h1><div class="sub">A batch is a group of companies that gets one email style from one mailbox.</div></div>
      <div class="actions"><a class="btn big" href="#/batches/new">+ Paste your own list</a></div></div>
    <div class="seg" style="margin-bottom:18px">${T.map(([k, l, n]) => `<a href="#" data-bt="${k}" class="${k === tab ? "active" : ""}">${l} · ${n}</a>`).join("")}</div>
    ${tab === "live" ? (live.length ? live.map(batchCard).join("") : `<div class="card empty" style="padding:40px">Nothing is sending right now. Open <a href="#" data-bt="ready">Ready to send</a>, pick a batch and press Start.</div>`) : ""}
    ${tab === "history" ? (hist.length ? `<div class="hint" style="margin:-4px 0 12px">Finished batches: everyone in them replied, got all their emails, or the batch was stopped.</div>${hist.map(batchCard).join("")}`
      : `<div class="card empty" style="padding:40px">No finished batches yet. When everyone in a batch has replied or received all their emails, it moves here.</div>`) : ""}
    ${tab === "ready" ? (ready.length ? `<div class="hint" style="margin:-4px 0 12px">${ready.length} batches · ${ready.reduce((a, b) => a + b.total, 0)} companies. Duplicates removed and every company is in exactly one batch. Open one, pick an email style and press Start.</div>
      ${Object.entries(groups).map(([src, bs]) => `<div class="card" style="margin-bottom:12px;padding:14px 18px">
        <div class="row"><b>${esc(src)}</b><span class="muted">${bs.length} batches · ${bs.reduce((a, b) => a + b.total, 0)} companies</span>
          <span class="muted" style="margin-left:auto;font-size:12.5px">${esc(bs[0].note || "")}</span></div>
        <div class="readygrid">${bs.map((b) => `<a class="ready" href="#/batches/${b.id}"><span>${esc(b.name.replace(src + " · ", "").replace(src, "All") || b.name)}</span><b>${b.total}</b></a>`).join("")}</div>
      </div>`).join("")}` : `<div class="card empty" style="padding:40px">No batches waiting.</div>`) : ""}`;
  $$("[data-bt]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); sessionStorage.setItem("btab", a.dataset.bt); ROUTES.batches(); }));
  tick();
  if (tab === "live" && live.length) autoRefresh(() => ROUTES.batches(), 30000);
};

function progressBar(b) {
  const t = Math.max(1, b.total), replied = b.replied, bad = b.bounced, done = b.finished, run = Math.max(0, b.total - replied - bad - done);
  const seg = (n, c) => (n ? `<i class="${c}" style="width:${(100 * n) / t}%"></i>` : "");
  return `<div class="bar">${seg(replied, "g")}${seg(run, "b")}${seg(done, "m")}${seg(bad, "r")}</div>
    <div class="legend"><span><i style="background:var(--good)"></i>${replied} replied</span><span><i style="background:var(--accent)"></i>${run} in progress</span>
    <span><i style="background:var(--line-2)"></i>${done} no reply</span>${bad ? `<span><i style="background:var(--bad)"></i>${bad} bounced / opted out</span>` : ""}</div>`;
}
const bStatus = (s, done) => s === "stopped" ? `<span class="pill">Stopped</span>` : done ? `<span class="pill">✓ Finished</span>`
  : s === "running" ? `<span class="pill good">● Running</span>` : s === "paused" ? `<span class="pill warn">Paused</span>` : `<span class="pill">${esc(s)}</span>`;

function batchCard(b) {
  return `<a class="bcard" href="#/batches/${b.id}">
    <div class="row"><h2>${esc(b.name)}</h2>${bStatus(b.status, b.is_done)}<span style="margin-left:auto">${fromPill(b.from_email)}</span></div>
    <div class="muted" style="font-size:13px;margin-top:2px">Sequence: ${esc(b.sequence)} · created ${when(b.created_at)}</div>
    ${progressBar(b)}
    <div class="stats" style="margin-top:14px">
      <div><b>${b.total}</b><span>people</span></div><div><b>${b.sent}</b><span>emails sent</span></div>
      <div><b>${b.replied}</b><span>replied</span></div><div><b>${b.reply_rate}%</b><span>reply rate</span></div>
      <div><b style="font-size:15px;padding-top:4px">${b.next_at && b.status === "running" ? when(b.next_at) : "-"}</b><span>next email</span></div></div></a>`;
}

async function batchPage(id) {
  const b = await api("/batches/" + id);
  if (b.status === "draft") return draftPage(b);
  page().innerHTML = `
    <a class="back" href="#/batches">← ${b.is_done ? "History" : "All batches"}</a>
    <div class="head"><div><div class="row"><h1>${esc(b.name)}</h1>${bStatus(b.status, b.is_done)}</div>
      <div class="sub row" style="margin-top:6px">Sending from ${fromPill(b.from_email)} · style <a href="#/sequences/${b.pipeline_id}">${esc(b.sequence)}</a></div>
      <div class="sub row" style="margin-top:6px">🕘 ${b.settings?.days || b.settings?.window_start ? esc(windowText({ days: b.settings.days || [0, 1, 2, 3, 4], window_start: b.settings.window_start || "09:00", window_end: b.settings.window_end || "16:00" })) : "Style's default send hours"}${b.settings?.daily_limit ? ` · up to ${b.settings.daily_limit}/day` : ""}
        ${b.status !== "stopped" ? `<button class="sm ghost" id="b-win">Change</button>` : ""}</div></div>
      <div class="actions">
        <button id="b-add">+ Add people</button>
        ${b.status === "running" ? `<button id="b-pause">⏸ Pause</button>` : b.status === "paused" ? `<button class="primary" id="b-resume">▶ Resume</button>` : ""}
        ${b.status !== "stopped" ? `<button class="ghost danger" id="b-stop">Stop batch</button>` : ""}</div></div>
    <div class="card">${progressBar(b)}
      <div class="stats" style="margin-top:14px"><div><b>${b.total}</b><span>people</span></div><div><b>${b.sent}</b><span>emails sent</span></div>
      <div><b>${b.replied}</b><span>replied</span></div><div><b>${b.positive}</b><span>interested / questions</span></div><div><b>${b.reply_rate}%</b><span>reply rate</span></div></div></div>
    <div class="card"><div class="card-head"><h2>People</h2><div class="right"><input id="b-q" placeholder="Search…" style="width:200px"></div></div>
      <table><thead><tr><th>Person</th><th>Status</th><th>Emails</th><th>Next</th><th></th></tr></thead><tbody id="b-rows"></tbody></table></div>`;
  const draw = () => {
    const q = ($("#b-q").value || "").toLowerCase();
    $("#b-rows").innerHTML = b.people.filter((p) => !q || [p.email, p.first_name, p.last_name, p.company].join(" ").toLowerCase().includes(q)).map((p) => `<tr>
      <td>${who(p, p.email)}</td>
      <td><span class="pill ${p.kind === "muted" ? "" : p.kind}">${esc(p.text)}</span>${p.reply_summary ? `<div class="muted ell" style="font-size:12.5px;margin-top:4px">“${esc(p.reply_summary)}”</div>` : ""}</td>
      <td class="num">${p.sent} of ${p.steps}</td>
      <td>${p.next_at && ["active"].includes(p.state) ? `<div style="white-space:nowrap">${when(p.next_at)}</div>${cd(p.next_at)}` : "-"}</td>
      <td class="r" style="white-space:nowrap">${p.reply_id ? `<a class="btn sm" href="#/replies/${p.reply_id}">Open reply</a>` : ""}
        ${p.queue_id ? `<button class="sm primary" data-send="${p.queue_id}">Send now</button>` : ""}
        ${["active", "paused"].includes(p.state) ? `<button class="sm ghost danger" data-stop="${p.id}">Stop</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">Nobody here.</td></tr>`;
    tick();
    $$("[data-send]").forEach((x) => (x.onclick = async () => { await act(() => api(`/queue/${x.dataset.send}/action`, "POST", { action: "send_now" }), "Sent ✓").catch(() => {}); batchPage(id); }));
    $$("[data-stop]").forEach((x) => (x.onclick = async () => { await act(() => api(`/enrollments/${x.dataset.stop}/action`, "POST", { action: "stop" }), "Stopped for this person"); batchPage(id); }));
  };
  $("#b-q").oninput = draw;
  draw();
  const a = (action, msg) => act(() => api(`/batches/${id}/action`, "POST", { action }), msg).then(() => batchPage(id));
  const bw = $("#b-win");
  if (bw) bw.onclick = async () => {
    const accs = await api("/accounts");
    const w = { days: b.settings?.days || [0, 1, 2, 3, 4], window_start: b.settings?.window_start || "09:00", window_end: b.settings?.window_end || "16:00", daily_limit: b.settings?.daily_limit || "" };
    const m = modal(`${mhead("When this batch sends")}<div id="mw"></div><div class="hint" style="margin-top:12px">Applies to everything still waiting in this batch, including follow-ups.</div>
      <div class="modal-foot"><button data-close>Cancel</button><button class="primary" id="mw-save">Save</button></div>`);
    windowEditor($("#mw", m), w, accs.find((x) => x.id === b.account_id), () => {});
    $("#mw-save", m).onclick = async () => { await act(() => api(`/batches/${id}/settings`, "POST", w), "Saved"); m.close(); batchPage(id); };
  };
  const p = $("#b-pause"), r = $("#b-resume"), s = $("#b-stop");
  if (p) p.onclick = () => a("pause", "Batch paused - nothing from it will send");
  if (r) r.onclick = () => a("resume", "Batch running again");
  if (s) s.onclick = () => confirm("Stop this batch? Nobody in it gets more emails.") && a("stop", "Batch stopped");
  $("#b-add").onclick = () => {
    const m = modal(`${mhead("Add people to this batch")}<textarea id="ad-t" rows="8" placeholder="jane@acme.com, Jane Doe, Acme Distribution"></textarea>
      <div class="hint" style="margin-top:6px">One per line: email, name, company. They start the sequence from Email 1.</div>
      <div class="modal-foot"><button data-close>Cancel</button><button class="primary" id="ad-go">Add</button></div>`);
    $("#ad-go", m).onclick = async () => {
      const pr = await act(() => api("/batches/parse", "POST", { text: $("#ad-t", m).value }));
      if (!pr.recipients.length) return toast("No email addresses found", "error");
      const res = await act(() => api(`/batches/${id}/add`, "POST", { recipients: pr.recipients }));
      toast(`Added ${res.enrolled}${res.skipped.length ? `, skipped ${res.skipped.length}` : ""}`); m.close(); batchPage(id);
    };
  };
  autoRefresh(() => batchPage(id), 30000);
}

const stars = (n) => `<span class="stars" title="${n} of 5">${"★".repeat(n || 0)}<i>${"★".repeat(5 - (n || 0))}</i></span>`;

async function draftPage(b) {
  const [seqs, accs] = await Promise.all([api("/sequences"), api("/accounts")]);
  const usable = seqs.filter((x) => !x.errors.length).sort((x, y) => (y.settings.rating || 0) - (x.settings.rating || 0) || x.id - y.id);
  const fits = (x) => (x.settings.best_for || "").toLowerCase().includes((b.source || "").split(" ")[0].toLowerCase());
  const pick = usable.find(fits) || usable[0];
  const st = { pid: pick?.id, acc: accs.find((a) => a.active)?.id };
  const ok = b.people.filter((p) => !p.skip);
  page().innerHTML = `
    <a class="back" href="#/batches">← Batches</a>
    <div class="head"><div><div class="row"><h1>${esc(b.name)}</h1><span class="pill info">Ready</span></div>
      <div class="sub" style="margin-top:4px">${b.total} companies · ${esc(b.note || "")}</div></div></div>
    <div class="grid2" style="grid-template-columns:minmax(0,1.15fr) minmax(0,1fr)">
      <div class="card"><h2 style="margin-bottom:14px">1 · Pick an email style</h2>
        <div class="styles">${usable.map((x) => `<label class="style ${x.id === st.pid ? "on" : ""}"><input type="radio" name="sty" value="${x.id}" ${x.id === st.pid ? "checked" : ""}>
          <div class="row" style="gap:8px"><b>${esc(x.name)}</b>${stars(x.settings.rating)}${fits(x) ? `<span class="pill good">fits this batch</span>` : ""}</div>
          <div class="muted" style="font-size:13px">${esc(x.settings.about || "")}</div>
          ${x.settings.best_for ? `<div style="font-size:12.5px;color:var(--ink-2)">Best for: ${esc(x.settings.best_for)}</div>` : ""}</label>`).join("")}</div>
        <h2 style="margin:22px 0 10px">2 · Send from</h2>
        <select id="d-acc" style="min-width:320px">${accs.filter((a) => a.active).map((a) => `<option value="${a.id}">${esc(a.from_name || "")} &lt;${esc(a.email)}&gt;</option>`).join("")}</select>
        <div class="hint" id="d-cap" style="margin-top:6px"></div>
        <h2 style="margin:22px 0 10px">3 · When</h2>
        <div class="row"><span class="wlabel">Start</span><label class="check"><input type="radio" name="d-when" value="now" checked> Now</label>
          <label class="check"><input type="radio" name="d-when" value="at"> Later:</label><input type="datetime-local" id="d-at" value="${toLocalInput()}" disabled></div>
        <div id="d-window" style="margin-top:12px"></div>
        <div class="summary" style="margin-top:22px"><div class="grow" id="d-sum"></div><button class="primary big" id="d-go">Start batch</button></div>
      </div>
      <div class="card"><div class="card-head"><h2>Preview</h2><div class="right"><select id="d-who" style="max-width:240px">${ok.slice(0, 50).map((p, i) => `<option value="${i}">${esc(p.domain || p.email)}</option>`).join("")}</select></div></div>
        <div id="d-preview"></div></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="card-head"><h2>Companies in this batch</h2><div class="right"><input id="d-q" placeholder="Search…" style="width:200px"></div></div>
      <table><thead><tr><th>Company</th><th>Email</th><th>What we know</th><th></th></tr></thead><tbody id="d-rows"></tbody></table></div>`;
  const draw = () => {
    const q = ($("#d-q").value || "").toLowerCase();
    $("#d-rows").innerHTML = b.people.filter((p) => !q || [p.email, p.domain, p.brands, p.category].join(" ").toLowerCase().includes(q)).map((p) => `<tr style="${p.skip ? "opacity:.55" : ""}">
      <td><a href="${esc(p.website || "https://" + p.domain)}" target="_blank">${esc(p.domain || "")}</a></td><td>${esc(p.email)}</td>
      <td class="muted" style="font-size:13px">${[p.brands && "Brand: " + esc(p.brands), p.category && esc(p.category)].filter(Boolean).join(" · ") || "-"}
        ${p.skip ? `<div><span class="pill warn">will be skipped: ${esc(p.skip)}</span></div>` : ""}</td>
      <td class="r"><button class="sm ghost danger" data-rm="${p.contact_id}">Remove</button></td></tr>`).join("");
    $$("[data-rm]").forEach((x) => (x.onclick = async () => { await act(() => api(`/batches/${b.id}/remove`, "POST", { contact_ids: [+x.dataset.rm] }), "Removed"); batchPage(b.id); }));
  };
  const acc = () => accs.find((a) => a.id === st.acc);
  const sum = () => {
    const a = acc(), sq = usable.find((x) => x.id === st.pid);
    const perDay = a ? Math.min(a.daily_limit, +st.win?.daily_limit || a.daily_limit) : 0, days = perDay ? Math.ceil(ok.length / perDay) : 0;
    $("#d-sum").innerHTML = a && sq ? `<b>Send “${esc(sq.name)}” to ${ok.length} companies</b> from ${fromPill(a.email)}<div class="muted" style="font-size:13px;margin-top:4px">${windowText(st.win)} · up to ${perDay}/day → first emails take ~${days} sending day${days === 1 ? "" : "s"}, plus follow-ups. A reply stops it for that company.</div>` : "Choose a style and a mailbox.";
    $("#d-cap").textContent = a ? `${a.sent_today} of ${a.daily_limit} used today` : "";
  };
  const preview = async () => {
    const p = ok[+($("#d-who")?.value || 0)];
    if (!p || !st.pid) return;
    const rec = { email: p.email, first_name: p.first_name, last_name: p.last_name, custom: JSON.parse(p.custom_json || "{}") };
    const r = await api("/batches/preview", "POST", { pipeline_id: st.pid, account_id: st.acc, recipient: rec });
    $("#d-preview").innerHTML = r.emails.map((e, i) => `${i ? `<div class="hint" style="margin:10px 0 10px 14px">⏱ only if no reply after ${dayWord(r.emails[i - 1].wait_days)}:</div>` : ""}
      <div class="mail"><div class="mail-h"><div><span>To</span>${esc(p.email)}</div><div><span>Subject</span><b>${esc(e.subject)}</b></div></div><div class="mail-b">${esc(e.body)}</div></div>`).join("");
  };
  const fromStyle = () => { const x = usable.find((y) => y.id === st.pid)?.settings || {}; return { days: [...(x.days || [0, 1, 2, 3, 4])], window_start: x.window_start || "09:00", window_end: x.window_end || "16:00", daily_limit: "" }; };
  st.win = fromStyle();
  let touched = false;
  const drawWindow = () => windowEditor($("#d-window"), st.win, acc(), () => { touched = true; sum(); });
  $$("[name=sty]").forEach((r) => (r.onchange = () => { st.pid = +r.value; $$(".style").forEach((l) => l.classList.toggle("on", l.contains(r))); if (!touched) { st.win = fromStyle(); drawWindow(); } sum(); preview(); }));
  $("#d-acc").onchange = (e) => { st.acc = +e.target.value; drawWindow(); sum(); preview(); };
  $("#d-who").onchange = preview;
  $$("[name=d-when]").forEach((r) => (r.onchange = () => ($("#d-at").disabled = $("[name=d-when]:checked").value !== "at")));
  $("#d-q").oninput = draw;
  $("#d-go").onclick = async (e) => {
    const sq = usable.find((x) => x.id === st.pid);
    if (!confirm(`Start sending “${sq.name}” to ${ok.length} companies from ${acc()?.email}?`)) return;
    e.target.disabled = true; e.target.textContent = "Starting…";
    try {
      const later = $("[name=d-when]:checked").value === "at";
      const r = await act(() => api(`/batches/${b.id}/start`, "POST", { pipeline_id: st.pid, account_id: st.acc, start_at: later ? fromLocalInput($("#d-at").value) : null, settings: st.win }));
      toast(`Started: ${r.enrolled} companies${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`);
      batchPage(b.id);
    } catch (err) { e.target.disabled = false; e.target.textContent = "Start batch"; }
  };
  drawWindow(); draw(); sum(); preview();
}

function windowText(w) {
  if (!w) return "";
  const d = w.days || [];
  const days = d.length === 7 ? "Every day" : d.join(",") === "0,1,2,3,4" ? "Mon–Fri" : d.map((x) => DAYS[x]).join(", ");
  return `${days}, ${w.window_start}–${w.window_end}`;
}

function windowEditor(el, w, acc, changed) {
  el.innerHTML = `
    <div class="row"><span class="wlabel">Send on</span><div class="days">${DAYS.map((d, i) => `<label><input type="checkbox" data-wd="${i}" ${w.days.includes(i) ? "checked" : ""}>${d}</label>`).join("")}</div></div>
    <div class="row" style="margin-top:10px"><span class="wlabel">Between</span><input type="time" id="w-s" value="${esc(w.window_start)}"><span class="muted">and</span><input type="time" id="w-e" value="${esc(w.window_end)}">
      <span class="hint">recipient's local time</span></div>
    <div class="row" style="margin-top:10px"><span class="wlabel">Max per day</span><input type="number" min="1" id="w-l" style="width:90px" value="${esc(w.daily_limit || "")}" placeholder="${acc ? acc.daily_limit : ""}">
      <span class="hint">for this batch${acc ? ` · the mailbox allows ${acc.daily_limit}/day across all batches` : ""}</span></div>`;
  $$("[data-wd]", el).forEach((c) => (c.onchange = () => { w.days = $$("[data-wd]", el).filter((x) => x.checked).map((x) => +x.dataset.wd); changed(); }));
  $("#w-s", el).onchange = (e) => { w.window_start = e.target.value; changed(); };
  $("#w-e", el).onchange = (e) => { w.window_end = e.target.value; changed(); };
  $("#w-l", el).oninput = (e) => { w.daily_limit = e.target.value; changed(); };
}

async function newBatch() {
  const [seqs, accs] = await Promise.all([api("/sequences"), api("/accounts")]);
  const st = { people: [], errors: [], pid: +(sessionStorage.getItem("nb-seq") || 0) || seqs.find((s) => !s.errors.length)?.id, acc: accs.find((a) => a.active)?.id, pv: 0 };
  page().innerHTML = `
    <a class="back" href="#/batches">← Batches</a>
    <div class="head"><div><h1>New batch</h1><div class="sub">Four quick steps. Nothing is sent until you press Start.</div></div></div>
    <div class="card" style="padding:26px 28px">
      <div class="wsec"><div class="wnum">1</div><div><h2>Who should get it?</h2>
        <textarea id="nb-text" rows="7" placeholder="One person per line:\njane@acme.com, Jane Doe, Acme Distribution\nbob@globex.com, Bob, Globex Supply\n\n…or paste a CSV with a header row (email, first name, company, …)"></textarea>
        <div class="row" style="margin-top:8px"><label class="btn sm" style="cursor:pointer">📄 Upload CSV<input type="file" id="nb-file" accept=".csv,.txt,.tsv" hidden></label>
          <span class="hint" id="nb-count">Name and company are optional - they fill {{first_name}} and {{company}} in the email.</span></div>
        <div id="nb-people" style="margin-top:10px"></div></div></div>
      <div class="wsec"><div class="wnum">2</div><div><h2>Which sequence?</h2>
        <div class="row"><select id="nb-seq" style="min-width:340px">${seqs.map((s) => `<option value="${s.id}" ${s.id === st.pid ? "selected" : ""}>${esc(s.name)}${s.errors.length ? " (needs fixing)" : ""}</option>`).join("")}</select>
          <a class="btn sm" id="nb-edit" href="#">Edit</a><a class="btn sm ghost" href="#/sequences/new">+ New sequence</a></div>
        <div class="flowline" id="nb-flow"></div></div></div>
      <div class="wsec"><div class="wnum">3</div><div><h2>Send from</h2>
        ${accs.length ? `<div class="row"><select id="nb-acc" style="min-width:340px">${accs.filter((a) => a.active).map((a) => `<option value="${a.id}">${esc(a.from_name || "")} &lt;${esc(a.email)}&gt;</option>`).join("")}</select>
          <span class="hint" id="nb-cap"></span></div>` : `<div class="banner bad">Connect a mailbox in <a href="#/settings">Settings</a> first.</div>`}</div></div>
      <div class="wsec"><div class="wnum">4</div><div><h2>When to start?</h2>
        <div class="row"><label class="check"><input type="radio" name="nb-when" value="now" checked> Now</label>
          <label class="check"><input type="radio" name="nb-when" value="at"> Later:</label><input type="datetime-local" id="nb-at" value="${toLocalInput()}" disabled></div>
        <div class="hint" style="margin-top:6px" id="nb-window"></div>
        <label class="field" style="margin-top:12px;max-width:420px"><span>Batch name (optional)</span><input id="nb-name" placeholder="e.g. Pet distributors - October"></label></div></div>
      <div class="wsec"><div class="wnum">✓</div><div><h2>Preview</h2><div id="nb-preview" class="hint">Add people to see their first email.</div></div></div>
      <div class="summary"><div class="grow" id="nb-sum"></div><button class="primary big" id="nb-go" disabled>Start batch</button></div>
    </div>`;
  const seq = () => seqs.find((s) => s.id === st.pid);
  const acc = () => accs.find((a) => a.id === st.acc);
  const usable = () => st.people.filter((p) => !["bounced", "unsubscribed", "do_not_contact"].includes(p.known_status) && !p.busy_in);
  const drawFlow = () => {
    const s = seq();
    $("#nb-flow").innerHTML = s ? flowLine(s.steps) + (s.errors.length ? `<div class="banner warn" style="width:100%;margin:8px 0 0">${s.errors.map(esc).join(" ")} <a class="right" href="#/sequences/${s.id}">Fix it</a></div>` : "") : "";
    $("#nb-edit").href = s ? "#/sequences/" + s.id : "#/sequences";
    const st_ = s?.settings;
    if (st_) $("#nb-window").textContent = `Emails go out ${st_.days.map((d) => DAYS[d]).join(", ")}, ${st_.window_start}-${st_.window_end} (recipient's time), a few minutes apart.`;
  };
  const drawCap = () => {
    const a = acc();
    if (a && $("#nb-cap")) $("#nb-cap").textContent = `${a.sent_today} of ${a.daily_limit} used today · at least ${a.min_gap_minutes} min between emails`;
  };
  const drawPeople = () => {
    const n = st.people.length, u = usable().length;
    $("#nb-count").textContent = n ? `${n} people found${u < n ? ` · ${n - u} will be skipped` : ""}` : "Name and company are optional - they fill {{first_name}} and {{company}} in the email.";
    $("#nb-people").innerHTML = (st.errors.length ? `<div class="banner warn">${st.errors.slice(0, 5).map(esc).join("<br>")}</div>` : "") + (n ? `<div style="max-height:260px;overflow-y:auto;border:1px solid var(--line);border-radius:12px">
      <table><tbody>${st.people.map((p, i) => {
        const skip = ["bounced", "unsubscribed", "do_not_contact"].includes(p.known_status) ? "bounced/unsubscribed before" : p.busy_in ? `already in “${p.busy_in}”` : "";
        return `<tr style="${skip ? "opacity:.55" : ""}"><td>${who(p, p.email)}</td>
          <td>${skip ? `<span class="pill warn">skip: ${esc(skip)}</span>` : p.last_contacted_at ? `<span class="pill">emailed ${ago(p.last_contacted_at)}</span>` : `<span class="pill good">new</span>`}</td>
          <td class="r"><button class="sm ghost" data-pv="${i}">Preview</button> <button class="sm ghost danger" data-rm="${i}">✕</button></td></tr>`;
      }).join("")}</tbody></table></div>` : "");
    $$("[data-rm]").forEach((b) => (b.onclick = () => { st.people.splice(+b.dataset.rm, 1); drawPeople(); drawPreview(); }));
    $$("[data-pv]").forEach((b) => (b.onclick = () => { st.pv = +b.dataset.pv; drawPreview(); $("#nb-preview").scrollIntoView({ behavior: "smooth", block: "center" }); }));
    drawSum();
  };
  const drawSum = () => {
    const u = usable().length, a = acc();
    const daysNeeded = a ? Math.ceil(u / Math.max(1, a.daily_limit)) : 0;
    $("#nb-sum").innerHTML = u && a ? `<b>Send to ${u} ${u === 1 ? "person" : "people"}</b> from ${fromPill(a.email)} using “${esc(seq()?.name || "")}”${daysNeeded > 1 ? ` <span class="muted">· first emails spread over ~${daysNeeded} days (limit ${a.daily_limit}/day)</span>` : ""}` : `<span class="muted">Add people, choose a sequence and a mailbox.</span>`;
    $("#nb-go").disabled = !(u && a && seq() && !seq().errors.length);
  };
  let pvTimer;
  const drawPreview = () => {
    clearTimeout(pvTimer);
    pvTimer = setTimeout(async () => {
      const p = st.people[st.pv] || st.people[0];
      if (!p || !st.pid) { $("#nb-preview").innerHTML = `<span class="hint">Add people to see their first email.</span>`; return; }
      try {
        const r = await api("/batches/preview", "POST", { pipeline_id: st.pid, account_id: st.acc, recipient: p });
        $("#nb-preview").innerHTML = `<div class="hint" style="margin-bottom:8px">What ${esc(p.first_name || p.email)} receives:</div>` + r.emails.map((e, i) => `
          ${i ? `<div class="hint" style="margin:10px 0 10px 14px">⏱ only if no reply after ${dayWord(r.emails[i - 1].wait_days)}:</div>` : ""}
          <div class="mail"><div class="mail-h"><div><span>From</span>${esc(acc()?.from_name || "")} &lt;${esc(acc()?.email || "")}&gt;</div><div><span>To</span>${esc(p.email)}</div><div><span>Subject</span><b>${esc(e.subject)}</b></div></div>
          <div class="mail-b">${esc(e.body)}</div></div>
          ${e.missing.length ? `<div class="banner warn" style="margin-top:6px">Missing ${e.missing.map(esc).join(", ")} for this person - this email will wait for your OK.</div>` : ""}`).join("");
      } catch (e) { $("#nb-preview").innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; }
    }, 250);
  };
  let parseTimer;
  $("#nb-text").oninput = () => {
    clearTimeout(parseTimer);
    parseTimer = setTimeout(async () => {
      const r = await api("/batches/parse", "POST", { text: $("#nb-text").value });
      st.people = r.recipients; st.errors = r.errors; st.pv = 0; drawPeople(); drawPreview();
    }, 350);
  };
  $("#nb-file").onchange = (e) => { const f = e.target.files[0]; if (!f) return; const rd = new FileReader(); rd.onload = () => { $("#nb-text").value = rd.result; $("#nb-text").oninput(); }; rd.readAsText(f); };
  $("#nb-seq").onchange = (e) => { st.pid = +e.target.value; sessionStorage.setItem("nb-seq", st.pid); drawFlow(); drawSum(); drawPreview(); };
  const accSel = $("#nb-acc");
  if (accSel) accSel.onchange = (e) => { st.acc = +e.target.value; drawCap(); drawSum(); drawPreview(); };
  $$("[name=nb-when]").forEach((r) => (r.onchange = () => ($("#nb-at").disabled = r.value !== "at" || !r.checked)));
  $("#nb-go").onclick = async (e) => {
    const later = $("[name=nb-when]:checked").value === "at";
    e.target.disabled = true; e.target.textContent = "Starting…";
    try {
      const r = await act(() => api("/batches", "POST", { pipeline_id: st.pid, account_id: st.acc, recipients: usable(), name: $("#nb-name").value, start_at: later ? fromLocalInput($("#nb-at").value) : null }));
      toast(`Batch started: ${r.enrolled} people${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`);
      location.hash = "#/batches/" + r.id;
    } catch (err) { e.target.disabled = false; e.target.textContent = "Start batch"; }
  };
  drawFlow(); drawCap(); drawPeople();
}

// ================================================================ SEQUENCES
ROUTES.sequences = async function (id) {
  if (id) return sequenceEditor(id === "new" ? null : +id);
  const rows = await api("/sequences");
  page().innerHTML = `
    <div class="head"><div><h1>Email styles</h1><div class="sub">Each style is a first email plus follow-ups. A reply always stops it and notifies you. ★ = how well we expect it to work.</div></div>
      <div class="actions"><a class="btn primary big" href="#/sequences/new">+ New style</a></div></div>
    ${rows.sort((a, b) => (b.settings.rating || 0) - (a.settings.rating || 0) || a.id - b.id).map((s) => `<div class="bcard">
      <div class="row"><h2><a href="#/sequences/${s.id}" style="color:inherit">${esc(s.name)}</a></h2>${s.settings.rating ? stars(s.settings.rating) : ""}${s.errors.length ? `<span class="pill warn">needs fixing</span>` : ""}
        <div class="row" style="margin-left:auto;gap:6px"><a class="btn sm" href="#/sequences/${s.id}">Edit</a><button class="sm ghost" data-dup="${s.id}">Duplicate</button><button class="sm ghost danger" data-del="${s.id}">Delete</button>
        <button class="sm primary" data-use="${s.id}">Use in a batch</button></div></div>
      ${s.settings.about ? `<div class="sub" style="margin-top:6px">${esc(s.settings.about)}${s.settings.best_for ? ` <span class="muted">· Best for: ${esc(s.settings.best_for)}</span>` : ""}</div>` : ""}
      <div class="flowline">${flowLine(s.steps)}</div>
      <div class="stats" style="margin-top:14px"><div><b>${s.people}</b><span>people</span></div><div><b>${s.sent}</b><span>emails sent</span></div><div><b>${s.replied}</b><span>replied</span></div><div><b>${s.reply_rate}%</b><span>reply rate</span></div></div>
    </div>`).join("") || `<div class="card empty">No sequences yet.</div>`}`;
  $$("[data-use]").forEach((b) => (b.onclick = () => { sessionStorage.setItem("nb-seq", b.dataset.use); location.hash = "#/batches/new"; }));
  $$("[data-dup]").forEach((b) => (b.onclick = async () => { await act(() => api(`/sequences/${b.dataset.dup}/duplicate`, "POST"), "Duplicated"); route(); }));
  $$("[data-del]").forEach((b) => (b.onclick = async () => { if (!confirm("Delete this sequence?")) return; const r = await act(() => api(`/sequences/${b.dataset.del}`, "DELETE")); toast(r.note || "Deleted"); route(); }));
};

const VARS = [["first_name|there", "First name"], ["company", "Company"], ["full_name", "Full name"], ["sender_first_name", "My first name"]];

async function sequenceEditor(id) {
  let S = id ? await api("/sequences/" + id) : { name: "New sequence", steps: [{ subject: "", body: "Hi {{first_name|there}},\n\n", wait_days: 3 }], settings: { days: [0, 1, 2, 3, 4], window_start: "09:00", window_end: "17:00", jitter_minutes: 20, stop_company_on_reply: true } };
  S = JSON.parse(JSON.stringify(S));
  DIRTY = false;
  const dirty = () => { DIRTY = true; $("#sv-state").textContent = "Unsaved changes"; };
  const draw = () => {
    const st = S.settings, n = S.steps.length;
    page().innerHTML = `
      <a class="back" href="#/sequences">← Sequences</a>
      <div class="head"><div class="grow"><input id="sq-name" value="${esc(S.name)}" style="font-size:24px;font-weight:700;border:none;padding:4px 0;width:100%;box-shadow:none"></div></div>
      <div class="card" style="margin-bottom:22px"><div class="row" style="gap:18px">
        <span class="sub" style="font-weight:600">Send on</span><div class="days">${DAYS.map((d, i) => `<label><input type="checkbox" data-day="${i}" ${st.days.includes(i) ? "checked" : ""}>${d}</label>`).join("")}</div>
        <span class="sub" style="font-weight:600">between</span><input type="time" id="sq-ws" value="${esc(st.window_start)}"> <span class="sub">and</span> <input type="time" id="sq-we" value="${esc(st.window_end)}">
        <span class="hint">in the recipient's time zone</span></div></div>
      <div id="steps">${S.steps.map((s, i) => `
        <div class="step"><div class="step-head"><span class="step-num">${i + 1}</span><b>${i ? "Follow-up " + i : "First email"}</b>
          ${i ? `<label class="check" style="margin-left:14px;font-size:13.5px;color:var(--ink-2)"><input type="checkbox" data-same="${i}" ${s.same_thread !== false ? "checked" : ""}> reply in the same thread</label>` : ""}
          ${i ? `<button class="sm ghost danger" style="margin-left:auto" data-rm="${i}">Remove</button>` : ""}</div>
          <div class="step-body">
            ${i === 0 || s.same_thread === false ? `<label class="field"><span>Subject</span><input data-subj="${i}" value="${esc(s.subject || "")}" placeholder="e.g. Wholesale account - Eigenreach"></label>` : `<div class="hint">Subject: “Re: ${esc(S.steps[0].subject || "…")}” - sent as a reply under your first email.</div>`}
            <label class="field"><span>Message</span><textarea data-body="${i}" rows="${i ? 6 : 10}">${esc(s.body || "")}</textarea></label>
            <div class="chips"><span class="hint">Insert:</span>${VARS.map(([v, l]) => `<button data-ins="${i}" data-v="${v}">${l}</button>`).join("")}<span class="hint">· your mailbox signature is added automatically</span></div>
          </div></div>
        <div class="link">
          <div class="rule reply"><span class="ic">💬</span><b>If they reply</b> → you get a notification and the sequence stops for them.</div>
          <div class="rule wait"><span class="ic">⏱</span><b>If no reply after</b> <input class="inline" type="number" min="1" step="1" data-wait="${i}" value="${s.wait_days}"> days →
            ${i < n - 1 ? `send <b>Follow-up ${i + 1}</b> ↓` : `<span>finish (marked “no reply”).</span> <button class="sm" id="add-step">+ Add a follow-up instead</button>`}</div>
        </div>`).join("")}</div>
      <div class="savebar"><button class="primary big" id="sv">Save sequence</button><button id="pv">Preview</button>
        <span class="hint" id="sv-state">${DIRTY ? "Unsaved changes" : ""}</span><span class="grow"></span>
        ${id ? `<button class="ghost" id="use">Use in a batch →</button>` : ""}</div>`;
    $("#sq-name").oninput = (e) => { S.name = e.target.value; dirty(); };
    $$("[data-day]").forEach((c) => (c.onchange = () => { st.days = $$("[data-day]").filter((x) => x.checked).map((x) => +x.dataset.day); dirty(); }));
    $("#sq-ws").onchange = (e) => { st.window_start = e.target.value; dirty(); };
    $("#sq-we").onchange = (e) => { st.window_end = e.target.value; dirty(); };
    $$("[data-subj]").forEach((x) => (x.oninput = () => { S.steps[+x.dataset.subj].subject = x.value; dirty(); }));
    $$("[data-body]").forEach((x) => (x.oninput = () => { S.steps[+x.dataset.body].body = x.value; dirty(); }));
    $$("[data-wait]").forEach((x) => (x.oninput = () => { S.steps[+x.dataset.wait].wait_days = +x.value; dirty(); }));
    $$("[data-same]").forEach((x) => (x.onchange = () => { S.steps[+x.dataset.same].same_thread = x.checked; dirty(); draw(); }));
    $$("[data-rm]").forEach((x) => (x.onclick = () => { S.steps.splice(+x.dataset.rm, 1); dirty(); draw(); }));
    $$("[data-ins]").forEach((b) => (b.onclick = () => {
      const ta = $(`[data-body="${b.dataset.ins}"]`), ins = `{{${b.dataset.v}}}`, p = ta.selectionStart ?? ta.value.length;
      ta.value = ta.value.slice(0, p) + ins + ta.value.slice(ta.selectionEnd ?? p);
      ta.focus(); ta.selectionStart = ta.selectionEnd = p + ins.length; ta.dispatchEvent(new Event("input"));
    }));
    const add = $("#add-step");
    if (add) add.onclick = () => { S.steps.push({ same_thread: true, body: "Hi {{first_name|there}},\n\nJust following up on my note below.\n\nThanks,\n", wait_days: 3 }); dirty(); draw(); };
    $("#sv").onclick = save;
    $("#pv").onclick = preview;
    const use = $("#use");
    if (use) use.onclick = () => { sessionStorage.setItem("nb-seq", id); location.hash = "#/batches/new"; };
  };
  const save = async () => {
    const body = { name: S.name, steps: S.steps, settings: S.settings };
    const r = id ? await act(() => api("/sequences/" + id, "PUT", body)) : await act(() => api("/sequences", "POST", body));
    DIRTY = false;
    if (r.errors?.length) toast("Saved, but fix: " + r.errors.join(" "), "error"); else toast("Saved ✓");
    if (!id) { location.hash = "#/sequences/" + r.id; return; }
    S = await api("/sequences/" + id); draw();
  };
  const preview = async () => {
    if (DIRTY || !id) { await save(); if (!id) return; }
    const acc = (STATE?.accounts || [])[0];
    const r = await act(() => api("/batches/preview", "POST", { pipeline_id: id, account_id: acc?.id, recipient: { email: "jane@acmedistribution.com", first_name: "Jane", last_name: "Doe", company: "Acme Distribution" } }));
    modal(`${mhead("Preview · sample person Jane Doe, Acme Distribution")}${r.emails.map((e, i) => `
      ${i ? `<div class="hint" style="margin:10px 0 10px 14px">⏱ only if no reply after ${dayWord(r.emails[i - 1].wait_days)}:</div>` : ""}
      <div class="mail"><div class="mail-h"><div><span>From</span>${esc(acc?.email || "")}</div><div><span>Subject</span><b>${esc(e.subject)}</b></div></div><div class="mail-b">${esc(e.body)}</div></div>`).join("")}
      <div class="modal-foot"><button data-close>Close</button></div>`, { wide: true });
  };
  draw();
}

// ================================================================ COMPANIES (progress)
const CST = {
  ready: ["Ready in a batch", "info"], not_in_batch: ["Not in a batch", ""], scheduled: ["First email scheduled", "info"],
  emailing: ["Emailing", "info"], no_reply: ["No reply - finished", ""], replied: ["Replied", "good"], interested: ["Interested", "good"],
  bounced: ["Bounced", "bad"], form_todo: ["Form to fill", "warn"], form_submitted: ["Form submitted", "good"],
  form_not_possible: ["Form not possible", "bad"], contacted_before: ["Contacted before (CRM)", ""], duplicate: ["Duplicate - skipped", ""],
  no_contact: ["No email or form", ""],
};
ROUTES.companies = async function () {
  const ov = await api("/companies/overview");
  const st = { status: sessionStorage.getItem("cs") || "", source: sessionStorage.getItem("csrc") || "", q: "", page: 0 };
  const sum = (src, keys) => keys.reduce((a, k) => a + (ov[src]?.[k] || 0), 0);
  const all = (keys) => Object.keys(ov).reduce((a, s) => a + sum(s, keys), 0);
  const E = ["ready", "not_in_batch", "scheduled", "emailing", "no_reply", "replied", "interested", "bounced"];
  const SENT = ["emailing", "no_reply", "replied", "interested", "bounced"];
  const REP = ["replied", "interested"];
  const FORM = ["form_todo", "form_submitted", "form_not_possible"];
  page().innerHTML = `
    <div class="head"><div><h1>Companies</h1><div class="sub">Every supplier from your spreadsheets and where it stands. One company gets one email style - never two. Grey notes show what another team did earlier (background only).</div></div>
      <div class="actions"><a class="btn" href="/api/export/companies.csv">⇩ Export</a></div></div>
    <div class="tiles">
      <div class="tile"><div class="l">To email</div><div class="v">${all(E)}</div><div class="h">${all(["ready"])} waiting in ready batches</div></div>
      <div class="tile"><div class="l">Emailed</div><div class="v">${all(SENT)}</div><div class="h">first email sent</div></div>
      <div class="tile"><div class="l">Forms to fill</div><div class="v">${all(["form_todo"])}</div><div class="h">${all(["form_submitted"])} submitted · see the forms spreadsheet</div></div>
      <div class="tile"><div class="l">Replied</div><div class="v">${all(REP)}</div><div class="h">${all(["interested"])} interested · ${all(["bounced"])} bounced</div></div>
    </div>
    <div class="card"><h2 style="margin-bottom:10px">Progress by list</h2>
      <table><thead><tr><th>List</th><th class="r">To email</th><th class="r">Emailed</th><th class="r">Replied</th><th class="r">Forms to fill</th><th class="r">Forms done</th><th class="r">No email or form</th></tr></thead><tbody>
      ${Object.keys(ov).sort().map((s) => `<tr style="cursor:pointer" data-src="${esc(s)}"><td><b>${esc(s)}</b></td><td class="r num">${sum(s, E)}</td><td class="r num">${sum(s, SENT)}</td>
        <td class="r num">${sum(s, REP)}</td><td class="r num">${sum(s, ["form_todo"])}</td><td class="r num">${sum(s, ["form_submitted"])}</td>
        <td class="r num muted">${sum(s, ["no_contact", "form_not_possible"])}</td></tr>`).join("")}</tbody></table></div>
    <div class="card"><div class="card-head"><div class="seg" id="c-tabs">${[["", "All"], ["email", "Email"], ["replied", "Replied"], ["forms", "Forms"], ["skipped", "No email or form"]].map(([k, l]) => `<a href="#" data-st="${k}" class="${k === st.status ? "active" : ""}">${l}</a>`).join("")}</div>
      <div class="right"><select id="c-src"><option value="">All lists</option>${Object.keys(ov).sort().map((s) => `<option ${s === st.source ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
      <input id="c-q" placeholder="Search site or email…" style="width:220px"></div></div>
      <div id="c-list"></div></div>`;
  const load = async () => {
    const d = await api(`/companies/list?status=${st.status}&source=${encodeURIComponent(st.source)}&q=${encodeURIComponent(st.q)}&page=${st.page}`);
    const pages = Math.ceil(d.total / 100);
    $("#c-list").innerHTML = `<div style="overflow-x:auto"><table><thead><tr><th>Company</th><th>Email</th><th>Status</th><th>Batch / note</th><th>Last activity</th><th></th></tr></thead><tbody>
      ${d.rows.map((r) => `<tr><td><a href="${esc(r.website || "https://" + r.domain)}" target="_blank">${esc(r.domain)}</a><div class="muted" style="font-size:12px">${esc(r.source)}</div></td>
        <td style="font-size:13.5px"><div class="ell" style="max-width:220px" title="${esc(r.email || "")}">${esc(r.email || "-")}</div></td>
        <td><span class="pill ${CST[r.st]?.[1] ?? ""}">${esc(CST[r.st]?.[0] ?? r.st)}</span></td>
        <td class="muted" style="font-size:12.5px;max-width:240px">${r.batch ? esc(r.batch) : ""}${r.crm_note ? `<div class="ell" style="max-width:240px" title="${esc(r.crm_note)}">${esc(r.crm_note)}</div>` : ""}</td>
        <td class="muted" style="font-size:13px;white-space:nowrap">${r.last_ts ? when(r.last_ts) : "-"}</td>
        <td class="r" style="white-space:nowrap">${r.reply_id ? `<a class="btn sm" href="#/replies/${r.reply_id}">Reply</a>` : ""}
          ${r.plan === "form" ? `<a class="btn sm" href="${esc(r.form_url)}" target="_blank">Open form</a>
            ${r.form_status === "submitted" ? `<button class="sm ghost" data-form="${r.id}" data-v="">Undo</button>` : `<button class="sm" data-form="${r.id}" data-v="submitted">✓ Submitted</button><button class="sm ghost" data-form="${r.id}" data-v="not_possible">Can't</button>`}` : ""}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">Nothing here.</td></tr>`}
      </tbody></table></div>
      ${pages > 1 ? `<div class="row" style="justify-content:center;margin-top:12px"><button class="sm" id="c-prev" ${st.page ? "" : "disabled"}>← Prev</button><span class="muted">Page ${st.page + 1} of ${pages} · ${d.total} companies</span><button class="sm" id="c-next" ${st.page + 1 < pages ? "" : "disabled"}>Next →</button></div>` : `<div class="muted" style="text-align:center;margin-top:10px">${d.total} companies</div>`}`;
    $$("[data-form]").forEach((x) => (x.onclick = async () => { await act(() => api(`/companies/${x.dataset.form}/form`, "POST", { status: x.dataset.v }), x.dataset.v === "submitted" ? "Marked as submitted" : "Updated"); load(); }));
    const pv = $("#c-prev"), nx = $("#c-next");
    if (pv) pv.onclick = () => { st.page--; load(); };
    if (nx) nx.onclick = () => { st.page++; load(); };
  };
  $$("#c-tabs a").forEach((a) => (a.onclick = (e) => { e.preventDefault(); st.status = a.dataset.st; st.page = 0; sessionStorage.setItem("cs", st.status); $$("#c-tabs a").forEach((x) => x.classList.toggle("active", x === a)); load(); }));
  $("#c-src").onchange = (e) => { st.source = e.target.value; st.page = 0; sessionStorage.setItem("csrc", st.source); load(); };
  $$("[data-src]").forEach((tr) => (tr.onclick = () => { st.source = tr.dataset.src; st.page = 0; $("#c-src").value = st.source; sessionStorage.setItem("csrc", st.source); load(); $("#c-list").scrollIntoView({ behavior: "smooth" }); }));
  let t;
  $("#c-q").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { st.q = e.target.value; st.page = 0; load(); }, 300); };
  load();
};

// ================================================================ SETTINGS
ROUTES.settings = async function () {
  const [s, accs] = await Promise.all([api("/settings"), api("/accounts")]);
  page().innerHTML = `
    <div class="head"><div><h1>Settings</h1><div class="sub">Passwords and keys are kept in your Mac's Keychain.</div></div></div>
    <div class="card"><div class="card-head"><h2>Mailboxes you send from</h2><div class="right"><button class="primary" id="acc-add">+ Add mailbox</button></div></div>
      ${accs.length ? `<table><tbody>${accs.map((a) => `<tr><td>${fromPill(a.email)} <span class="muted" style="margin-left:6px">${esc(a.from_name || "")}</span></td>
        <td class="muted">${a.sent_today} / ${a.daily_limit} today · ${a.min_gap_minutes} min apart</td>
        <td>${a.password_set ? `<span class="pill good">Connected</span>` : `<span class="pill bad">No password</span>`}${a.active ? "" : ` <span class="pill">off</span>`}</td>
        <td class="r" style="white-space:nowrap"><button class="sm" data-test="${a.id}">Test</button> <button class="sm" data-edit="${a.id}">Edit</button> <button class="sm ghost danger" data-del="${a.id}">Remove</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">No mailbox yet.</div>`}
      <details class="guide" style="margin-top:12px"><summary class="sub" style="cursor:pointer">How to connect a Google Workspace mailbox</summary><ol>
        <li>At <a href="https://myaccount.google.com/security" target="_blank">myaccount.google.com/security</a> turn on <b>2-Step Verification</b>.</li>
        <li>At <a href="https://myaccount.google.com/apppasswords" target="_blank">myaccount.google.com/apppasswords</a> create a password named “EmailManager” and copy the 16 letters.</li>
        <li>Click <b>Add mailbox</b>, paste it, then <b>Test</b>.</li>
        <li>To send as an alias (e.g. info@), add it in Gmail → Settings → Accounts → “Send mail as”, then add it here as another mailbox with the same login.</li></ol></details></div>

    <div class="card"><h2 style="margin-bottom:12px">Test mode</h2>
      <label class="check"><input type="checkbox" data-k="test_mode" ${s.test_mode ? "checked" : ""}> Send every email to my test address instead of the real person</label>
      <label class="field" style="margin-top:12px;max-width:380px"><span>Test address</span><input data-k="test_address" value="${esc(s.test_address)}"></label></div>

    <div class="card"><h2 style="margin-bottom:12px">Notifications</h2>
      <div class="row" style="gap:20px"><label class="check"><input type="checkbox" data-k="notify_macos" ${s.notify_macos ? "checked" : ""}> Mac notifications</label>
        <button class="sm" id="n-test">Send a test notification</button></div>
      <label class="field" style="margin-top:12px;max-width:380px"><span>Phone alerts (optional): ntfy topic</span><input data-k="notify_ntfy_topic" value="${esc(s.notify_ntfy_topic)}" placeholder="a long random name"></label>
      <div class="hint" style="margin-top:6px">Install the free “ntfy” app on your phone and subscribe to the same topic name.</div></div>

    <div class="card"><h2 style="margin-bottom:12px">Claude (reads and summarises replies)</h2>
      <div class="row"><input type="password" id="ai-key" style="min-width:340px" placeholder="${s.anthropic_key_set ? "API key saved ✓ (paste to replace)" : "Anthropic API key: sk-ant-…"}">
        <button id="ai-save">Save key</button><button class="ghost" id="ai-test">Test</button><span class="hint" id="ai-out"></span></div>
      <label class="field" style="margin-top:14px"><span>About your business (helps Claude judge replies and write answers)</span><textarea data-k="business_context" rows="4">${esc(s.business_context)}</textarea></label></div>`;
  const save = (k, v) => act(() => api("/settings", "POST", { [k]: v }), "Saved").then(refreshState);
  $$("[data-k]").forEach((el) => (el.onchange = () => save(el.dataset.k, el.type === "checkbox" ? el.checked : el.value)));
  $("#n-test").onclick = () => act(() => api("/notifications/test", "POST"), "Sent - check your Mac");
  $("#ai-save").onclick = async () => { const v = $("#ai-key").value.trim(); if (!v) return toast("Paste a key first"); await act(() => api("/settings", "POST", { anthropic_api_key: v }), "Key saved"); ROUTES.settings(); };
  $("#ai-test").onclick = async () => { $("#ai-out").textContent = "Asking Claude…"; try { const r = await api("/settings/test-ai", "POST"); $("#ai-out").textContent = `✓ ${r.analysis.category}: ${r.analysis.summary}`; } catch (e) { $("#ai-out").textContent = "✗ " + e.message; } };
  $("#acc-add").onclick = () => mailboxForm({});
  $$("[data-edit]").forEach((b) => (b.onclick = () => mailboxForm(accs.find((a) => a.id == b.dataset.edit))));
  $$("[data-del]").forEach((b) => (b.onclick = async () => { if (!confirm("Remove this mailbox?")) return; const r = await act(() => api("/accounts/" + b.dataset.del, "DELETE")); toast(r.note || "Removed"); ROUTES.settings(); refreshState(); }));
  $$("[data-test]").forEach((b) => (b.onclick = async () => {
    b.disabled = true; b.textContent = "Testing…";
    try { const r = await api(`/accounts/${b.dataset.test}/test`, "POST"); const okS = r.smtp === "ok", okI = String(r.imap).startsWith("ok"); toast(okS && okI ? "✓ Sending and receiving both work" : `Sending: ${r.smtp} · Receiving: ${r.imap}`, okS && okI ? "" : "error"); }
    catch (e) { toast(e.message, "error"); }
    b.disabled = false; b.textContent = "Test";
  }));
};

function mailboxForm(a) {
  const m = modal(`${mhead(a.id ? "Edit mailbox" : "Add mailbox")}
    <div class="form">
      <label class="field"><span>Send from (address people see)</span><input id="f-email" value="${esc(a.email || "")}" placeholder="wholesale@eigenreach.com"></label>
      <label class="field"><span>Your name</span><input id="f-name" value="${esc(a.from_name || "Oleg Presnyakov")}"></label>
      <label class="field"><span>Google login (if different)</span><input id="f-user" value="${esc(a.username && a.username !== a.email ? a.username : "")}" placeholder="same as above"></label>
      <label class="field"><span>App password ${a.password_set ? "(saved)" : ""}</span><input id="f-pw" type="password" placeholder="${a.password_set ? "leave empty to keep" : "16 letters from Google"}"></label>
      <label class="field"><span>Max emails per day</span><input id="f-lim" type="number" min="1" value="${a.daily_limit ?? 30}"></label>
      <label class="field"><span>Minutes between emails</span><input id="f-gap" type="number" min="0" step="0.5" value="${a.min_gap_minutes ?? 4}"></label>
    </div>
    <label class="field" style="margin-top:14px"><span>Signature</span><textarea id="f-sig" rows="5">${esc(a.signature ?? "Oleg Presnyakov\nFounder, Eigenreach\nwholesale@eigenreach.com · eigenreach.com\n540 Thompson St, Ann Arbor, MI 48104")}</textarea></label>
    <label class="check" style="margin-top:10px"><input type="checkbox" id="f-on" ${a.active === 0 ? "" : "checked"}> Active</label>
    <div class="modal-foot"><button data-close>Cancel</button><button class="primary" id="f-save">Save</button></div>`);
  $("#f-save", m).onclick = async () => {
    const email = $("#f-email", m).value.trim();
    const body = { email, from_name: $("#f-name", m).value, username: $("#f-user", m).value.trim() || email, password: $("#f-pw", m).value, daily_limit: +$("#f-lim", m).value, min_gap_minutes: +$("#f-gap", m).value, signature: $("#f-sig", m).value, active: $("#f-on", m).checked ? 1 : 0 };
    if (a.id) await act(() => api("/accounts/" + a.id, "PUT", body), "Saved"); else await act(() => api("/accounts", "POST", body), "Mailbox added - press Test");
    m.close(); ROUTES.settings(); refreshState();
  };
}

// ================================================================ boot
refreshState().then(route);
setInterval(refreshState, 15000);
