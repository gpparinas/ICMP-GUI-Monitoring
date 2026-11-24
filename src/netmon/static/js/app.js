// Network Health Monitor dashboard: routing, polling and the four views.

import { renderChart, renderHistoryTable } from "./chart.js";
import { CHANNEL_TYPES, channelDialog, monitorDialog } from "./forms.js";
import {
  STATUS_LABELS, api, checkbox, clear, confirmDialog, field, fmtAgo, fmtClock, fmtDateTime, fmtDuration,
  fmtInterval, fmtLatency, fmtUptime, h, hideTip, icon, installTips, openDialog, statusBadge, statusIcon, toast,
} from "./util.js";

const POLL_MS = 5000;
const TYPE_LABELS = { icmp: "Ping", tcp: "TCP", http: "HTTP", dns: "DNS" };

const state = {
  session: null,      // { setup_required, authenticated, public_view, site_title, version }
  overview: null,     // last /api/overview response
  filter: "all",
  search: "",
  range: "24h",
  lastOk: 0,
};

const main = document.querySelector("main");
const topbar = document.querySelector(".topbar");
let view = null;       // { refresh(): Promise, destroy?() } for the current route
let timer = null;

// --- theme ------------------------------------------------------------------------

function currentTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}

function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("netmon-theme", next);
  renderTopbar();
}

// --- shell ------------------------------------------------------------------------

