// Response-time chart: one line (average per bucket), one axis, failed checks marked
// in a strip under the plot, crosshair + tooltip on hover, and a table twin.

import { clear, fmtClockShort, fmtDateTime, fmtDay, fmtLatency, fmtUptime, h, hideTip, showTip, svg } from "./util.js";

const MARGIN = { top: 12, right: 14, bottom: 40, left: 48 };
const HEIGHT = 260;
const STRIP = 6; // height of the failed-checks strip

function niceCeil(value) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (value <= step * power) return step * power;
  }
  return 10 * power;
}

function axisLabel(ms) {
  if (ms >= 1000) return `${+(ms / 1000).toFixed(2)} s`;
  return `${+ms.toFixed(2)} ms`;
}

function timeTicks(from, to, count) {
  const span = to - from;
  const steps = [300, 600, 900, 1800, 3600, 2 * 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 5 * 86400, 7 * 86400];
  const step = steps.find((s) => span / s <= count) || steps[steps.length - 1];
  // Align ticks to local clock boundaries so labels read 09:00, 12:00, ...
  const offset = new Date(from * 1000).getTimezoneOffset() * 60;
  const first = Math.ceil((from - offset) / step) * step + offset;
  const ticks = [];
  for (let t = first; t <= to; t += step) ticks.push(t);
  return { ticks, step };
}

