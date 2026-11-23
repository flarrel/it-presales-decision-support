const byId = (id) => document.getElementById(id);
const whole = new Intl.NumberFormat("en", { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });
const percent = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 1 });
const shortDate = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" });
const fullDate = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const pageSize = 20;
const modelLabels = { seasonal_naive: "Seasonal naive", trailing_average: "Trailing average" };
const exportLabels = {
  tickets: "Validated tickets (CSV)",
  weekly: "Weekly queue metrics (CSV)",
  staffing: "Staffing comparisons (CSV)",
  sensitivity: "Handling-time sensitivity (CSV)",
};
const plots = new Map();
const state = { report: null, selection: null, page: 0, drill: null, ticketRequest: null };

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function value(number, format = decimal) {
  return typeof number === "number" && Number.isFinite(number) ? format.format(number) : "—";
}

function date(iso, format = fullDate) {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.valueOf()) ? iso : format.format(parsed);
}

function lastDayOfWeek(week) {
  if (!week) return "";
  const end = new Date(week);
  end.setUTCDate(end.getUTCDate() + 6);
  return end.toISOString().slice(0, 10);
}

function message(id, text) {
  const target = byId(id);
  target.textContent = text;
  target.hidden = !text;
}

function showTable(target, columns, rows, label, empty = "No matching records.") {
  target.replaceChildren();
  if (!rows.length) {
    target.append(node("p", empty, "empty"));
    return;
  }

  const wrap = node("div", undefined, "table-wrap");
  const table = node("table");
  table.setAttribute("aria-label", label);
  const heading = table.createTHead().insertRow();
  for (const column of columns) {
    const th = node("th", column[0], column[2] ? "numeric" : undefined);
    th.scope = "col";
    heading.append(th);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const [, content, numeric] of columns) {
      const cell = tr.insertCell();
      const result = content(row);
      if (result instanceof Node) cell.append(result);
      else cell.textContent = result ?? "—";
      if (numeric) cell.classList.add("numeric");
    }
  }
  wrap.append(table);
  target.append(wrap);
}

function numbers(label, field, format = whole) {
  return [label, (row) => value(row[field], format), true];
}

function groupByWeek(rows) {
  const totals = new Map();
  for (const row of rows) {
    if (!totals.has(row.week)) {
      totals.set(row.week, { week: row.week, tickets: 0, resolved_tickets: 0, sla_breaches: 0 });
    }
    const week = totals.get(row.week);
    week.tickets += row.tickets ?? 0;
    week.resolved_tickets += row.resolved_tickets ?? 0;
    week.sla_breaches += row.sla_breaches ?? 0;
  }
  return [...totals.values()].sort((left, right) => left.week.localeCompare(right.week));
}

function applyOptions(select, options, label) {
  for (const option of options) {
    const choice = node("option", label(option));
    choice.value = option;
    select.append(choice);
  }
}

function filters() {
  return { queue: byId("queue").value, start: byId("from").value, end: byId("through").value };
}

function byQueue(rows, queue) {
  return queue ? rows.filter((row) => row.queue === queue) : rows;
}

function chart(id, dates, series, options = {}) {
  const canvas = byId(id);
  plots.set(canvas, () => draw(canvas, dates, series, options));
  plots.get(canvas)();
}