function renderTopbar() {
  const s = state.session || {};
  const route = location.hash.replace(/^#\/?/, "").split("/")[0] || "dashboard";
  const showNav = s.authenticated || (s.public_view && !s.setup_required);
  const link = (href, label, key) => h("a", { href, class: route === key || (key === "dashboard" && route === "monitor") ? "active" : "" }, label);
  const theme = currentTheme();
  clear(topbar,
    h("a", { class: "brand", href: "#/" }, icon("logo", ""), h("span", { class: "truncate" }, s.site_title || "Network Health Monitor")),
    showNav && h("nav", { class: "nav", "aria-label": "Sections" },
      link("#/", "Dashboard", "dashboard"),
      link("#/incidents", "Incidents", "incidents"),
      s.authenticated && link("#/settings", "Settings", "settings")),
    h("span", { class: "spacer" }),
    showNav && h("span", { class: "live", id: "live" }, h("i"), h("span", null, "Connecting…")),
    h("button", { class: "btn ghost icon-only", onclick: toggleTheme, title: `Switch to ${theme === "dark" ? "light" : "dark"} theme`, "aria-label": "Toggle colour theme" },
      icon(theme === "dark" ? "sun" : "moon")),
    s.authenticated
      ? h("button", { class: "btn ghost", onclick: signOut }, "Sign out")
      : showNav && h("a", { class: "btn", href: "#/login" }, "Sign in"));
  document.title = s.site_title || "Network Health Monitor";
  updateLive();
}

function updateLive() {
  const el = document.getElementById("live");
  if (!el) return;
  const stale = state.lastOk && Date.now() - state.lastOk > POLL_MS * 3;
  el.classList.toggle("stale", Boolean(stale));
  el.lastChild.textContent = !state.lastOk ? "Connecting…" : stale ? "Connection lost" : `Updated ${fmtClock(state.lastOk / 1000)}`;
}

async function signOut() {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  await loadSession();
  location.hash = "#/";
  route();
}

async function loadSession() {
  state.session = await api("/api/session");
}

// --- router -----------------------------------------------------------------------

async function route() {
  clearTimeout(timer);
  hideTip();
  view?.destroy?.();
  view = null;

  const s = state.session;
  const [name, arg] = location.hash.replace(/^#\/?/, "").split("/");
  const canView = s.authenticated || s.public_view;

  if (s.setup_required) view = setupView();
  else if (name === "login" && !s.authenticated) view = loginView();
  else if (!canView) view = loginView();
  else if (name === "wall") view = wallView();
  else if (name === "monitor" && arg) view = monitorView(Number(arg));
  else if (name === "incidents") view = incidentsView();
  else if (name === "settings" && s.authenticated) view = settingsView();
  else view = dashboardView();

  renderTopbar();
  window.scrollTo(0, 0);
  await tick();
}

async function tick() {
  clearTimeout(timer);
  const active = view;
  if (active?.refresh && document.visibilityState !== "hidden") {
    try {
      await active.refresh();
      state.lastOk = Date.now();
    } catch (err) {
      if (err.status === 401) {
        await loadSession().catch(() => {});
        if (view === active) return route();
      }
    }
    updateLive();
  }
  if (view === active && active?.refresh) timer = setTimeout(tick, active.interval || POLL_MS);
}

/** Run an action, report failures as a toast, then refresh the current view. */
async function act(fn, success) {
  try {
    const result = await fn();
    if (success) toast(success);
    await tick();
    return result;
  } catch (err) {
    toast(err.message, "error");
    return null;
  }
}

// --- auth views -------------------------------------------------------------------

function authCard(title, text, fields, label, onSubmit) {
  const error = h("p", { class: "form-error", hidden: true, role: "alert" });
  const button = h("button", { class: "btn primary", type: "submit" }, label);
  const form = h("form", { class: "card", onsubmit: async (event) => {
    event.preventDefault();
    error.hidden = true;
    button.disabled = true;
    try {
      await onSubmit();
      await loadSession();
      if (location.hash.startsWith("#/login")) location.hash = "#/";
      await route();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      button.disabled = false;
    }
  } }, h("h1", null, title), h("p", null, text), fields, error, button);
  clear(main, h("div", { class: "auth" }, form));
  form.querySelector("input")?.focus();
}

function setupView() {
  const password = h("input", { class: "input", type: "password", required: true, minlength: 8, autocomplete: "new-password" });
  const repeat = h("input", { class: "input", type: "password", required: true, autocomplete: "new-password" });
  authCard("Welcome", "Choose an admin password to finish setting up the monitor. You will need it to add monitors and change settings.",
    [field("Admin password", password, "At least 8 characters."), field("Repeat password", repeat)],
    "Create password and continue",
    async () => {
      if (password.value !== repeat.value) throw new Error("The passwords do not match.");
      await api("/api/setup", { method: "POST", body: { password: password.value } });
    });
  return {};
}

function loginView() {
  const password = h("input", { class: "input", type: "password", required: true, autocomplete: "current-password" });
  authCard("Sign in", "Enter the admin password to continue.", [field("Password", password)], "Sign in",
    () => api("/api/login", { method: "POST", body: { password: password.value } }));
  return {};
}

// --- dashboard --------------------------------------------------------------------

/** "13 minutes", "3 hours 5 minutes", "2 days": durations written out for sentences. */
function spelledDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const units = [["day", 86400], ["hour", 3600], ["minute", 60], ["second", 1]];
  const parts = [];
  for (const [name, size] of units) {
    const value = Math.floor(seconds / size);
    if (value) parts.push(`${value} ${name}${value === 1 ? "" : "s"}`);
    seconds -= value * size;
    if (parts.length === 2 || (parts.length && name === "minute")) break;
  }
  return parts.join(" ") || "a moment";
}

function nameList(items) {
  const names = items.map((m) => m.name);
  if (names.length <= 2) return names.join(" and ");
  return `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
}

/** The sentence at the top: what is wrong, written as plainly as possible. */
function headline(data) {
  const { counts, monitors } = data;
  const now = data.now;
  const down = monitors.filter((m) => m.status === "down");
  const degraded = monitors.filter((m) => m.status === "degraded");
  const active = monitors.length - counts.paused;
  let title, sub;
  if (!monitors.length) {
    title = "Nothing is being monitored yet";
    sub = "Add a router, server, printer or website and it will be checked around the clock.";
  } else if (down.length === 1) {
    const m = down[0];
    title = `${m.name} is down`;
    sub = [h("b", null, m.detail || "Check failed"), `. Stopped answering ${spelledDuration(now - m.since)} ago.`];
  } else if (down.length) {
    title = `${down.length} monitors are down`;
    sub = [h("b", null, nameList(down)), " stopped answering."];
  } else if (degraded.length === 1) {
    const m = degraded[0];
    title = `${m.name} needs attention`;
    sub = [h("b", null, m.detail || "Degraded"), `. For the last ${spelledDuration(now - m.since)}.`];
  } else if (degraded.length) {
    title = `${degraded.length} monitors need attention`;
    sub = [h("b", null, nameList(degraded)), " are slow or have warnings."];
  } else if (counts.pending) {
    title = "Waiting for the first results";
    sub = `${counts.pending} of ${active} monitors have not been checked yet.`;
  } else if (!active) {
    title = "All monitors are paused";
    sub = "Nothing is being checked. Resume a monitor to start again.";
  } else {
    title = "Everything is up";
    sub = `${active} monitor${active === 1 ? "" : "s"} answering. ${fmtUptime(data.uptime_24h)} uptime over the last 24 hours.`;
  }
  const problem = down.length || degraded.length;
  if (problem && monitors.length > problem) {
    const ok = counts.up;
    sub = [sub, ` ${ok} other${ok === 1 ? " is" : "s are"} up.`];
  }
  return h("div", { class: "headline", role: "status", "aria-live": "polite" }, h("h1", null, title), h("p", null, sub));
}

function groupMonitors(monitors) {
  const groups = new Map();
  for (const m of monitors) {
    const key = m.group || "Ungrouped";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(m);
  }
  return groups;
}

function portTip(m, now) {
  const state = m.status === "pending" ? "Waiting for the first check"
    : m.status === "paused" ? "Paused"
    : `${STATUS_LABELS[m.status]} for ${fmtDuration(now - m.since)}`;
  const detail = m.status === "down" || m.status === "degraded" ? m.detail
    : m.status === "up" ? fmtLatency(m.latency_ms) : "";
  return [m.name, state, detail, m.target].filter(Boolean).join("|");
}

/** Every monitor as a port with a link light, one bank per group. */
function patchPanel(data) {
  const banks = [...groupMonitors(data.monitors)].map(([name, items]) => {
    const down = items.filter((m) => m.status === "down").length;
    return h("div", { class: "bank" },
      h("div", { class: "bank-name" }, name,
        h("span", null, down ? `${down} of ${items.length} down` : `${items.length} monitor${items.length === 1 ? "" : "s"}`)),
      h("div", { class: "ports" }, items.map((m) =>
        h("a", { class: `port ${m.status}`, href: `#/monitor/${m.id}`, "data-tip": portTip(m, data.now),
          "aria-label": `${m.name}: ${STATUS_LABELS[m.status]}` },
          h("i", { class: "led" }), icon(m.status, `socket ic-${m.status}`), h("span", { class: "label" }, m.name)))));
  });
  return h("section", { class: "panel patch-panel", "aria-label": "Status of every monitor" }, banks);
}

