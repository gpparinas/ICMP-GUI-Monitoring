// Small DOM, formatting and API helpers shared by every view.

const SVG_NS = "http://www.w3.org/2000/svg";

/** Create an element. Text children always go in as text nodes, never as HTML. */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

export function svg(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value !== null && value !== undefined && value !== false) el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

function setAttrs(el, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (key === "value") el.value = value;
    else if (key === "checked" || key === "disabled" || key === "hidden") el[key] = Boolean(value);
    else el.setAttribute(key, value === true ? "" : value);
  }
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// --- icons -------------------------------------------------------------------

const ICONS = {
  up: '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M4.8 8.2l2.2 2.2 4.2-4.6" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
  degraded: '<path d="M8 1.6l6.9 12.1H1.1z" fill="currentColor" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M8 6v3.6" stroke="#0b0b0b" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="11.7" r=".95" fill="#0b0b0b"/>',
  down: '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M5.5 5.5l5 5m0-5l-5 5" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/>',
  paused: '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M6.2 5.4v5.2m3.6-5.2v5.2" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/>',
  pending: '<circle cx="8" cy="8" r="7" fill="currentColor"/><circle cx="5" cy="8" r="1" fill="#fff"/><circle cx="8" cy="8" r="1" fill="#fff"/><circle cx="11" cy="8" r="1" fill="#fff"/>',
  plus: '<path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
  search: '<circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10.5 10.5L14 14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  back: '<path d="M9.5 3.5L5 8l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
  sun: '<circle cx="8" cy="8" r="3" fill="currentColor"/><path d="M8 1.5v1.6m0 9.8v1.6M1.5 8h1.6m9.8 0h1.6M3.4 3.4l1.1 1.1m7 7l1.1 1.1m0-9.2l-1.1 1.1m-7 7l-1.1 1.1" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
  moon: '<path d="M13.5 9.6A5.8 5.8 0 016.4 2.5a5.8 5.8 0 107.1 7.1z" fill="currentColor"/>',
  refresh: '<path d="M13 8a5 5 0 11-1.6-3.7M13 2.5v2.6h-2.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  logo: '<path d="M1.5 8.5h2.8l1.7-4.8 3 9 1.8-4.2h3.7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
};

export function icon(name, cls = "icon") {
  const el = svg("svg", { viewBox: "0 0 16 16", class: cls, "aria-hidden": "true" });
  el.innerHTML = ICONS[name] || ""; // static, trusted markup from the table above
  return el;
}

export const STATUS_LABELS = {
  up: "Up", degraded: "Degraded", down: "Down", paused: "Paused", pending: "Pending",
};

export function statusBadge(status) {
  return h("span", { class: `status ${status}` }, icon(status), h("span", null, STATUS_LABELS[status] || status));
}

// --- formatting ----------------------------------------------------------------

export function fmtLatency(ms) {
  if (ms === null || ms === undefined) return "—";
  if (ms >= 10000) return `${(ms / 1000).toFixed(0)} s`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  if (ms >= 10) return `${Math.round(ms)} ms`;
  return `${ms.toFixed(1)} ms`;
}

export function fmtUptime(ratio) {
  if (ratio === null || ratio === undefined) return "—";
  if (ratio >= 0.99995) return "100%";
  const pct = ratio * 100;
  return `${pct.toFixed(pct >= 99 ? 2 : 1)}%`;
}

export function fmtDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  if (seconds < 60) return `${seconds}s`;
  const units = [["d", 86400], ["h", 3600], ["m", 60]];
  const parts = [];
  for (const [label, size] of units) {
    const value = Math.floor(seconds / size);
    if (value) parts.push(`${value}${label}`);
    seconds -= value * size;
  }
  return parts.slice(0, 2).join(" ");
}

export function fmtAgo(ts, now = Date.now() / 1000) {
  if (!ts) return "never";
  const diff = now - ts;
  return diff < 5 ? "just now" : `${fmtDuration(diff)} ago`;
}

const dayTime = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const clock = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const clockShort = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const day = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

export const fmtDateTime = (ts) => dayTime.format(new Date(ts * 1000));
export const fmtClock = (ts) => clock.format(new Date(ts * 1000));
export const fmtClockShort = (ts) => clockShort.format(new Date(ts * 1000));
export const fmtDay = (ts) => day.format(new Date(ts * 1000));

