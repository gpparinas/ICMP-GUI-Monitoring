// Dialogs for adding and editing monitors and notification channels.

import { api, checkbox, field, h, openDialog } from "./util.js";

const TYPES = {
  icmp: { label: "Ping (ICMP)", target: "Host name or IP address", placeholder: "192.168.1.1", help: "Is the device reachable on the network? Good for routers, switches, printers and servers." },
  tcp: { label: "TCP port", target: "Host and port", placeholder: "192.168.1.10:3389", help: "Is a service accepting connections? For example :3389 remote desktop, :445 file shares, :5432 a database." },
  http: { label: "Website / HTTP(S)", target: "URL", placeholder: "https://intranet.example.com", help: "Does a page load with the expected status? Also warns before the TLS certificate expires." },
  dns: { label: "DNS lookup", target: "Host name", placeholder: "example.com", help: "Does the name resolve, and optionally to the address you expect?" },
};

const INTERVALS = [[15, "15 seconds"], [30, "30 seconds"], [60, "1 minute"], [120, "2 minutes"], [300, "5 minutes"], [600, "10 minutes"], [1800, "30 minutes"], [3600, "1 hour"]];

function select(options, value) {
  return h("select", { class: "input" }, options.map(([v, label]) => h("option", { value: v, selected: String(v) === String(value) }, label)));
}

export function monitorDialog(monitor, groups) {
  const editing = Boolean(monitor);
  const m = monitor || { type: "icmp", interval: 60, timeout: 5, retries: 2, method: "GET", expected_status: "200-399", verify_tls: true, cert_warn_days: 14 };

  const name = h("input", { class: "input", required: true, maxlength: 80, value: m.name || "", placeholder: "Front office printer" });
  const type = select(Object.entries(TYPES).map(([k, t]) => [k, t.label]), m.type);
  const typeHelp = h("p", { class: "help" });
  const target = h("input", { class: "input", required: true, value: m.target || "", autocapitalize: "off", spellcheck: "false" });
  const targetLabel = h("label", null);
  const group = h("input", { class: "input", maxlength: 60, value: m.group || "", list: "group-options", placeholder: "Network, Servers, Websites…" });
  const intervals = INTERVALS.some(([v]) => v === m.interval) ? INTERVALS : [...INTERVALS, [m.interval, `${m.interval} seconds`]].sort((a, b) => a[0] - b[0]);
  const interval = select(intervals, m.interval);
  const timeout = h("input", { class: "input", type: "number", min: 0.5, max: 60, step: 0.5, required: true, value: m.timeout });
  const retries = h("input", { class: "input", type: "number", min: 0, max: 10, step: 1, required: true, value: m.retries });
  const warn = h("input", { class: "input", type: "number", min: 1, step: 1, value: m.latency_warn_ms ?? "", placeholder: "Off" });

  const method = select([["GET", "GET"], ["HEAD", "HEAD"]], m.method || "GET");
  const expected = h("input", { class: "input", value: m.expected_status || "200-399" });
  const keyword = h("input", { class: "input", maxlength: 200, value: m.keyword || "", placeholder: "Optional" });
  const verify = checkbox("Verify the TLS certificate", m.verify_tls !== false, "Turn off only for devices with self-signed certificates.");
  const certDays = h("input", { class: "input", type: "number", min: 0, max: 365, value: m.cert_warn_days ?? 14 });
  const expectedIp = h("input", { class: "input", value: m.expected_ip || "", placeholder: "Optional" });

  const httpFields = h("fieldset", null, h("legend", null, "Website options"),
    h("div", { class: "field-row" },
      field("Method", method),
      field("Accepted status codes", expected, "For example 200-399 or 200,401"),
      field("Certificate warning (days)", certDays, "0 turns the warning off")),
    field("Page must contain", keyword, "Marks the site down if this text is missing from the page."),
    verify.el);
  const dnsFields = h("fieldset", null, h("legend", null, "DNS options"),
    field("Expected IP address", expectedIp, "Marks the name down if it resolves to anything else."));

  const sync = () => {
    const t = TYPES[type.value];
    targetLabel.textContent = t.target;
    target.placeholder = t.placeholder;
    typeHelp.textContent = t.help;
    httpFields.hidden = type.value !== "http";
    dnsFields.hidden = type.value !== "dns";
  };
  type.addEventListener("change", sync);
  sync();

  target.id = "monitor-target";
  targetLabel.setAttribute("for", target.id);

  const body = [
    h("div", { class: "field-row" }, field("Name", name), field("Group", group)),
    h("datalist", { id: "group-options" }, groups.map((g) => h("option", { value: g }))),
    h("div", { class: "field" }, h("label", { for: "monitor-type" }, "Check type"), Object.assign(type, { id: "monitor-type" }), typeHelp),
    h("div", { class: "field" }, targetLabel, target),
    h("div", { class: "field-row" },
      field("Check every", interval),
      field("Timeout (seconds)", timeout),
      field("Retries before alerting", retries)),
    field("Slow response warning (ms)", warn, "Shows the monitor as degraded when replies take longer than this. Leave empty to turn off."),
    httpFields,
    dnsFields,
  ];

  return openDialog({
    title: editing ? `Edit ${monitor.name}` : "Add a monitor",
    body,
    submitLabel: editing ? "Save changes" : "Add monitor",
    onSubmit: () => {
      const payload = {
        name: name.value, type: type.value, target: target.value, group: group.value,
        interval: Number(interval.value), timeout: Number(timeout.value), retries: Number(retries.value),
        latency_warn_ms: warn.value === "" ? null : Number(warn.value),
        enabled: editing ? monitor.enabled : true,
        method: method.value, expected_status: expected.value, keyword: keyword.value,
        verify_tls: verify.input.checked, cert_warn_days: Number(certDays.value || 0),
        expected_ip: expectedIp.value,
      };
      return editing
        ? api(`/api/monitors/${monitor.id}`, { method: "PUT", body: payload })
        : api("/api/monitors", { method: "POST", body: payload });
    },
  });
}