function draw(canvas, dates, series, { percentage = false, bars = false } = {}) {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const context = canvas.getContext("2d");
  if (!context || !width || !height) return;
  context.scale(scale, scale);
  context.font = "11px system-ui, sans-serif";
  context.fillStyle = "#4c5d6a";

  const values = series.flatMap((item) => item.values).filter(Number.isFinite);
  if (!values.length || !dates.length) {
    context.textAlign = "center";
    context.fillText("No data for this view", width / 2, height / 2);
    return;
  }

  const bounds = { top: 12, right: width - 16, bottom: height - 30, left: 44 };
  const topValue = Math.max(...values, percentage ? 0.01 : 1);
  const unit = 10 ** Math.floor(Math.log10(topValue / 4));
  const step = Math.max(percentage ? 0.0025 : 1, Math.ceil(topValue / (4 * unit)) * unit);
  const maximum = step * 4;
  const span = bounds.right - bounds.left;
  const x = bars
    ? (index) => bounds.left + span * (index + 0.5) / dates.length
    : (index) => bounds.left + span * (dates.length === 1 ? 0.5 : index / (dates.length - 1));
  const y = (number) => bounds.bottom - (bounds.bottom - bounds.top) * number / maximum;

  context.textAlign = "right";
  context.textBaseline = "middle";
  for (let tick = 0; tick <= 4; tick++) {
    const amount = tick * step;
    context.strokeStyle = "#dce6eb";
    context.beginPath();
    context.moveTo(bounds.left, y(amount));
    context.lineTo(bounds.right, y(amount));
    context.stroke();
    const tickLabel = amount >= 1000 && !percentage ? `${decimal.format(amount / 1000)}k` : value(amount, percentage ? percent : whole);
    context.fillText(tickLabel, bounds.left - 7, y(amount));
  }

  const labelCount = Math.min(dates.length, Math.max(2, Math.floor(span / 86)));
  for (let label = 0; label < labelCount; label++) {
    const index = labelCount === 1 ? 0 : Math.round(label * (dates.length - 1) / (labelCount - 1));
    const position = x(index);
    const labelText = date(dates[index], shortDate);
    context.textAlign = "center";
    const halfLabel = context.measureText(labelText).width / 2 + 2;
    context.fillText(labelText, Math.min(width - halfLabel, Math.max(halfLabel, position)), height - 13);
  }

  for (const item of series) {
    context.strokeStyle = item.color;
    context.fillStyle = item.color;
    if (bars) {
      const barWidth = Math.max(1, Math.min(28, 0.78 * span / dates.length));
      item.values.forEach((number, index) => {
        if (Number.isFinite(number)) {
          context.fillRect(x(index) - barWidth / 2, y(number), barWidth, bounds.bottom - y(number));
        }
      });
      continue;
    }

    context.lineWidth = 2.2;
    context.setLineDash(item.dashed ? [6, 4] : []);
    context.beginPath();
    let first = true;
    item.values.forEach((number, index) => {
      if (!Number.isFinite(number)) {
        first = true;
        return;
      }
      if (first) context.moveTo(x(index), y(number));
      else context.lineTo(x(index), y(number));
      first = false;
    });
    context.stroke();
    context.setLineDash([]);
    if (dates.length <= 18) {
      item.values.forEach((number, index) => {
        if (!Number.isFinite(number)) return;
        context.beginPath();
        context.arc(x(index), y(number), 2.7, 0, Math.PI * 2);
        context.fill();
      });
    }
  }
}

function renderHistory({ queue, start, end }) {
  const matching = byQueue(state.report.weekly, queue).filter((row) => row.week >= start && row.week <= end);
  const weekly = groupByWeek(matching);
  const totals = weekly.reduce((sum, row) => ({
    tickets: sum.tickets + row.tickets,
    resolved: sum.resolved + row.resolved_tickets,
    breached: sum.breached + row.sla_breaches,
  }), { tickets: 0, resolved: 0, breached: 0 });
  const breachRate = (row) => row.resolved_tickets ? row.sla_breaches / row.resolved_tickets : null;
  const cards = [
    ["Tickets opened", value(totals.tickets, whole)],
    ["Resolved tickets", value(totals.resolved, whole)],
    ["SLA breaches", value(totals.breached, whole)],
    ["SLA breach rate", value(totals.resolved ? totals.breached / totals.resolved : null, percent)],
  ];
  byId("summary").replaceChildren(...cards.map(([label, statistic]) => {
    const card = node("div", undefined, "kpi");
    card.append(node("p", label), node("strong", statistic));
    return card;
  }));

  const dates = weekly.map((row) => row.week);
  chart("volume-chart", dates, [{ values: weekly.map((row) => row.tickets), color: "#007266" }], { bars: true });
  chart("sla-chart", dates, [{ values: weekly.map(breachRate), color: "#ab5023" }], { percentage: true });
  const weekAction = (row) => {
    const button = node("button", date(row.week), "table-action");
    button.type = "button";
    button.setAttribute("aria-label", `Inspect ${row.queue}, week of ${date(row.week)}`);
    button.addEventListener("click", () => {
      state.drill = { queue: row.queue, start: row.week, end: row.week };
      state.page = 0;
      byId("ticket-details").open = true;
      byId("ticket-details").scrollIntoView({ block: "nearest" });
      byId("ticket-details").querySelector("summary").focus();
      void loadTickets();
    });
    return button;
  };
  showTable(byId("weekly-table"), [
    ["Week of", weekAction],
    ["Queue", (row) => row.queue],
    numbers("Opened", "tickets"),
    numbers("Resolved", "resolved_tickets"),
    numbers("SLA breaches", "sla_breaches"),
    ["Breach rate", (row) => value(breachRate(row), percent), true],
  ], matching, "Weekly queue metrics; select a week to inspect its tickets");
}