export function fmtInterval(seconds) {
  if (seconds % 3600 === 0) return `${seconds / 3600} h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}

// --- API ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Cannot reach the monitor. Is it still running?", 0);
  }
  if (response.ok) {
    return response.headers.get("content-type")?.includes("json") ? response.json() : response.text();
  }
  let message = `Request failed (${response.status})`;
  try {
    const { detail } = await response.json();
    if (typeof detail === "string") message = detail;
    else if (Array.isArray(detail) && detail.length) {
      const first = detail[0];
      const field = (first.loc || []).filter((part) => part !== "body").join(".");
      const text = String(first.msg || "").replace(/^Value error, /, "");
      message = field ? `${field.replaceAll("_", " ")}: ${text}` : text;
      message = message.charAt(0).toUpperCase() + message.slice(1);
    }
  } catch { /* keep the generic message */ }
  throw new ApiError(message, response.status);
}

// --- toasts, tooltips, dialogs -----------------------------------------------------

let toastHost;

export function toast(message, kind = "ok") {
  if (!toastHost) {
    toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toastHost);
  }
  const el = h("div", { class: `toast ${kind}` }, icon(kind === "error" ? "down" : "up"), message);
  toastHost.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3000);
}

let tipEl;

export function showTip(x, y, ...content) {
  if (!tipEl) {
    tipEl = h("div", { class: "tip", role: "tooltip" });
    document.body.append(tipEl);
  }
  clear(tipEl, ...content);
  tipEl.hidden = false;
  const { width, height } = tipEl.getBoundingClientRect();
  const left = Math.min(Math.max(8, x - width / 2), window.innerWidth - width - 8);
  const top = y - height - 12 < 8 ? y + 18 : y - height - 12;
  tipEl.style.left = `${left}px`;
  tipEl.style.top = `${top}px`;
}

export function hideTip() {
  if (tipEl) tipEl.hidden = true;
}

/** Elements carrying data-tip="Title|line|line" get a tooltip on hover and focus. */
export function installTips() {
  const show = (event) => {
    const target = event.target.closest?.("[data-tip]");
    if (!target) return hideTip();
    const [title, ...lines] = target.dataset.tip.split("|");
    const box = target.getBoundingClientRect();
    showTip(box.left + box.width / 2, box.top, h("strong", null, title), lines.map((l) => h("span", null, l)));
  };
  document.addEventListener("pointerover", show);
  document.addEventListener("focusin", show);
  document.addEventListener("pointerdown", hideTip);
  document.addEventListener("scroll", hideTip, true);
}

/** Open a modal dialog. `build(close)` returns { title, body, submitLabel, onSubmit, danger }. */
export function openDialog({ title, body, submitLabel = "Save", onSubmit, danger = false, narrow = false }) {
  return new Promise((resolve) => {
    const error = h("p", { class: "form-error", hidden: true, role: "alert" });
    const submit = h("button", { class: `btn ${danger ? "danger solid" : "primary"}`, type: "submit" }, submitLabel);
    const form = h("form", { method: "dialog", novalidate: false },
      h("header", null, h("h2", null, title)),
      h("div", { class: "body" }, body, error),
      h("footer", null,
        h("button", { class: "btn", type: "button", onclick: () => dialog.close() }, "Cancel"),
        submit));
    const dialog = h("dialog", { class: narrow ? "narrow" : "" }, form);
    let result = null;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.hidden = true;
      submit.disabled = true;
      try {
        result = (await onSubmit()) ?? true;
        dialog.close();
      } catch (err) {
        error.textContent = err.message || String(err);
        error.hidden = false;
      } finally {
        submit.disabled = false;
      }
    });
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(result);
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}

export function confirmDialog(title, message, actionLabel) {
  return openDialog({
    title,
    body: h("p", { class: "secondary" }, message),
    submitLabel: actionLabel,
    danger: true,
    narrow: true,
    onSubmit: async () => true,
  });
}

/** A labelled form field. */
export function field(label, control, help) {
  const id = control.id || `f-${Math.random().toString(36).slice(2, 9)}`;
  control.id = id;
  return h("div", { class: "field" }, h("label", { for: id }, label), control, help && h("p", { class: "help" }, help));
}

export function checkbox(label, checked, help) {
  const input = h("input", { type: "checkbox", checked });
  const el = h("label", { class: "check" }, input, h("span", null, label, help && h("small", null, help)));
  return { el, input };
}