// --- notification channels -------------------------------------------------------

export const CHANNEL_TYPES = {
  email: { label: "Email (SMTP)" },
  slack: { label: "Slack", urlHelp: "Incoming webhook URL from your Slack workspace." },
  discord: { label: "Discord", urlHelp: "Channel settings → Integrations → Webhooks → Copy webhook URL." },
  teams: { label: "Microsoft Teams", urlHelp: "URL of a Teams Workflow using the “When a Teams webhook request is received” trigger." },
  telegram: { label: "Telegram" },
  webhook: { label: "Webhook (JSON)", urlHelp: "Receives a JSON POST for every alert. Use it to connect anything else." },
};

export function channelDialog(channel) {
  const editing = Boolean(channel);
  const c = channel || { type: "email", enabled: true, config: {} };
  const cfg = c.config || {};

  const name = h("input", { class: "input", required: true, maxlength: 80, value: c.name || "", placeholder: "IT team email" });
  const type = select(Object.entries(CHANNEL_TYPES).map(([k, t]) => [k, t.label]), c.type);
  const enabled = checkbox("Send alerts to this channel", c.enabled !== false);

  const host = h("input", { class: "input", value: cfg.host || "", placeholder: "smtp.office365.com" });
  const port = h("input", { class: "input", type: "number", min: 1, max: 65535, value: cfg.port || 587 });
  const security = select([["starttls", "STARTTLS (port 587)"], ["ssl", "SSL/TLS (port 465)"], ["none", "None"]], cfg.security || "starttls");
  const username = h("input", { class: "input", value: cfg.username || "", autocomplete: "off" });
  const password = h("input", { class: "input", type: "password", value: cfg.password || "", autocomplete: "new-password" });
  const from = h("input", { class: "input", type: "email", value: cfg.from_addr || "", placeholder: "monitor@yourcompany.com" });
  const to = h("input", { class: "input", value: cfg.to_addrs || "", placeholder: "it@yourcompany.com, owner@yourcompany.com" });
  const url = h("input", { class: "input", value: cfg.url || "", placeholder: "https://…", autocomplete: "off" });
  const urlHelp = h("p", { class: "help" });
  const token = h("input", { class: "input", type: "password", value: cfg.bot_token || "", autocomplete: "new-password" });
  const chat = h("input", { class: "input", value: cfg.chat_id || "", placeholder: "-1001234567890" });

  const emailFields = h("div", null,
    h("div", { class: "field-row" }, field("SMTP server", host), field("Port", port), field("Security", security)),
    h("div", { class: "field-row" }, field("User name", username), field("Password", password)),
    field("From address", from),
    field("Send to", to, "Separate several addresses with commas."));
  const urlFields = h("div", { class: "field" }, h("label", { for: "channel-url" }, "Webhook URL"), Object.assign(url, { id: "channel-url" }), urlHelp);
  const telegramFields = h("div", null,
    field("Bot token", token, "Create a bot with @BotFather and paste its token."),
    field("Chat ID", chat, "The user, group or channel that should receive alerts."));

  const sync = () => {
    emailFields.hidden = type.value !== "email";
    telegramFields.hidden = type.value !== "telegram";
    urlFields.hidden = ["email", "telegram"].includes(type.value);
    urlHelp.textContent = CHANNEL_TYPES[type.value].urlHelp || "";
  };
  type.addEventListener("change", sync);
  sync();

  return openDialog({
    title: editing ? `Edit ${channel.name}` : "Add a notification channel",
    body: [h("div", { class: "field-row" }, field("Name", name), field("Type", type)), emailFields, urlFields, telegramFields, enabled.el],
    submitLabel: editing ? "Save changes" : "Add channel",
    onSubmit: () => {
      let config;
      if (type.value === "email") {
        config = { host: host.value, port: Number(port.value), security: security.value, username: username.value,
          password: password.value, from_addr: from.value, to_addrs: to.value };
      } else if (type.value === "telegram") {
        config = { bot_token: token.value, chat_id: chat.value };
      } else {
        config = { url: url.value };
      }
      const payload = { name: name.value, type: type.value, enabled: enabled.input.checked, config };
      return editing
        ? api(`/api/notifiers/${channel.id}`, { method: "PUT", body: payload })
        : api("/api/notifiers", { method: "POST", body: payload });
    },
  });
}