function beats(monitor) {
  const slots = 40;
  const recent = monitor.recent.slice(-slots);
  const cells = [];
  for (let i = recent.length; i < slots; i++) cells.push(h("i"));
  for (const [ts, status, latency] of recent) {
    const text = status === "down" ? "Check failed" : `${STATUS_LABELS[status]}, ${fmtLatency(latency)}`;
    cells.push(h("i", { class: status, "data-tip": `${text}|${fmtDateTime(ts)}` }));
  }
  const failed = recent.filter((r) => r[1] === "down").length;
  return h("div", { class: "beats", role: "img", "aria-label": `Last ${recent.length} checks: ${failed} failed` }, cells);
}

function monitorRow(m) {
  const latency = m.status === "down" || m.status === "paused" ? "—" : fmtLatency(m.latency_ms);
  const problem = (m.status === "down" || m.status === "degraded") && m.detail;
  return h("a", { class: `mrow is-${m.status}`, href: `#/monitor/${m.id}` },
    statusIcon(m.status),
    h("div", { class: "grow" },
      h("div", { class: "name truncate" }, m.name),
      h("div", { class: "target" },
        h("span", { class: "kind" }, TYPE_LABELS[m.type]),
        h("span", { class: "truncate" }, m.target),
        problem && h("span", { class: "problem truncate" }, m.detail))),
    h("div", { class: "col-beats" }, beats(m)),
    h("div", { class: "right num" }, latency),
    h("div", { class: "right num col-24h" }, fmtUptime(m.uptime["24h"])),
    h("div", { class: "right num col-30d" }, fmtUptime(m.uptime["30d"])));
}

