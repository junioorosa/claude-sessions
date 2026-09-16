import { $, el, esc, basename, relTime, fmtDate, fmtCost, fmtDuration, toast, api, mutate, copyText } from "./util.js";
import { renderTranscript, applyToggles } from "./transcript.js";
import { loadPrefs, savePrefs, isDefaultPrefs, passes, sortRecs, bucketsOf, renderFilterMenu } from "./filters.js";
import { connectEvents } from "./live.js";

const POLL_MS = 30000;

const S = {
  sessions: [], byId: {}, projects: [],
  ui: { pinned: [], groups: [], titles: {}, hidden: [] },
  prefs: loadPrefs(), collapsed: loadCollapsed(), scroll: new Map(),
  selected: null, filter: "", hits: null, hitsQuery: "", resuming: new Set(),
};

function loadCollapsed() {
  try { return new Set(JSON.parse(localStorage.getItem("sessions.collapsed") || "[]")); } catch (e) { return new Set(); }
}

function saveCollapsed() {
  try { localStorage.setItem("sessions.collapsed", JSON.stringify([...S.collapsed])); } catch (e) { /* per-viewer convenience only */ }
}

function savedScroll(id) {
  if (S.scroll.has(id)) return S.scroll.get(id);
  try { const v = sessionStorage.getItem("sessions.scroll." + id); return v === null ? null : Number(v); } catch (e) { return null; }
}

function rememberScroll(id, top) {
  S.scroll.set(id, top);
  try { sessionStorage.setItem("sessions.scroll." + id, String(top)); } catch (e) { /* ignore */ }
}

// ---------- state ----------

const displayTitle = (rec) => S.ui.titles[rec.id] || rec.title || "(untitled)";
const branchOf = (rec) => (rec.gitBranch && rec.gitBranch !== "HEAD" ? rec.gitBranch : "");
const resumeCommand = (rec) => "claude --resume " + rec.id;

const groupById = (groupId) => S.ui.groups.find((g) => g.id === groupId);

function locationOf(id) {
  if (S.ui.pinned.includes(id)) return { kind: "pinned" };
  for (const group of S.ui.groups) if (group.sessions.includes(id)) return { kind: "group", groupId: group.id };
  return { kind: "ungrouped" };
}

function moveTo(id, target, beforeId) {
  S.ui.pinned = S.ui.pinned.filter((s) => s !== id);
  for (const group of S.ui.groups) group.sessions = group.sessions.filter((s) => s !== id);
  const group = target.kind === "group" ? groupById(target.groupId) : null;
  const list = target.kind === "pinned" ? S.ui.pinned : group ? group.sessions : null;
  if (list) {
    const at = beforeId ? list.indexOf(beforeId) : -1;
    if (at >= 0) list.splice(at, 0, id);
    else list.push(id);
  }
  saveState();
}

let saveTimer = null;
function saveState() {
  renderSidebar();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    mutate("PUT", "/api/state", S.ui).then((state) => { S.ui = state; renderSidebar(); }).catch((e) => toast("Could not save: " + e.message, true));
  }, 150);
}

function setPrefs(patch) {
  S.prefs = { ...S.prefs, ...patch };
  savePrefs(S.prefs);
  renderSidebar();
  openFilterMenu();
}

async function loadAll() {
  const [index, state] = await Promise.all([api("/api/sessions"), api("/api/state")]);
  S.ui = state;
  applyIndex(index);
}