function renderForecast(queue) {
  const forecast = state.report.forecast;
  const predictions = byQueue(forecast.predictions, queue);
  const forecastDates = [...new Set(predictions.map((row) => row.week))].sort();
  const historicalRows = byQueue(state.report.weekly, queue);
  const forecastQueues = new Set(predictions.map((row) => row.queue));
  const comparableHistory = forecastDates.length ? historicalRows.filter((row) => forecastQueues.has(row.queue)) : historicalRows;
  const actuals = groupByWeek(comparableHistory)
    .filter((row) => !forecastDates.length || row.week < forecastDates[0]).slice(-12);
  const actualDates = actuals.map((row) => row.week);
  const totals = new Map();
  for (const row of predictions) totals.set(row.week, (totals.get(row.week) ?? 0) + row.predicted);
  const actualValues = actuals.map((row) => row.tickets);
  byId("forecast-context").textContent = forecastDates.length
    ? [
      `${forecast.horizon_weeks}-week outlook`,
      "Up to 12 weeks of recent actuals",
      comparableHistory.length < historicalRows.length ? "Queues with sufficient history only" : null,
    ].filter(Boolean).join(" · ")
    : "Forecast unavailable for the selected queues.";
  chart("forecast-chart", [...actualDates, ...forecastDates], [
    { values: actualValues, color: "#007266" },
    {
      values: [...actualValues.map((number, index) => index === actualValues.length - 1 ? number : null),
        ...forecastDates.map((week) => totals.get(week))],
      color: "#7b43aa",
      dashed: true,
    },
  ]);

  showTable(byId("forecast-metrics"), [
    ["Queue", (row) => row.queue],
    ["Model", (row) => `${modelLabels[row.model] ?? row.model}${row.selected ? " · used" : ""}`],
    numbers("MAE", "mae", decimal),
    numbers("Samples", "n"),
  ], byQueue(forecast.metrics, queue), "Forecast holdout accuracy", "There is insufficient history for this view.");

  const target = byId("forecast-data");
  const forecastValues = node("div");
  const holdoutValues = node("div");
  target.replaceChildren(node("h3", "Predictions"), forecastValues, node("h3", "Holdout predictions"), holdoutValues);
  showTable(forecastValues, [
    ["Week of", (row) => date(row.week)],
    ["Queue", (row) => row.queue],
    numbers("Predicted tickets", "predicted", decimal),
    ["Model", (row) => modelLabels[row.model] ?? row.model],
  ], predictions, "Forecast predictions");
  const backtest = byQueue(forecast.backtest, queue);
  showTable(holdoutValues, [
    ["Origin week", (row) => date(row.origin_week)],
    ["Target week", (row) => date(row.week)],
    ["Queue", (row) => row.queue],
    numbers("Weeks ahead", "horizon"),
    numbers("Actual", "actual"),
    numbers("Seasonal naive", "seasonal_naive", decimal),
    numbers("Trailing average", "trailing_average", decimal),
  ], backtest, "Holdout predictions");
}