async function addMonitor() {
  const groups = [...new Set((state.overview?.monitors || []).map((m) => m.group).filter(Boolean))];
  const created = await monitorDialog(null, groups);
  if (created) { toast(`${created.name} added`); await tick(); }
}

function dashboardView() {
  const admin = state.session.authenticated;
  const top = h("div");
  const list = h("div");
  const search = h("input", { class: "input", type: "search", placeholder: "Search monitors", value: state.search, "aria-label": "Search monitors",
    oninput: () => { state.search = search.value; renderList(); } });
  const chips = h("div", { class: "segmented", role: "group", "aria-label": "Filter by status" });
  const toolbar = h("div", { class: "toolbar" },
    h("div", { class: "search" }, icon("search"), search), chips, h("span", { class: "grow" }),
    h("a", { class: "btn ghost", href: "#/wall", title: "A full-screen view for a TV or spare monitor" }, icon("wall"), "Wall screen"),
    admin && h("button", { class: "btn primary", onclick: addMonitor }, icon("plus"), "Add monitor"));
  clear(main, top, toolbar, list);

  function renderList() {
    const data = state.overview;
    if (!data) return;
    const { counts, monitors } = data;
    hideTip();

    clear(top, headline(data), monitors.length ? patchPanel(data) : null);

    const filters = [["all", "All", monitors.length], ["down", "Down", counts.down], ["degraded", "Degraded", counts.degraded], ["up", "Up", counts.up], ["paused", "Paused", counts.paused]];
    clear(chips, filters.map(([key, label, count]) =>
      h("button", { type: "button", "aria-pressed": String(state.filter === key), onclick: () => { state.filter = key; renderList(); } },
        label, h("span", { class: "count" }, count))));
    toolbar.hidden = !monitors.length;

    if (!monitors.length) {
      clear(list, admin
        ? h("button", { class: "btn primary", onclick: addMonitor }, icon("plus"), "Add your first monitor")
        : h("p", { class: "secondary" }, "Sign in to add monitors."));
      return;
    }

    const needle = state.search.trim().toLowerCase();
    const shown = monitors.filter((m) =>
      (state.filter === "all" || m.status === state.filter)
      && (!needle || `${m.name} ${m.target} ${m.group}`.toLowerCase().includes(needle)));
    if (!shown.length) {
      clear(list, h("div", { class: "empty" }, h("h2", null, "No monitors match"), h("p", null, "Try a different search, or choose All.")));
      return;
    }

    clear(list, [...groupMonitors(shown)].map(([name, items]) => {
      const down = items.filter((m) => m.status === "down").length;
      return h("section", { class: "group" },
        h("div", { class: "group-head" }, h("h2", null, name),
          h("span", null, `${items.length} monitor${items.length === 1 ? "" : "s"}${down ? `, ${down} down` : ""}`)),
        h("div", { class: "mrow head" }, h("div", null, ""), h("div", null, "Monitor"),
          h("div", { class: "right col-beats" }, "Last 40 checks"), h("div", { class: "right" }, "Response"),
          h("div", { class: "right col-24h" }, "24 hours"), h("div", { class: "right col-30d" }, "30 days")),
        items.map(monitorRow));
    }));
  }

  return {
    async refresh() {
      state.overview = await api("/api/overview");
      if (state.overview.site_title !== state.session.site_title) {
        state.session.site_title = state.overview.site_title;
        renderTopbar();
      }
      renderList();
    },
  };
}

/** Full-screen panel for a TV on the office wall: headline and ports, nothing else. */
function wallView() {
  document.body.classList.add("wall");
  const exit = (event) => { if (event.key === "Escape") location.hash = "#/"; };
  document.addEventListener("keydown", exit);
  return {
    async refresh() {
      const data = await api("/api/overview");
      state.overview = data;
      clear(main, headline(data), data.monitors.length ? patchPanel(data) : null,
        h("div", { class: "wall-foot" },
          h("span", null, `${data.site_title}. Updated ${fmtClock(data.now)}.`),
          h("a", { href: "#/" }, "Leave wall screen")));
    },
    destroy() {
      document.body.classList.remove("wall");
      document.removeEventListener("keydown", exit);
    },
  };
}