function applyIndex(index) {
  S.sessions = index.sessions;
  S.byId = Object.fromEntries(index.sessions.map((r) => [r.id, r]));
  const projects = new Map();
  for (const rec of index.sessions) if (rec.project) projects.set(rec.project, basename(rec.project));
  S.projects = [...projects.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  $("#config-dir").textContent = index.configDir;
  $("#config-dir").title = index.configDir;
  renderSidebar();
}

async function refreshSessions(manual) {
  const button = $("#refresh");
  const spinUntil = Date.now() + 600;  // one visible turn even when the fetch is instant
  if (manual) button.classList.add("spinning");
  try {
    const before = S.selected && S.byId[S.selected] ? S.byId[S.selected].updatedAt : null;
    applyIndex(await api("/api/sessions"));
    const after = S.selected && S.byId[S.selected] ? S.byId[S.selected].updatedAt : null;
    if (S.selected && before && after && before !== after) reloadTranscript();
    if (manual) toast("Refreshed: " + S.sessions.length + " sessions, " + S.sessions.filter((r) => r.live).length + " running");
  } catch (e) {
    toast("Refresh failed: " + e.message, true);
  } finally {
    setTimeout(() => button.classList.remove("spinning"), Math.max(0, spinUntil - Date.now()));
  }
}

// ---------- sidebar ----------

function visible(rec, pinned) {
  if (S.hits && !S.hits[rec.id]) return false;
  if (S.filter) {
    const hay = [displayTitle(rec), rec.title, rec.project, rec.gitBranch, rec.id, rec.firstPrompt].join(" ").toLowerCase();
    if (!hay.includes(S.filter)) return false;
  }
  return passes(rec, S.prefs, new Set(S.ui.hidden), pinned);
}

function renderSidebar() {
  const nav = $("#sections");
  const scroll = nav.scrollTop;
  nav.innerHTML = "";
  $("#filter").classList.toggle("active", !isDefaultPrefs(S.prefs));
  const placed = new Set();
  const pinned = sortRecs(S.ui.pinned.map((id) => S.byId[id]).filter(Boolean).filter((r) => visible(r, true)), { sort: "manual" }, displayTitle);
  S.ui.pinned.forEach((id) => placed.add(id));
  nav.appendChild(bucket({ kind: "pinned" }, "pinned", "Pinned", "", pinned, "Drag sessions here to pin them"));
  if (S.prefs.group === "custom") {
    for (const group of S.ui.groups) {
      const recs = group.sessions.map((id) => S.byId[id]).filter(Boolean);
      recs.forEach((r) => placed.add(r.id));
      nav.appendChild(bucket({ kind: "group", groupId: group.id }, "group:" + group.id, group.name, "", sortRecs(recs.filter((r) => visible(r, false)), { sort: "manual" }, displayTitle), "Drag sessions here"));
    }
    const rest = sortRecs(S.sessions.filter((r) => !placed.has(r.id) && visible(r, false)), S.prefs, displayTitle);
    nav.appendChild(bucket({ kind: "ungrouped" }, "rest", "Ungrouped", "", rest, S.hits ? "No matches" : "No sessions"));
  } else {
    const rest = sortRecs(S.sessions.filter((r) => !placed.has(r.id) && visible(r, false)), S.prefs, displayTitle);
    const buckets = bucketsOf(rest, S.prefs);
    if (!buckets.length) nav.appendChild(el("div", { class: "section-empty", text: S.hits ? "No matches" : "No sessions match the filters" }));
    for (const b of buckets) nav.appendChild(bucket({ kind: "ungrouped" }, S.prefs.group + ":" + b.key, b.label, b.title, b.recs, null));
  }
  nav.scrollTop = scroll;
}

function bucket(target, key, label, title, recs, emptyText) {
  const collapsed = S.collapsed.has(key);
  const liveCount = recs.filter((r) => r.live).length;
  const head = el("div", { class: "bucket-head", title }, [
    el("span", { class: "name", text: label }),
    el("span", { class: "caret", text: collapsed ? "›" : "⌄" }),
    liveCount ? el("span", { class: "live-count", text: String(liveCount) }) : null,
    el("span", { class: "grow" }),
  ]);
  if (target.kind === "group") {
    head.appendChild(el("span", { class: "actions" }, [
      el("button", { title: "Rename group", text: "✎", onclick: (e) => { e.stopPropagation(); renameGroup(target.groupId); } }),
      el("button", { title: "Delete group (sessions go back to the list)", text: "×", onclick: (e) => { e.stopPropagation(); deleteGroup(target.groupId); } }),
    ]));
  }
  head.addEventListener("click", () => {
    if (S.collapsed.has(key)) S.collapsed.delete(key); else S.collapsed.add(key);
    saveCollapsed();
    renderSidebar();
  });
  const box = el("div", { class: "bucket" + (collapsed ? " collapsed" : ""), "data-kind": target.kind }, [head]);
  if (!recs.length && emptyText) box.appendChild(el("div", { class: "section-empty", text: emptyText }));
  for (const rec of recs) box.appendChild(row(rec, target));
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("drop-target"); });
  box.addEventListener("dragleave", (e) => { if (!box.contains(e.relatedTarget)) box.classList.remove("drop-target"); });
  box.addEventListener("drop", (e) => {
    e.preventDefault();
    box.classList.remove("drop-target");
    const id = e.dataTransfer.getData("text/plain");
    if (id && S.byId[id]) moveTo(id, target, null);
  });
  return box;
}