function renderScenarios(queue) {
  const target = byId("scenarios");
  target.replaceChildren();
  for (const scenario of state.report.scenarios) {
    const panel = node("article", undefined, "panel");
    const reasons = Array.isArray(scenario.reasons) ? scenario.reasons.join(" ") : scenario.reasons;
    panel.append(node("h3", scenario.name), node("p",
      scenario.feasible ? "Feasible within budget and coverage constraints." : reasons || "The constraints are infeasible.",
      scenario.feasible ? "status" : undefined));
    const metrics = node("dl", undefined, "scenario-stats");
    const statistics = [
      ["Budget", value(scenario.budget)],
      ["Cost per added hour", value(scenario.hourly_rate)],
      ["Min. coverage", value(scenario.minimum_coverage, percent)],
      ["Baseline unmet (h)", value(scenario.baseline_unmet_hours)],
      ["Plan unmet (h)", value(scenario.recommended_unmet_hours)],
      ["Added hours", value(scenario.extra_hours)],
      ["Added cost", value(scenario.extra_cost)],
    ];
    for (const [label, statistic] of statistics) {
      const entry = node("div");
      entry.append(node("dt", label), node("dd", statistic));
      metrics.append(entry);
    }
    const details = node("details", undefined, "data-details");
    const rows = node("div");
    details.append(node("summary", queue ? `Plan by week for ${queue}` : "Plan by queue and week"), rows);
    showTable(rows, [
      ["Week of", (row) => date(row.week)],
      ["Queue", (row) => row.queue],
      numbers("Demand (h)", "workload_hours", decimal),
      numbers("Baseline (h)", "baseline_hours", decimal),
      numbers("Added (h)", "extra_hours", decimal),
      numbers("Scheduled (h)", "staffed_hours", decimal),
      numbers("Unmet (h)", "unmet_hours", decimal),
      numbers("Coverage", "coverage", percent),
    ], byQueue(scenario.rows ?? [], queue), `${scenario.name} staffing plan`);
    panel.append(metrics, details);
    target.append(panel);
  }
  if (!state.report.scenarios.length) target.append(node("p", "No staffing scenarios available."));

  const rows = byQueue(state.report.sensitivity ?? [], queue);
  showTable(byId("sensitivity"), [
    ["Scenario", (row) => row.scenario],
    ["Queue", (row) => row.queue],
    ["Week of", (row) => date(row.week)],
    ["Assumption", (row) => row.assumption],
    ["Handling sample", (row) => row.source],
    numbers("Observations", "observations"),
    numbers("Minutes/ticket", "handling_minutes", decimal),
    numbers("Scheduled (h)", "scheduled_hours", decimal),
    numbers("Required (h)", "required_hours", decimal),
    numbers("Shortfall (h)", "shortfall_hours", decimal),
    ["Demand covered", (row) => row.covered ? "Yes" : "No"],
  ], rows, "Staffing sensitivity to handling time", state.report.sensitivity_status || "No sensitivity results for this view.");
}

function renderValidation() {
  const validation = state.report.validation;
  byId("validation-summary").textContent =
    `${value(validation.accepted, whole)} accepted · ${value(validation.rejected, whole)} rejected`;
  showTable(byId("validation"), [
    ["Validation issue", (row) => row.rule.replaceAll("_", " ")],
    numbers("Count", "count"),
  ], Object.entries(validation.issues ?? {}).map(([rule, count]) => ({ rule, count })), "Import validation issues", "No validation issues.");
  for (const [key, label] of Object.entries(exportLabels)) {
    const href = state.report.exports?.[key];
    if (!href) continue;
    const link = node("a", label);
    link.href = href;
    const item = node("li");
    item.append(link);
    byId("exports").append(item);
    if (key === "weekly") {
      byId("weekly-export").href = href;
      byId("weekly-export").hidden = false;
    }
  }
}