// --- monitor page -------------------------------------------------------------------

function monitorView(id) {
  const admin = state.session.authenticated;
  const head = h("div");
  const stats = h("div");
  const chartHost = h("div", { class: "chart card-body" });
  const lower = h("div", { class: "columns" });
  const ranges = h("div", { class: "segmented", role: "group", "aria-label": "Time range" });
  const modes = h("div", { class: "segmented", role: "group", "aria-label": "Chart or table" });
  let monitor = null, history = null, historyAt = 0, mode = "chart", missing = false;

  clear(main,
    h("a", { class: "back", href: "#/" }, icon("back"), "All monitors"),
    head,
    h("div", { class: "filters" }, ranges),
    stats,
    h("section", { class: "panel" }, h("div", { class: "card-head" }, h("h2", { class: "grow" }, "Response time"), modes), chartHost),
    lower);

  const resize = new ResizeObserver(() => { if (history && mode === "chart") renderChart(chartHost, history); });
  resize.observe(chartHost);

  function renderControls() {
    clear(ranges, [["1h", "Last hour"], ["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"]].map(([key, label]) =>
      h("button", { type: "button", "aria-pressed": String(state.range === key), onclick: () => { state.range = key; renderControls(); loadHistory(true); } }, label)));
    clear(modes, [["chart", "Chart"], ["table", "Table"]].map(([key, label]) =>
      h("button", { type: "button", "aria-pressed": String(mode === key), onclick: () => { mode = key; renderControls(); renderHistory(); } }, label)));
  }

  function renderHistory() {
    if (!history) return;
    const s = history.summary;
    const figure = (label, value, note) => h("div", { class: "figure" },
      h("div", { class: "label" }, label), h("div", { class: "value" }, value), note && h("div", { class: "note" }, note));
    clear(stats, h("div", { class: "figures" },
      figure("Uptime in this period", fmtUptime(s.uptime), `${s.checks.toLocaleString()} checks`),
      figure("Average response", fmtLatency(s.avg_ms)),
      figure("Slowest response", fmtLatency(s.max_ms)),
      figure("Uptime, last 30 days", fmtUptime(monitor?.uptime["30d"]), `${fmtUptime(monitor?.uptime["7d"])} over 7 days`)));
    if (mode === "chart") renderChart(chartHost, history);
    else renderHistoryTable(chartHost, history);
  }

  async function loadHistory(showLoading) {
    if (showLoading) chartHost.classList.add("loading"); // keep the old frame, dimmed
    try {
      history = await api(`/api/monitors/${id}/history?range=${state.range}`);
      historyAt = Date.now();
      renderHistory();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      chartHost.classList.remove("loading");
    }
  }

  function renderHead() {
    const m = monitor;
    const now = Date.now() / 1000;
    const since = m.status === "pending" ? "Waiting for the first check"
      : m.status === "paused" ? `since ${fmtDateTime(m.since)}`
      : `for ${fmtDuration(now - m.since)}`;
    const actions = admin && h("div", { class: "actions" },
      m.enabled && h("button", { class: "btn", onclick: () => act(() => api(`/api/monitors/${id}/check`, { method: "POST" }).then(() => new Promise((r) => setTimeout(r, 1200))), "Check started") }, icon("refresh"), "Check now"),
      h("button", { class: "btn", onclick: () => act(() => api(`/api/monitors/${id}/${m.enabled ? "pause" : "resume"}`, { method: "POST" }), m.enabled ? "Monitor paused" : "Monitor resumed") }, m.enabled ? "Pause" : "Resume"),
      h("button", { class: "btn", onclick: edit }, "Edit"),
      h("button", { class: "btn danger", onclick: remove }, "Delete"));
    clear(head,
      h("div", { class: "page-head" },
        h("div", { class: "grow" }, h("h1", null, m.name),
          h("div", { class: "sub" }, h("span", { class: "pill" }, TYPE_LABELS[m.type]), h("span", null, m.target),
            h("span", { class: "muted" }, `Checked every ${fmtInterval(m.interval)}`),
            m.group && h("span", { class: "muted" }, `In ${m.group}`))),
        actions),
      h("div", { class: `statusline ${m.status}` }, statusIcon(m.status),
        h("span", { class: "grow" }, h("span", { class: "state" }, STATUS_LABELS[m.status]), ` ${since}.`,
          m.detail && m.status !== "paused" ? ` ${m.detail}.` : ""),
        h("span", { class: "muted num" }, m.last_check ? `Last check ${fmtAgo(m.last_check, now)}` : "")));

    lower.replaceChildren(
      h("section", null, h("h2", { class: "section-title" }, "Incidents"),
        m.incidents.length
          ? h("div", { class: "table-scroll" }, h("table", { class: "table" },
            h("thead", null, h("tr", null, h("th", null, "Problem"), h("th", null, "Started"), h("th", { class: "right" }, "Lasted"))),
            h("tbody", null, m.incidents.map((i) => h("tr", null,
              h("td", null, statusBadge(i.severity), h("div", { class: "muted" }, i.cause)),
              h("td", { class: "num" }, fmtDateTime(i.started_at)),
              h("td", { class: "num right" }, i.ended_at ? fmtDuration(i.ended_at - i.started_at) : "Ongoing"))))))
          : h("p", { class: "empty" }, "No outages or slowdowns recorded.")),
      h("section", null, h("h2", { class: "section-title" }, "Latest checks"),
        m.checks.length
          ? h("div", { class: "table-scroll capped" }, h("table", { class: "table" },
            h("thead", null, h("tr", null, h("th", null, "Time"), h("th", null, "Result"), h("th", { class: "right" }, "Response"))),
            h("tbody", null, m.checks.map((c) => h("tr", null,
              h("td", { class: "num" }, fmtDateTime(c.ts)),
              h("td", null, h("div", { class: "row" }, statusBadge(c.state), c.detail && h("span", { class: "muted truncate" }, c.detail))),
              h("td", { class: "num right" }, fmtLatency(c.latency_ms)))))))
          : h("p", { class: "empty" }, "No checks have run yet.")));
  }

  async function edit() {
    const groups = [...new Set((state.overview?.monitors || []).map((m) => m.group).filter(Boolean))];
    if (await monitorDialog(monitor, groups)) { toast("Changes saved"); await tick(); loadHistory(false); }
  }

  async function remove() {
    const ok = await confirmDialog(`Delete ${monitor.name}?`, "The monitor and all of its history will be removed. This cannot be undone.", "Delete monitor");
    if (!ok) return;
    try {
      await api(`/api/monitors/${id}`, { method: "DELETE" });
      toast(`${monitor.name} deleted`);
      location.hash = "#/";
    } catch (err) {
      toast(err.message, "error");
    }
  }

  renderControls();
  return {
    async refresh() {
      if (missing) return;
      try {
        monitor = await api(`/api/monitors/${id}`);
      } catch (err) {
        if (err.status !== 404) throw err;
        missing = true;
        clear(main, h("div", { class: "card empty" }, h("h2", null, "Monitor not found"),
          h("p", null, "It may have been deleted."), h("a", { class: "btn", href: "#/" }, "Back to dashboard")));
        return;
      }
      renderHead();
      if (Date.now() - historyAt > 30000) await loadHistory(!history);
      else renderHistory();
    },
    destroy: () => resize.disconnect(),
  };
}

