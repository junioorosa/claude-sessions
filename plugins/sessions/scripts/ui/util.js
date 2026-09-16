export const $ = (sel, root) => (root || document).querySelector(sel);

const CLIENT_HEADERS = { "X-Sessions-Client": "1", "Content-Type": "application/json" };

export function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
  }
  for (const child of [].concat(children || [])) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function esc(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function basename(path) {
  if (!path) return "";
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export function relTime(iso) {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  if (diff < 86400 * 14) return Math.floor(diff / 86400) + "d ago";
  return new Date(iso).toLocaleDateString();
}

export function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleString() : "";
}

export function fmtCost(value) {
  return value === null || value === undefined ? "" : "$" + value.toFixed(2);
}

export function fmtDuration(ms) {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m " + (s % 60) + "s";
  return Math.floor(m / 60) + "h " + (m % 60) + "m";
}

export function fmtTokens(n) {
  if (n === null || n === undefined) return "";
  return n >= 1000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "k" : String(n);
}

let toastTimer = null;
export function toast(message, isError) {
  let node = $("#toast");
  if (!node) {
    node = el("div", { id: "toast", class: "toast" });
    document.body.appendChild(node);
  }
  node.textContent = message;
  node.className = "toast" + (isError ? " error" : "");
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, isError ? 8000 : 3500);
}

export async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

export const mutate = (method, path, body) => api(path, { method, headers: CLIENT_HEADERS, body: JSON.stringify(body || {}) });

export function copyText(text) {
  navigator.clipboard.writeText(text).then(() => toast("Copied: " + text), () => toast("Clipboard unavailable: " + text, true));
}