function rowTooltip(rec) {
  const parts = [rec.project, branchOf(rec), fmtDate(rec.updatedAt), rec.costUSD ? fmtCost(rec.costUSD) : "", rec.live ? "running (pid " + rec.live.pid + ")" : ""];
  return rec.title + "\n" + parts.filter(Boolean).join(" · ") + (rec.firstPrompt ? "\n\n" + rec.firstPrompt : "");
}

function row(rec, target) {
  const body = el("div", { class: "body" }, [el("div", { class: "title", text: displayTitle(rec) })]);
  if (S.hits && S.hits[rec.id]) {
    for (const snippet of S.hits[rec.id].slice(0, 2)) body.appendChild(el("div", { class: "snippet", html: highlight(snippet.text, S.hitsQuery) }));
  }
  const node = el("div", {
    class: "row" + (rec.id === S.selected ? " selected" : "") + (S.ui.hidden.includes(rec.id) ? " hidden-session" : ""),
    draggable: "true", "data-id": rec.id, title: rowTooltip(rec),
  }, [
    el("span", { class: "dot" + (rec.live ? " live" : target.kind === "pinned" ? " pinned" : "") }),
    body,
    el("button", { class: "kebab", title: "More", text: "⋮", onclick: (e) => { e.stopPropagation(); openMenu(rec, e.currentTarget.getBoundingClientRect()); } }),
  ]);
  node.addEventListener("click", () => select(rec.id));
  node.addEventListener("dblclick", () => resumeSession(rec, false));
  node.addEventListener("contextmenu", (e) => { e.preventDefault(); openMenu(rec, { left: e.clientX, bottom: e.clientY }); });
  node.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", rec.id); e.dataTransfer.effectAllowed = "move"; });
  if (target.kind !== "ungrouped") {
    node.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); node.classList.add("drop-before"); });
    node.addEventListener("dragleave", () => node.classList.remove("drop-before"));
    node.addEventListener("drop", (e) => {
      e.preventDefault(); e.stopPropagation();
      node.classList.remove("drop-before");
      const id = e.dataTransfer.getData("text/plain");
      if (id && id !== rec.id && S.byId[id]) moveTo(id, target, rec.id);
    });
  }
  return node;
}

function highlight(text, q) {
  const safe = esc(text);
  if (!q) return safe;
  const pattern = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "ig");
  return safe.replace(pattern, (m) => "<mark>" + m + "</mark>");
}

// ---------- menus ----------

function openFilterMenu() {
  const menu = $("#filter-menu");
  renderFilterMenu(menu, S.prefs, S.projects, setPrefs, () => { closeMenus(); newGroup(null); });
  menu.hidden = false;
  const anchor = $("#filter").getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(anchor.left, window.innerWidth - menu.offsetWidth - 8)) + "px";
  menu.style.top = anchor.bottom + 6 + "px";
}

function openMenu(rec, anchor) {
  const menu = $("#menu");
  menu.innerHTML = "";
  const where = locationOf(rec.id);
  const item = (label, action, cls) => menu.appendChild(el("div", { class: "item " + (cls || ""), text: label, onclick: () => { closeMenus(); action(); } }));
  item("Resume in terminal", () => resumeSession(rec, false));
  item(where.kind === "pinned" ? "Unpin" : "Pin", () => moveTo(rec.id, where.kind === "pinned" ? { kind: "ungrouped" } : { kind: "pinned" }, null));
  menu.appendChild(el("div", { class: "label", text: "Move to" }));
  for (const group of S.ui.groups) {
    if (where.kind === "group" && where.groupId === group.id) continue;
    item(group.name, () => moveTo(rec.id, { kind: "group", groupId: group.id }, null), "sub");
  }
  item("New group…", () => newGroup(rec.id), "sub");
  if (where.kind === "group") item("Remove from " + groupById(where.groupId).name, () => moveTo(rec.id, { kind: "ungrouped" }, null), "sub");
  menu.appendChild(el("div", { class: "sep" }));
  item("Rename…", () => renameSession(rec));
  item(S.ui.hidden.includes(rec.id) ? "Unhide" : "Hide", () => {
    S.ui.hidden = S.ui.hidden.includes(rec.id) ? S.ui.hidden.filter((s) => s !== rec.id) : S.ui.hidden.concat(rec.id);
    saveState();
  });
  item("Copy resume command", () => copyText(resumeCommand(rec)));
  item("Copy session id", () => copyText(rec.id));
  menu.hidden = false;
  menu.style.left = Math.min(anchor.left, window.innerWidth - menu.offsetWidth - 8) + "px";
  menu.style.top = Math.min(anchor.bottom + 4, window.innerHeight - menu.offsetHeight - 8) + "px";
}