// --- incidents ----------------------------------------------------------------------

function incidentsView() {
  const body = h("div");
  clear(main, h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", null, "Incidents"),
    h("div", { class: "sub" }, "Every outage and slowdown, newest first. Ongoing problems stay at the top."))), body);
  return {
    interval: 10000,
    async refresh() {
      const { incidents, now } = await api("/api/incidents");
      if (!incidents.length) {
        clear(body, h("div", { class: "empty" }, h("h2", null, "No incidents"), h("p", null, "Nothing has gone down since monitoring started.")));
        return;
      }
      clear(body, h("div", { class: "table-scroll" }, h("table", { class: "table" },
        h("thead", null, h("tr", null, h("th", null, "Problem"), h("th", null, "Monitor"), h("th", null, "Cause"),
          h("th", null, "Started"), h("th", null, "Ended"), h("th", { class: "right" }, "Lasted"))),
        h("tbody", null, incidents.map((i) => h("tr", null,
          h("td", null, statusBadge(i.severity)),
          h("td", null, h("a", { href: `#/monitor/${i.monitor_id}` }, i.monitor_name)),
          h("td", { class: "secondary" }, i.cause || "—"),
          h("td", { class: "num" }, fmtDateTime(i.started_at)),
          h("td", { class: "num" }, i.ended_at ? fmtDateTime(i.ended_at) : h("span", { class: "pill" }, "Ongoing")),
          h("td", { class: "num right" }, fmtDuration((i.ended_at || now) - i.started_at))))))));
    },
  };
}