async function loadTickets() {
  if (!byId("ticket-details").open) return;
  state.ticketRequest?.abort();
  const request = new AbortController();
  state.ticketRequest = request;
  const scope = state.drill ?? state.selection;
  byId("clear-drill").hidden = !state.drill;
  const offset = state.page * pageSize;
  const search = new URLSearchParams({ ...scope, end: lastDayOfWeek(scope.end), limit: pageSize, offset });
  byId("ticket-context").textContent = [
    scope.queue || "All queues",
    scope.start === scope.end ? date(scope.start) : `${date(scope.start)} to ${date(scope.end)}`,
    "Loading tickets…",
  ].join(" · ");
  byId("tickets-prev").disabled = true;
  byId("tickets-next").disabled = true;
  try {
    const response = await fetch(`/tickets.json?${search}`, { signal: request.signal });
    if (!response.ok) throw new Error(`Ticket request returned ${response.status}.`);
    const { rows, total } = await response.json();
    if (request.signal.aborted) return;
    byId("ticket-context").textContent = [
      scope.queue || "All queues",
      scope.start === scope.end ? date(scope.start) : `${date(scope.start)} to ${date(scope.end)}`,
      `${value(total, whole)} tickets`,
    ].join(" · ");
    showTable(byId("ticket-table"), [
      ["Ticket", (row) => row.ticket_id],
      ["Queue", (row) => row.queue],
      ["Opened", (row) => row.opened_at],
      ["Priority", (row) => row.priority],
      numbers("Handling (min)", "handling_minutes", decimal),
      numbers("SLA (min)", "sla_minutes", decimal),
      ["SLA outcome", (row) => row.resolved_at ? row.sla_breached ? "Breached" : "Met" : "Open"],
    ], rows, "Ticket details");
    byId("ticket-page").textContent = total ? `${offset + 1}–${offset + rows.length} of ${value(total, whole)}` : "";
    byId("tickets-prev").disabled = state.page === 0;
    byId("tickets-next").disabled = offset + rows.length >= total;
  } catch (error) {
    if (error.name === "AbortError" || request.signal.aborted) return;
    byId("ticket-context").textContent = error.message;
    byId("ticket-table").replaceChildren();
    byId("ticket-page").textContent = "";
  }
}

function render() {
  const selection = filters();
  const invalid = selection.start > selection.end;
  message("filter-error", invalid ? "Choose a start week on or before the through week. Results still show the last valid selection." : "");
  if (invalid) return;
  state.selection = selection;
  state.drill = null;
  state.page = 0;
  renderHistory(selection);
  renderForecast(selection.queue);
  renderScenarios(selection.queue);
  void loadTickets();
}

async function initialize() {
  try {
    const response = await fetch("/report.json");
    if (!response.ok) throw new Error(`The report request returned ${response.status}.`);
    const report = await response.json();
    state.report = report;
    byId("data-context").textContent = [
      report.simulated ? "Simulated service desk data" : "Service desk data",
      report.as_of ? `Data through ${date(report.as_of)}` : null,
    ].filter(Boolean).join(" · ");

    const queues = [...new Set(report.weekly.map((row) => row.queue))].sort();
    const weeks = [...new Set(report.weekly.map((row) => row.week))].sort();
    applyOptions(byId("queue"), queues, (queue) => queue);
    applyOptions(byId("from"), weeks, (week) => date(week));
    applyOptions(byId("through"), weeks, (week) => date(week));
    if (weeks.length) {
      byId("from").firstElementChild.defaultSelected = true;
      byId("through").lastElementChild.defaultSelected = true;
    }

    byId("report").hidden = false;
    renderValidation();
    render();
    byId("filters").addEventListener("change", render);
    byId("filters").addEventListener("reset", () => queueMicrotask(render));
    byId("ticket-details").addEventListener("toggle", () => {
      if (byId("ticket-details").open && !state.drill) void loadTickets();
    });
    byId("clear-drill").addEventListener("click", () => {
      state.drill = null;
      state.page = 0;
      void loadTickets();
    });
    byId("tickets-prev").addEventListener("click", () => {
      state.page = Math.max(0, state.page - 1);
      void loadTickets();
    });
    byId("tickets-next").addEventListener("click", () => {
      state.page++;
      void loadTickets();
    });
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) plots.get(entry.target.firstElementChild)?.();
    });
    for (const plot of document.querySelectorAll(".plot")) observer.observe(plot);
  } catch (error) {
    byId("report").hidden = true;
    byId("data-context").textContent = "";
    message("report-error", `Could not load the report. ${error.message}`);
  }
}

void initialize();