function closeMenus() {
  $("#menu").hidden = true;
  $("#filter-menu").hidden = true;
}

function newGroup(initialSession) {
  const name = (prompt("Group name") || "").trim();
  if (!name) return;
  const group = { id: "g" + Date.now().toString(36), name, sessions: [] };
  S.ui.groups.push(group);
  if (S.prefs.group !== "custom") setPrefs({ group: "custom" });
  if (initialSession) moveTo(initialSession, { kind: "group", groupId: group.id }, null);
  else saveState();
  closeMenus();
}

function renameGroup(groupId) {
  const group = groupById(groupId);
  if (!group) return;
  const name = (prompt("Group name", group.name) || "").trim();
  if (!name || name === group.name) return;
  group.name = name;
  saveState();
}

function deleteGroup(groupId) {
  const group = groupById(groupId);
  if (!group || !confirm("Delete group \"" + group.name + "\"? Its sessions go back to the list.")) return;
  S.ui.groups = S.ui.groups.filter((g) => g.id !== groupId);
  saveState();
}

function renameSession(rec) {
  const value = prompt("Title (empty restores the original)", displayTitle(rec));
  if (value === null) return;
  const trimmed = value.trim();
  if (trimmed && trimmed !== rec.title) S.ui.titles[rec.id] = trimmed;
  else delete S.ui.titles[rec.id];
  saveState();
  if (S.selected === rec.id) renderHeader(rec);
}

// ---------- session pane ----------

async function select(id) {
  if (S.selected === id) return;  // second click of a double-click: keep what is on screen
  const box = $("#transcript");
  if (S.selected && box.firstChild) rememberScroll(S.selected, box.scrollTop);
  S.selected = id;
  for (const row of document.querySelectorAll(".row.selected")) row.classList.remove("selected");
  for (const row of document.querySelectorAll('.row[data-id="' + id + '"]')) row.classList.add("selected");
  const rec = S.byId[id];
  if (!rec) return;
  $("#empty-state").hidden = true;
  $("#session").hidden = false;
  renderHeader(rec);
  $("#transcript-stats").textContent = "loading…";
  try {
    const data = await api("/api/transcript/" + id);
    if (S.selected !== id) return;
    renderTranscript(data, box);  // the previous transcript stays visible until this one is ready
    const saved = savedScroll(id);
    box.scrollTop = saved === null ? box.scrollHeight : saved;
  } catch (e) {
    box.innerHTML = "";
    $("#transcript-stats").textContent = "";
    toast("Could not load transcript: " + e.message, true);
  }
}

async function reloadTranscript() {
  const box = $("#transcript");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const top = box.scrollTop;
  try {
    const data = await api("/api/transcript/" + S.selected);
    renderHeader(S.byId[S.selected]);
    renderTranscript(data, box);
    box.scrollTop = atBottom ? box.scrollHeight : top;
  } catch (e) { /* keep what is on screen */ }
}

function renderHeader(rec) {
  const header = $("#session-header");
  header.innerHTML = "";
  const title = el("h1", {}, [
    el("span", { text: displayTitle(rec), title: rec.titleSource === "none" ? "" : "title source: " + rec.titleSource }),
    rec.live ? el("span", { class: "live-badge", text: "running · " + rec.live.status }) : null,
  ]);
  const meta = el("div", { class: "meta-line" }, [
    el("span", { title: "project" }, [el("code", { text: rec.project || rec.projectKey })]),
    branchOf(rec) ? el("span", {}, ["branch ", el("code", { text: branchOf(rec) })]) : null,
    el("span", { text: fmtDate(rec.createdAt) + " → " + fmtDate(rec.updatedAt) + " (" + relTime(rec.updatedAt) + ")" }),
    el("span", { text: rec.userTurns + " prompts · " + rec.assistantTurns + " replies" }),
    rec.costUSD !== null && rec.costUSD !== undefined ? el("span", { text: fmtCost(rec.costUSD) + (rec.durationMs ? " · " + fmtDuration(rec.durationMs) : "") }) : null,
    rec.models && rec.models.length ? el("span", { text: rec.models.join(", ") }) : null,
    rec.headless ? el("span", { text: "headless (" + rec.entrypoint + ")" }) : null,
    rec.version ? el("span", { text: "v" + rec.version }) : null,
    el("span", {}, [el("code", { text: rec.id })]),
  ]);
  const actions = el("div", { class: "header-actions" }, [
    el("button", { class: "primary", text: rec.live ? "Already running" : "Resume", disabled: !!rec.live, onclick: () => resumeSession(rec, false) }),
    el("button", { text: "Fork", title: "Resume as a new session id (--fork-session)", onclick: () => resumeSession(rec, true) }),
    el("button", { text: "Copy command", onclick: () => copyText(resumeCommand(rec)) }),
    el("code", { text: resumeCommand(rec) }),
    el("span", { class: "status", id: "resume-status" }),
  ]);
  header.append(title, meta, actions);
}