// --- settings -----------------------------------------------------------------------

function settingsView() {
  const general = h("div", { class: "card" });
  const channels = h("div", { class: "card" });
  const backup = h("div", { class: "card" });
  const security = h("div", { class: "card" });
  clear(main, h("div", { class: "page-head" }, h("h1", null, "Settings")), h("div", { class: "settings" }, general, channels, backup, security));

  async function renderGeneral() {
    const s = await api("/api/settings");
    const title = h("input", { class: "input", required: true, maxlength: 60, value: s.site_title });
    const retention = h("input", { class: "input", type: "number", min: 1, max: 365, required: true, value: s.retention_days });
    const publicView = checkbox("Show the dashboard without signing in", s.public_view,
      "Anyone who can open this address sees monitor names, addresses and status, read-only. Handy for a wall screen.");
    const degraded = checkbox("Alert on slow responses and expiring certificates", s.notify_degraded,
      "When off, alerts are only sent when something goes down or comes back.");
    clear(general, h("div", { class: "card-head" }, h("h2", null, "General")),
      h("form", { class: "card-body", onsubmit: (event) => {
        event.preventDefault();
        act(async () => {
          await api("/api/settings", { method: "PUT", body: {
            site_title: title.value, retention_days: Number(retention.value),
            public_view: publicView.input.checked, notify_degraded: degraded.input.checked } });
          await loadSession();
          renderTopbar();
        }, "Settings saved");
      } },
      h("div", { class: "field-row" },
        field("Dashboard title", title),
        field("Keep detailed check history (days)", retention, "Hourly summaries are kept for over a year.")),
      publicView.el, degraded.el,
      h("button", { class: "btn primary", type: "submit" }, "Save settings")));
  }

  async function renderChannels() {
    const list = await api("/api/notifiers");
    const addBtn = h("button", { class: "btn small", onclick: async () => { if (await channelDialog(null)) { toast("Channel added"); renderChannels(); } } }, icon("plus"), "Add channel");
    clear(channels, h("div", { class: "card-head" }, h("h2", { class: "grow" }, "Notification channels"), addBtn),
      list.length
        ? list.map((c) => h("div", { class: "channel" },
          h("div", { class: "grow" },
            h("div", { class: "row" }, h("strong", null, c.name), h("span", { class: "pill" }, CHANNEL_TYPES[c.type]?.label || c.type), !c.enabled && h("span", { class: "pill" }, "Off")),
            c.last_error ? h("div", { class: "err" }, `Last delivery failed: ${c.last_error}`)
              : h("div", { class: "muted" }, c.last_sent_at ? `Last alert sent ${fmtAgo(c.last_sent_at)}` : "No alerts sent yet")),
          h("button", { class: "btn small", onclick: async (event) => {
            event.target.disabled = true;
            try { await api(`/api/notifiers/${c.id}/test`, { method: "POST" }); toast("Test alert sent"); }
            catch (err) { toast(err.message, "error"); }
            renderChannels();
          } }, "Send test"),
          h("button", { class: "btn small", onclick: async () => { if (await channelDialog(c)) { toast("Channel saved"); renderChannels(); } } }, "Edit"),
          h("button", { class: "btn small danger", onclick: async () => {
            if (!(await confirmDialog(`Delete ${c.name}?`, "Alerts will no longer be sent to this channel.", "Delete channel"))) return;
            try { await api(`/api/notifiers/${c.id}`, { method: "DELETE" }); toast("Channel deleted"); } catch (err) { toast(err.message, "error"); }
            renderChannels();
          } }, "Delete")))
        : h("div", { class: "empty" }, h("h2", null, "No one is being alerted yet"),
          h("p", null, "Add email, Slack, Microsoft Teams, Discord, Telegram or a webhook to hear about outages the moment they are confirmed.")));
  }

  function renderBackup() {
    const days = h("select", { class: "input" }, [[7, "Last 7 days"], [30, "Last 30 days"], [90, "Last 90 days"], [365, "Last 12 months"]]
      .map(([v, l]) => h("option", { value: v, selected: v === 30 }, l)));
    const file = h("input", { type: "file", accept: ".json,application/json", hidden: true, onchange: async () => {
      const picked = file.files[0];
      file.value = "";
      if (!picked) return;
      try {
        const result = await api("/api/import", { method: "POST", body: JSON.parse(await picked.text()) });
        const problems = result.errors.length ? ` ${result.errors.length} could not be read: ${result.errors[0]}` : "";
        toast(`Imported ${result.imported}, skipped ${result.skipped} already present.${problems}`, result.errors.length ? "error" : "ok");
      } catch (err) {
        toast(err instanceof SyntaxError ? "That file is not valid JSON." : err.message, "error");
      }
    } });
    clear(backup, h("div", { class: "card-head" }, h("h2", null, "Reports and backup")),
      h("div", { class: "card-body" },
        h("div", { class: "field" }, h("div", { class: "label" }, "Uptime report"),
          h("div", { class: "row wrap" }, h("div", { style: "width:180px" }, days),
            h("button", { class: "btn", onclick: () => { location.href = `/api/report.csv?days=${days.value}`; } }, "Download CSV")),
          h("p", { class: "help" }, "Uptime, response times and outages per monitor. Opens in Excel or Google Sheets.")),
        h("div", { class: "field" }, h("div", { class: "label" }, "Monitor list"),
          h("div", { class: "row wrap" },
            h("a", { class: "btn", href: "/api/export", download: "netmon-monitors.json" }, "Export monitors"),
            h("button", { class: "btn", onclick: () => file.click() }, "Import monitors"), file),
          h("p", { class: "help" }, "Import accepts an export from this app or a config.json from version 1. Monitors that already exist are skipped."))));
  }

  function renderSecurity() {
    clear(security, h("div", { class: "card-head" }, h("h2", null, "Security")),
      h("div", { class: "card-body" },
        h("p", { class: "secondary", style: "margin-bottom:12px" }, "Changing the admin password signs out every other browser."),
        h("button", { class: "btn", onclick: () => {
          const current = h("input", { class: "input", type: "password", required: true, autocomplete: "current-password" });
          const next = h("input", { class: "input", type: "password", required: true, minlength: 8, autocomplete: "new-password" });
          const repeat = h("input", { class: "input", type: "password", required: true, autocomplete: "new-password" });
          openDialog({
            title: "Change admin password", narrow: true, submitLabel: "Change password",
            body: [field("Current password", current), field("New password", next, "At least 8 characters."), field("Repeat new password", repeat)],
            onSubmit: async () => {
              if (next.value !== repeat.value) throw new Error("The new passwords do not match.");
              await api("/api/password", { method: "POST", body: { current: current.value, new: next.value } });
              toast("Password changed");
            },
          });
        } }, "Change password")));
  }

  renderBackup();
  renderSecurity();
  let loaded = false;
  return {
    interval: 60000,
    async refresh() {
      if (loaded) return; // forms are not re-rendered under the person typing in them
      await Promise.all([renderGeneral(), renderChannels()]);
      loaded = true;
    },
  };
}

// --- start --------------------------------------------------------------------------

async function start() {
  const saved = localStorage.getItem("netmon-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  installTips();
  try {
    await loadSession();
  } catch (err) {
    clear(main, h("div", { class: "card empty" }, h("h2", null, "Cannot reach the monitor"), h("p", null, err.message),
      h("button", { class: "btn", onclick: () => location.reload() }, "Try again")));
    return;
  }
  window.addEventListener("hashchange", route);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") tick(); });
  setInterval(updateLive, 5000);
  await route();
}

start();