export function renderChart(host, history) {
  hideTip();
  const points = history.points;
  const withLatency = points.filter((p) => p.avg !== null);
  if (!points.length) {
    clear(host, h("div", { class: "empty" }, h("h2", null, "No data for this period yet"),
      h("p", null, "Results appear here after the first checks have run.")));
    return;
  }

  const width = Math.max(320, Math.floor(host.clientWidth || 800));
  const plotW = width - MARGIN.left - MARGIN.right;
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;
  const yMax = niceCeil(Math.max(1, ...withLatency.map((p) => p.avg)) * 1.1);
  const x = (t) => MARGIN.left + ((t - history.from) / (history.to - history.from)) * plotW;
  const y = (v) => MARGIN.top + plotH - (v / yMax) * plotH;
  const mid = (p) => Math.min(history.to, p.t + history.bucket / 2);
  const baseY = MARGIN.top + plotH;

  const root = svg("svg", { viewBox: `0 0 ${width} ${HEIGHT}`, height: HEIGHT, role: "img",
    "aria-label": `Response time over the selected period. Average ${fmtLatency(history.summary.avg_ms)}, uptime ${fmtUptime(history.summary.uptime)}.` });

  // Grid and y axis: four clean steps.
  for (let i = 0; i <= 4; i++) {
    const value = (yMax / 4) * i;
    root.append(
      svg("line", { class: i === 0 ? "baseline" : "grid", x1: MARGIN.left, x2: width - MARGIN.right, y1: y(value), y2: y(value) }),
      svg("text", { x: MARGIN.left - 8, y: y(value) + 4, "text-anchor": "end" }, axisLabel(value)));
  }

  // X axis labels.
  const { ticks, step } = timeTicks(history.from, history.to, Math.max(3, Math.floor(plotW / 110)));
  for (const t of ticks) {
    const label = step >= 86400 ? fmtDay(t) : history.to - history.from > 86400 ? `${fmtDay(t)} ${fmtClockShort(t)}` : fmtClockShort(t);
    const tx = x(t);
    if (tx < MARGIN.left + 14 || tx > width - MARGIN.right - 14) continue;
    root.append(svg("text", { x: tx, y: HEIGHT - 8, "text-anchor": "middle" }, label));
  }

  // The line is broken wherever checks failed or data is missing.
  const segments = [];
  let current = [];
  let previous = null;
  for (const p of points) {
    const gap = previous && p.t - previous.t > history.bucket * 1.5;
    if (p.avg === null || gap) {
      if (current.length) segments.push(current);
      current = [];
    }
    if (p.avg !== null) current.push(p);
    previous = p;
  }
  if (current.length) segments.push(current);

  for (const segment of segments) {
    if (segment.length === 1) {
      root.append(svg("circle", { class: "dot", cx: x(mid(segment[0])), cy: y(segment[0].avg), r: 2 }));
      continue;
    }
    const path = segment.map((p, i) => `${i ? "L" : "M"}${x(mid(p)).toFixed(1)},${y(p.avg).toFixed(1)}`).join("");
    const firstX = x(mid(segment[0])).toFixed(1);
    const lastX = x(mid(segment[segment.length - 1])).toFixed(1);
    root.append(
      svg("path", { class: "area", d: `${path}L${lastX},${baseY}L${firstX},${baseY}Z` }),
      svg("path", { class: "line", d: path }));
  }

  // Failed checks: a strip directly under the baseline.
  for (const p of points) {
    if (p.up >= 1) continue;
    const left = Math.max(MARGIN.left, x(p.t));
    const right = Math.min(width - MARGIN.right, x(p.t + history.bucket));
    root.append(svg("rect", { class: "outage", x: left, y: baseY + 4, width: Math.max(2, right - left - 1), height: STRIP, rx: 1.5 }));
  }

  // Hover layer: the crosshair snaps to the nearest bucket.
  const cross = svg("line", { class: "cross", y1: MARGIN.top, y2: baseY + 4 + STRIP, visibility: "hidden" });
  const marker = svg("circle", { class: "marker", r: 5, visibility: "hidden" });
  const hit = svg("rect", { x: MARGIN.left, y: MARGIN.top, width: plotW, height: plotH + 4 + STRIP, fill: "transparent", tabindex: 0,
    "aria-label": "Response time chart. Use the left and right arrow keys to read values." });
  root.append(cross, marker, hit);
  let index = -1;

  const focusPoint = (i) => {
    index = Math.max(0, Math.min(points.length - 1, i));
    const p = points[index];
    const px = x(mid(p));
    cross.setAttribute("x1", px);
    cross.setAttribute("x2", px);
    cross.setAttribute("visibility", "visible");
    if (p.avg !== null) {
      marker.setAttribute("cx", px);
      marker.setAttribute("cy", y(p.avg));
      marker.setAttribute("visibility", "visible");
    } else {
      marker.setAttribute("visibility", "hidden");
    }
    const box = root.getBoundingClientRect();
    const scale = box.width / width;
    const failed = Math.round(p.n * (1 - p.up));
    showTip(box.left + px * scale, box.top + (p.avg !== null ? y(p.avg) : baseY) * scale,
      h("strong", null, p.avg !== null ? `${fmtLatency(p.avg)} average` : "No successful checks"),
      p.avg !== null && p.n > 1 ? h("span", null, `Slowest ${fmtLatency(p.max)}`) : null,
      h("span", null, failed ? `${failed} of ${p.n} checks failed` : `${p.n} check${p.n === 1 ? "" : "s"}, all passed`),
      h("span", null, fmtDateTime(p.t)));
  };
  const blur = () => {
    index = -1;
    cross.setAttribute("visibility", "hidden");
    marker.setAttribute("visibility", "hidden");
    hideTip();
  };

  hit.addEventListener("pointermove", (event) => {
    const box = root.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    let best = 0;
    for (let i = 1; i < points.length; i++) {
      if (Math.abs(x(mid(points[i])) - px) < Math.abs(x(mid(points[best])) - px)) best = i;
    }
    focusPoint(best);
  });
  hit.addEventListener("pointerleave", blur);
  hit.addEventListener("blur", blur);
  hit.addEventListener("focus", () => focusPoint(index < 0 ? points.length - 1 : index));
  hit.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") focusPoint(index - 1);
    else if (event.key === "ArrowRight") focusPoint(index + 1);
    else if (event.key === "Escape") hit.blur();
    else return;
    event.preventDefault();
  });

  clear(host, root,
    h("div", { class: "legend" },
      h("span", null, h("i", { class: "key-line" }), "Average response time"),
      h("span", null, h("i", { class: "key-box" }), "Failed checks")));
}

/** The same data as a table: every value in the chart is reachable without hovering. */
export function renderHistoryTable(host, history) {
  if (!history.points.length) {
    clear(host, h("div", { class: "empty" }, h("h2", null, "No data for this period yet")));
    return;
  }
  const rows = [...history.points].reverse().map((p) =>
    h("tr", null,
      h("td", { class: "num" }, fmtDateTime(p.t)),
      h("td", { class: "num right" }, fmtLatency(p.avg)),
      h("td", { class: "num right" }, fmtLatency(p.max)),
      h("td", { class: "num right" }, fmtUptime(p.up)),
      h("td", { class: "num right" }, p.n)));
  clear(host, h("div", { class: "table-scroll capped" },
    h("table", { class: "table" },
      h("thead", null, h("tr", null,
        h("th", null, "Period starting"), h("th", { class: "right" }, "Average"),
        h("th", { class: "right" }, "Slowest"), h("th", { class: "right" }, "Passed"),
        h("th", { class: "right" }, "Checks"))),
      h("tbody", null, rows))));
}
