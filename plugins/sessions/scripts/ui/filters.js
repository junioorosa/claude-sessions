import { el, basename } from "./util.js";

export const DEFAULT_PREFS = { status: "active", source: "interactive", group: "date", sort: "activity", showEmpty: false, folder: "" };

export const FILTERS = [
  { key: "status", label: "Status", options: [
    ["active", "Active", "Everything you have not hidden"],
    ["running", "Running", "Only sessions with a live Claude Code process"],
    ["hidden", "Hidden", "Only sessions you hid (right-click > Hide)"],
    ["all", "All", "Hidden ones included"],
  ] },
  { key: "source", label: "Source", options: [
    ["interactive", "Interactive", "Sessions you ran in a terminal or the desktop app"],
    ["headless", "Headless (claude -p, SDK)", "Non-interactive runs: scripts, automations, SDK"],
    ["all", "All", ""],
  ] },
  { key: "group", label: "Group by", options: [
    ["date", "Date", "By day of last activity"],
    ["folder", "Folder", "By project directory"],
    ["custom", "Custom groups", "Your own groups; drag rows into them"],
    ["status", "Status", "Running vs finished"],
    ["none", "None", "One flat list"],
  ] },
  { key: "sort", label: "Sort by", options: [
    ["activity", "Last activity", ""], ["created", "Created", ""], ["title", "Title", ""], ["cost", "Cost", "Sessions with a recorded cost first"],
  ] },
];

export function loadPrefs() {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem("sessions.prefs") || "{}") }; } catch (e) { return { ...DEFAULT_PREFS }; }
}

export function savePrefs(prefs) {
  try { localStorage.setItem("sessions.prefs", JSON.stringify(prefs)); } catch (e) { /* per-viewer convenience only */ }
}

export const isDefaultPrefs = (prefs) => Object.keys(DEFAULT_PREFS).every((k) => prefs[k] === DEFAULT_PREFS[k]);

/* Pinned rows only obey the text filter; everything else obeys the whole menu. */
export function passes(rec, prefs, hidden, pinned) {
  if (pinned) return true;
  const isHidden = hidden.has(rec.id);
  if (prefs.status === "active" && isHidden) return false;
  if (prefs.status === "running" && !rec.live) return false;
  if (prefs.status === "hidden" && !isHidden) return false;
  if (prefs.source === "interactive" && rec.headless) return false;
  if (prefs.source === "headless" && !rec.headless) return false;
  if (!prefs.showEmpty && rec.empty) return false;
  if (prefs.folder && rec.project !== prefs.folder) return false;
  return true;
}

export function sortRecs(recs, prefs, titleOf) {
  const key = {
    activity: (r) => r.updatedAt || "",
    created: (r) => r.createdAt || "",
    title: (r) => titleOf(r).toLowerCase(),
    cost: (r) => r.costUSD || 0,
    manual: () => 0,
  }[prefs.sort] || ((r) => r.updatedAt || "");
  const asc = prefs.sort === "title";
  return [...recs].sort((a, b) => {
    if (!!a.live !== !!b.live) return a.live ? -1 : 1;
    const ka = key(a), kb = key(b);
    if (ka === kb) return 0;
    return (ka < kb ? -1 : 1) * (asc ? 1 : -1);
  });
}

export function dateLabel(iso) {
  if (!iso) return "Older";
  const day = new Date(iso); day.setHours(0, 0, 0, 0);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((today - day) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return day.toLocaleDateString(undefined, { weekday: "long" });
  if (days < 30) return day.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return "Older";
}

/* [{ key, label, title, recs }] in display order; input already sorted. */
export function bucketsOf(recs, prefs) {
  if (prefs.group === "none") return [{ key: "all", label: "Sessions", title: "", recs }];
  const map = new Map();
  for (const rec of recs) {
    let key, label, title = "";
    if (prefs.group === "folder") { key = rec.project || rec.projectKey; label = basename(key); title = key; }
    else if (prefs.group === "status") { key = rec.live ? "running" : "finished"; label = rec.live ? "Running" : "Finished"; }
    else { key = dateLabel(rec.updatedAt); label = key; }
    if (!map.has(key)) map.set(key, { key, label, title, recs: [] });
    map.get(key).recs.push(rec);
  }
  if (prefs.group === "folder") disambiguate([...map.values()]);
  // Live rows float inside a bucket, but buckets themselves follow the calendar
  // (folders: live folders first, then by their newest session).
  const newest = (b) => b.recs.reduce((m, r) => ((r.updatedAt || "") > m ? r.updatedAt : m), "");
  const hasLive = (b) => b.recs.some((r) => r.live);
  return [...map.values()].sort((a, b) => {
    if (prefs.group === "folder" && hasLive(a) !== hasLive(b)) return hasLive(a) ? -1 : 1;
    if (prefs.group === "status") return a.key === "running" ? -1 : 1;
    return newest(b).localeCompare(newest(a));
  });
}

/* Two folders named the same (conob_8 under two workspaces) get their parent folder in the label. */
function disambiguate(buckets) {
  const byLabel = new Map();
  for (const b of buckets) byLabel.set(b.label, (byLabel.get(b.label) || 0) + 1);
  for (const b of buckets) {
    if (byLabel.get(b.label) < 2) continue;
    const parts = b.key.replace(/[\\/]+$/, "").split(/[\\/]/);
    b.label = parts.slice(-2).join("/");
  }
}

export function renderFilterMenu(menu, prefs, projects, onChange, onNewGroup) {
  menu.innerHTML = "";
  const grid = el("div", { class: "filter-grid" });
  for (const section of FILTERS) {
    const column = el("div", { class: "filter-section" }, [el("div", { class: "label", text: section.label })]);
    for (const [value, text, hint] of section.options) {
      column.appendChild(el("div", {
        class: "item" + (prefs[section.key] === value ? " checked" : ""), text, title: hint || null,
        onclick: () => onChange({ [section.key]: value }),
      }));
    }
    grid.appendChild(column);
  }
  menu.appendChild(grid);
  menu.appendChild(el("div", { class: "sep" }));
  menu.appendChild(el("div", { class: "item" + (prefs.showEmpty ? " checked" : ""), text: "Show empty sessions", onclick: () => onChange({ showEmpty: !prefs.showEmpty }) }));
  const select = el("select", { class: "folder-select", onchange: (e) => onChange({ folder: e.target.value }) },
    [el("option", { value: "", text: "All folders" })].concat(projects.map(([path, name]) => el("option", { value: path, text: name, title: path }))));
  select.value = projects.some(([p]) => p === prefs.folder) ? prefs.folder : "";
  menu.appendChild(el("div", { class: "item plain" }, [el("span", { text: "Folder" }), select]));
  menu.appendChild(el("div", { class: "sep" }));
  menu.appendChild(el("div", { class: "item", text: "New custom group…", onclick: onNewGroup }));
  menu.appendChild(el("div", { class: "item dim", text: "Reset filters", onclick: () => onChange({ ...DEFAULT_PREFS }) }));
}