/* One launch per session at a time: a double-click, a second click on Resume or
   another tab cannot open two terminals. The server refuses repeats for a few seconds too. */
async function resumeSession(rec, fork) {
  const status = S.selected === rec.id ? $("#resume-status") : null;
  if (rec.live && !fork) { toast("Already running (pid " + rec.live.pid + ")"); return; }
  if (S.resuming.has(rec.id)) { toast("Already opening " + displayTitle(rec)); return; }
  S.resuming.add(rec.id);
  if (status) status.textContent = "opening…";
  try {
    const result = await mutate("POST", "/api/resume/" + rec.id, { fork });
    const text = result.ok ? "opened in " + result.detail + " (" + result.cwd + ")" : result.busy ? "already opening" : "could not open a terminal: " + result.detail;
    if (status) status.textContent = text;
    if (result.ok) toast(displayTitle(rec) + ": " + text);
    else if (!result.busy) toast("Run it yourself: cd " + result.cwd + " && " + result.command, true);
  } catch (e) {
    if (status) status.textContent = "";
    toast("Resume failed: " + e.message, true);
  } finally {
    setTimeout(() => S.resuming.delete(rec.id), 5000);  // the terminal takes a moment to show up as running
  }
}

// ---------- live updates ----------

async function applyChanges(ids) {
  try {
    applyIndex(await api("/api/sessions"));
    if (S.selected && ids.has(S.selected)) reloadTranscript();
  } catch (e) { /* the 30s poll retries */ }
}

// ---------- search ----------

let filterTimer = null;
function onFilterInput(e) {
  S.filter = e.target.value.trim().toLowerCase();
  if (!S.filter) S.hits = null;
  clearTimeout(filterTimer);
  filterTimer = setTimeout(renderSidebar, 80);
}

async function searchMessages() {
  const q = $("#search").value.trim();
  if (q.length < 2) { S.hits = null; renderSidebar(); return; }
  try {
    const result = await api("/api/search?q=" + encodeURIComponent(q));
    S.hits = Object.fromEntries(result.hits.map((h) => [h.id, h.snippets]));
    S.hitsQuery = q;
    S.filter = "";
    renderSidebar();
    toast(result.hits.length + " session(s) mention \"" + q + "\"");
  } catch (e) {
    toast("Search failed: " + e.message, true);
  }
}

// ---------- wiring ----------

$("#search").addEventListener("input", onFilterInput);
$("#search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); searchMessages(); }
  if (e.key === "Escape") { e.target.value = ""; S.filter = ""; S.hits = null; renderSidebar(); }
});
$("#refresh").addEventListener("click", () => refreshSessions(true));
$("#filter").addEventListener("click", (e) => { e.stopPropagation(); if ($("#filter-menu").hidden) openFilterMenu(); else closeMenus(); });
for (const id of ["#show-tools", "#show-thinking", "#show-context"]) $(id).addEventListener("change", applyToggles);
document.addEventListener("click", (e) => { if (!$("#menu").contains(e.target) && !$("#filter-menu").contains(e.target)) closeMenus(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });
window.addEventListener("blur", closeMenus);
window.addEventListener("beforeunload", () => { if (S.selected) rememberScroll(S.selected, $("#transcript").scrollTop); });
loadAll().then(() => connectEvents(applyChanges)).catch((e) => toast("Could not load sessions: " + e.message, true));
setInterval(() => refreshSessions(false), POLL_MS);  // fallback when the event stream is down
