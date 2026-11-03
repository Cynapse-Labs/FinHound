/* FinHound dashboard: factor research, signal research, and data health views. */
"use strict";

(function () {
  const { fmt, esc } = FH;
  const state = FH.state;

  const FACTORS = [
    { id: "momentum_12_1", label: "Momentum 12-1", description: "12-month total return, skipping the most recent month." },
    { id: "reversal", label: "Reversal", description: "Negative 1-month return; short-term mean reversion." },
    { id: "volatility", label: "Low volatility", description: "Negative 60-day realized volatility." },
    { id: "distance_high", label: "Distance from 52W high", description: "Close relative to the trailing 52-week high." },
    { id: "dollar_volume", label: "Dollar volume", description: "20-day average dollar volume; liquidity exposure." },
  ];
  const FACTOR_LABELS = Object.fromEntries(FACTORS.map((factor) => [factor.id, factor.label]));
  const FACTOR_DESCRIPTIONS = Object.fromEntries(FACTORS.map((factor) => [factor.id, factor.description]));

  /* --------------------------------- Factors -------------------------------- */

  function ensureFactorControls() {
    const select = document.getElementById("factor-name");
    if (!select) return;
    if (!select.options.length) {
      FH.fillSelect(
        select,
        FACTORS.map((factor) => ({ value: factor.id, label: factor.label })),
        state.factor.name
      );
    }
    if (!state.factor.symbol) {
      state.factor.symbol = state.universe.find((symbol) => symbol !== "SPY") || state.universe[0] || null;
    }
    FH.fillSelect(document.getElementById("factor-symbol"), state.universe, state.factor.symbol);
  }

  async function loadFactors() {
    ensureFactorControls();
    const symbol = state.factor.symbol;
    if (!symbol) {
      renderFactorEmpty();
      return;
    }
    const name = state.factor.name;
    const [rows, summary] = await Promise.all([
      FH.api(`/factors/${encodeURIComponent(symbol)}?factor=${encodeURIComponent(name)}`),
      FH.api(`/factors/summary?factor=${encodeURIComponent(name)}&limit=10`),
    ]);
    state.factor.rows = rows;
    state.factor.summary = summary;
    renderFactors();
  }
  FH.views.factors = loadFactors;

  function renderFactorEmpty() {
    const kpis = document.getElementById("factor-kpis");
    if (kpis) kpis.innerHTML = FH.empty("No factor data", "Run the pipeline after ingestion to compute factors.");
    ["factor-leaders", "factor-laggards"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.innerHTML = FH.empty("No exposures");
    });
    FH.chart("factor-chart")?.clear();
    const title = document.getElementById("factor-chart-title");
    if (title) title.textContent = "Standardized factor";
    const description = document.getElementById("factor-description");
    if (description) description.textContent = "Run the pipeline after ingestion.";
    const asof = document.getElementById("factor-asof");
    if (asof) asof.textContent = "No data";
  }

  function renderFactors() {
    const { rows, summary, symbol, name } = {
      rows: state.factor.rows,
      summary: state.factor.summary,
      symbol: state.factor.symbol,
      name: state.factor.name,
    };
    const asof = document.getElementById("factor-asof");
    const title = document.getElementById("factor-chart-title");
    const description = document.getElementById("factor-description");
    if (title) title.textContent = `${FACTOR_LABELS[name] || name} · ${symbol}`;
    if (description) description.textContent = FACTOR_DESCRIPTIONS[name] || "";
    if (asof) asof.innerHTML = rows.length ? `<strong>${esc(symbol)}</strong> · ${esc(fmt.date(rows[rows.length - 1].date))}` : esc(symbol || "");

    const section = summary?.factors?.[name] || { items: [], long: [], short: [], count: 0 };
    const items = section.items || [];
    const latest = rows.length ? rows[rows.length - 1] : null;
    const previous = rows.length > 21 ? rows[rows.length - 22] : null;
    const change = latest && previous ? FH.toNumber(latest.standardized) - FH.toNumber(previous.standardized) : null;
    const rank = items.findIndex((item) => item.symbol === symbol);
    const percentile = rank >= 0 && items.length > 1 ? 1 - rank / (items.length - 1) : null;

    document.getElementById("factor-kpis").innerHTML = [
      FH.kpi({
        label: "Standardized",
        value: latest ? fmt.num(latest.standardized, 3) : "—",
        sub: `${rows.length} observations`,
        tone: latest && latest.standardized > 0 ? "good" : latest && latest.standardized < 0 ? "bad" : "",
      }),
      FH.kpi({
        label: "Raw value",
        value: latest ? fmt.num(latest.raw_value, 4) : "—",
        sub: fmt.date(latest?.date),
      }),
      FH.kpi({
        label: "Cross-section",
        value: percentile === null ? "—" : `p${Math.round(percentile * 100)}`,
        sub: `${items.length} names · rank ${rank >= 0 ? rank + 1 : "—"}`,
      }),
      FH.kpi({
        label: "21d change",
        value: change === null ? "—" : fmt.signed(change, 3),
        sub: "z-score drift",
        tone: change > 0 ? "good" : change < 0 ? "bad" : "",
      }),
    ].join("");

    FH.chart("factor-chart")?.setData({
      type: "line",
      labels: rows.map((row) => row.date),
      series: [
        {
          name: FACTOR_LABELS[name] || name,
          color: "#2dd4bf",
          values: rows.map((row) => FH.toNumber(row.standardized)),
          area: true,
        },
      ],
      zeroLine: true,
      yFormat: (value) => fmt.num(value, 1),
      valueFormat: (value) => fmt.num(value, 3),
    });

    renderFactorSide("factor-leaders", section.long || [], items, "long");
    renderFactorSide("factor-laggards", section.short || [], items, "short");
  }

  function renderFactorSide(elementId, list, allItems, side) {
    const node = document.getElementById(elementId);
    if (!node) return;
    if (!list.length) {
      node.innerHTML = FH.empty("No exposures", "The factor summary is empty for this date.");
      return;
    }
    const maxAbs = Math.max(...allItems.map((item) => Math.abs(FH.toNumber(item.standardized) || 0)), 0.0001);
    node.innerHTML = `<div class="list">${list
      .map((item) => {
        const value = FH.toNumber(item.standardized);
        return FH.listRow({
          rank: item.rank,
          symbol: item.symbol,
          value: fmt.num(value, 3),
          width: (Math.abs(value || 0) / maxAbs) * 100,
          negative: side === "short" || (value || 0) < 0,
          meta: `raw ${fmt.num(item.raw_value, 3)}`,
        });
      })
      .join("")}</div>`;
  }

  function bindFactors() {
    document.getElementById("factor-symbol")?.addEventListener("change", (event) => {
      state.factor.symbol = event.target.value;
      loadFactors().catch((error) => FH.toast(error.message, "bad"));
    });
    document.getElementById("factor-name")?.addEventListener("change", (event) => {
      state.factor.name = event.target.value;
      loadFactors().catch((error) => FH.toast(error.message, "bad"));
    });
  }

  /* --------------------------------- Signals -------------------------------- */

  function ensureSignalControls() {
    if (!state.signal.name) {
      state.signal.name = state.signals[0] || null;
    }
    FH.fillSelect(document.getElementById("signal-name"), state.signals, state.signal.name);
  }

  async function loadSignals() {
    ensureSignalControls();
    const name = state.signal.name;
    if (!name) {
      renderSignalsEmpty();
      return;
    }
    const params = new URLSearchParams({ limit: String(state.signal.limit) });
    if (state.signal.date) params.set("date", state.signal.date);
    const snapshot = await FH.api(`/signals/${encodeURIComponent(name)}/snapshot?${params.toString()}`);
    state.signal.snapshot = snapshot;
    renderSignals();
  }
  FH.views.signals = loadSignals;

  function renderSignalsEmpty() {
    const kpis = document.getElementById("signal-kpis");
    if (kpis) kpis.innerHTML = FH.empty("No signal files", "Run the pipeline after ingestion to generate a signal.");
    ["signal-longs", "signal-shorts"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.innerHTML = FH.empty("No candidates");
    });
    FH.chart("signal-hist-chart")?.clear();
    FH.chart("signal-history-chart")?.clear();
    const coverage = document.getElementById("signal-coverage");
    if (coverage) coverage.textContent = "No signals";
  }

  function renderSignals() {
    const snapshot = state.signal.snapshot;
    if (!snapshot) return;
    const name = snapshot.name || state.signal.name;
    const items = snapshot.items || [];
    const coverage = document.getElementById("signal-coverage");
    const dateInput = document.getElementById("signal-date");
    if (dateInput && snapshot.first_date && snapshot.last_date) {
      dateInput.min = snapshot.first_date;
      dateInput.max = snapshot.last_date;
    }
    if (coverage) {
      coverage.innerHTML = `${fmt.int(snapshot.count)} scored · ${esc(fmt.date(snapshot.first_date))} → ${esc(fmt.date(snapshot.last_date))}`;
    }

    document.getElementById("signal-kpis").innerHTML = [
      FH.kpi({ label: "Scored", value: fmt.int(snapshot.count), sub: `${esc(name)}` }),
      FH.kpi({
        label: "Mean score",
        value: fmt.num(snapshot.mean, 3),
        sub: `std ${fmt.num(snapshot.std, 3)}`,
      }),
      FH.kpi({
        label: "Positive",
        value: `${fmt.int(snapshot.positive)} / ${fmt.int(snapshot.count)}`,
        sub: snapshot.count ? `${fmt.pctRaw((snapshot.positive / snapshot.count) * 100)} of universe` : "—",
      }),
      FH.kpi({
        label: "Dispersion",
        value: fmt.num(snapshot.std, 3),
        sub: `range ${fmt.num(snapshot.min, 2)} → ${fmt.num(snapshot.max, 2)}`,
      }),
    ].join("");

    renderSignalSide("signal-longs", snapshot.long || [], items, "long");
    renderSignalSide("signal-shorts", snapshot.short || [], items, "short");
    renderSignalHistogram(items);
    renderSignalHistory(snapshot.history || []);
  }

  function renderSignalSide(elementId, list, allItems, side) {
    const node = document.getElementById(elementId);
    if (!node) return;
    if (!list.length) {
      node.innerHTML = FH.empty("No candidates", "This signal has no scored names on the selected date.");
      return;
    }
    const maxAbs = Math.max(...allItems.map((item) => Math.abs(FH.toNumber(item.score) || 0)), 0.0001);
    node.innerHTML = `<div class="list">${list
      .map((item) => {
        const score = FH.toNumber(item.score);
        return FH.listRow({
          rank: item.rank,
          symbol: item.symbol,
          value: fmt.num(score, 3),
          width: (Math.abs(score || 0) / maxAbs) * 100,
          negative: side === "short" || (score || 0) < 0,
          meta: item.percentile === undefined ? "" : `p${Math.round(item.percentile * 100)}`,
        });
      })
      .join("")}</div>`;
  }

  function renderSignalHistogram(items) {
    const chart = FH.chart("signal-hist-chart");
    if (!chart) return;
    if (!items.length) {
      chart.clear();
      return;
    }
    const scores = items.map((item) => FH.toNumber(item.score)).filter((value) => value !== null).sort((a, b) => a - b);
    if (!scores.length) {
      chart.clear();
      return;
    }
    let low = scores[0];
    let high = scores[scores.length - 1];
    if (low === high) {
      low -= 0.5;
      high += 0.5;
    }
    const bins = Math.max(6, Math.min(16, Math.ceil(Math.sqrt(scores.length) * 2)));
    const width = (high - low) / bins;
    const counts = new Array(bins).fill(0);
    scores.forEach((score) => {
      const index = Math.min(bins - 1, Math.floor((score - low) / width));
      counts[index] += 1;
    });
    const centers = counts.map((_, index) => low + width * (index + 0.5));
    chart.setData({
      type: "bars",
      labels: centers.map((center) => center.toFixed(2)),
      series: [{ name: "Names", values: counts }],
      barColors: (_, index) => (centers[index] >= 0 ? "rgba(45,212,191,0.7)" : "rgba(248,113,113,0.7)"),
      yFormat: (value) => fmt.int(value),
      valueFormat: (value) => fmt.int(value),
      labelFormat: (value) => value,
    });
  }

  function renderSignalHistory(history) {
    const chart = FH.chart("signal-history-chart");
    if (!chart) return;
    if (!history.length) {
      chart.clear();
      return;
    }
    chart.setData({
      type: "line",
      labels: history.map((row) => row.date),
      series: [{ name: "Scored names", color: "#818cf8", values: history.map((row) => FH.toNumber(row.count)), area: true }],
      yFormat: (value) => fmt.int(value),
      valueFormat: (value) => fmt.int(value),
    });
  }

  function bindSignals() {
    document.getElementById("signal-name")?.addEventListener("change", (event) => {
      state.signal.name = event.target.value;
      state.signal.date = null;
      const dateInput = document.getElementById("signal-date");
      if (dateInput) dateInput.value = "";
      loadSignals().catch((error) => FH.toast(error.message, "bad"));
    });
    document.getElementById("signal-date")?.addEventListener("change", (event) => {
      state.signal.date = event.target.value || null;
      loadSignals().catch((error) => FH.toast(error.message, "bad"));
    });
    document.getElementById("signal-limit")?.addEventListener("change", (event) => {
      state.signal.limit = Number(event.target.value) || 10;
      loadSignals().catch((error) => FH.toast(error.message, "bad"));
    });
  }

  /* ------------------------------- Data health ------------------------------- */

  async function loadHealth() {
    const severity = document.getElementById("issue-severity")?.value || "";
    const issueUrl = `/qc/issues?limit=500${severity ? `&severity=${encodeURIComponent(severity)}` : ""}`;
    const [summary, issues, ingest, pipeline] = await Promise.all([
      FH.api("/qc/summary"),
      FH.api(issueUrl),
      FH.api("/ingest/runs?limit=25"),
      FH.api("/pipeline/runs?limit=25"),
    ]);
    state.qcSummary = summary;
    renderHealthKpis(summary, issues, pipeline);
    renderHealthSummary(summary);
    renderHealthIssues(issues);
    renderHealthIngest(ingest);
    renderHealthPipeline(pipeline);
  }
  FH.views.health = loadHealth;

  function renderHealthKpis(summary, issues, pipeline) {
    const errors = summary.reduce((sum, row) => sum + (Number(row.error_count) || 0), 0);
    const warnings = summary.reduce((sum, row) => sum + (Number(row.warn_count) || 0), 0);
    const missing = summary.reduce((sum, row) => sum + (Number(row.missing_days) || 0), 0);
    const expected = summary.reduce((sum, row) => sum + (Number(row.expected_days) || 0), 0);
    const latest = pipeline[0];
    document.getElementById("health-kpis").innerHTML = [
      FH.kpi({ label: "Symbols checked", value: fmt.int(summary.length), sub: `${fmt.int(expected)} expected sessions` }),
      FH.kpi({
        label: "Errors",
        value: fmt.int(errors),
        sub: errors ? "excluded from clean prices" : "none detected",
        tone: errors ? "bad" : "good",
      }),
      FH.kpi({
        label: "Warnings",
        value: fmt.int(warnings),
        sub: `${fmt.int(missing)} missing sessions`,
        tone: warnings ? "warn" : "good",
      }),
      FH.kpi({ label: "Issue rows", value: fmt.int(issues.length), sub: "latest 500 returned" }),
      FH.kpi({
        label: "Last pipeline",
        value: latest ? String(latest.status || "unknown").toUpperCase() : "—",
        sub: latest ? fmt.datetime(latest.finished_at) : "no runs recorded",
        tone: latest?.status === "ok" ? "good" : latest?.status === "failed" ? "bad" : "",
      }),
    ].join("");
  }

  function renderHealthSummary(summary) {
    const node = document.getElementById("health-summary");
    if (!node) return;
    if (!summary.length) {
      node.innerHTML = FH.empty("No quality summary", "Run the pipeline to populate QC results.");
      return;
    }
    const rows = summary
      .slice()
      .sort((a, b) => a.symbol.localeCompare(b.symbol))
      .map(
        (row) => `<tr>
          <td class="symbol">${esc(row.symbol)}</td>
          <td class="num">${fmt.date(row.first_date)}</td>
          <td class="num">${fmt.date(row.last_date)}</td>
          <td class="num">${fmt.int(row.expected_days)}</td>
          <td class="num ${row.missing_days ? "warn" : ""}">${fmt.int(row.missing_days)}</td>
          <td class="num ${row.error_count ? "down" : ""}">${fmt.int(row.error_count)}</td>
          <td class="num ${row.warn_count ? "warn" : ""}">${fmt.int(row.warn_count)}</td>
        </tr>`
      )
      .join("");
    node.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Symbol</th><th class="num">First</th><th class="num">Last</th><th class="num">Expected</th><th class="num">Missing</th><th class="num">Errors</th><th class="num">Warnings</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  function renderHealthIssues(issues) {
    const node = document.getElementById("health-issues");
    const note = document.getElementById("health-issue-note");
    if (!node) return;
    if (note) note.textContent = `${issues.length} rows`;
    if (!issues.length) {
      node.innerHTML = FH.empty("No quality issues", "The current filter returned no rows.");
      return;
    }
    const rows = issues
      .map(
        (row) => `<tr>
          <td class="mono">${fmt.date(row.date)}</td>
          <td class="symbol">${esc(row.symbol)}</td>
          <td>${esc(row.check_name)}</td>
          <td>${FH.severityBadge(row.severity)}</td>
          <td class="muted">${esc(row.detail)}</td>
        </tr>`
      )
      .join("");
    node.innerHTML = `<div class="table-wrap tall"><table>
      <thead><tr><th>Date</th><th>Symbol</th><th>Check</th><th>Severity</th><th>Detail</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  function renderHealthIngest(runs) {
    const node = document.getElementById("health-ingest");
    if (!node) return;
    if (!runs.length) {
      node.innerHTML = FH.empty("No ingestion runs", "Run `make ingest` or the ingest service.");
      return;
    }
    node.innerHTML = `<div class="list">${runs
      .map((run) => {
        const failures = Number(run.failures) || 0;
        return `<div class="list-row">
          <div class="lhs">
            ${failures ? FH.badge(`${failures} failures`, "bad") : FH.badge("ok", "good")}
            <span class="mono">${fmt.date(run.started_at)}</span>
          </div>
          <span class="faint mono">${fmt.int(run.symbols)} symbols · ${fmt.int(run.rows)} rows</span>
        </div>`;
      })
      .join("")}</div>`;
  }

  function renderHealthPipeline(runs) {
    const node = document.getElementById("health-pipeline");
    if (!node) return;
    if (!runs.length) {
      node.innerHTML = FH.empty("No pipeline runs", "Run `make all` or press Rebuild analytics.");
      return;
    }
    node.innerHTML = `<div class="list">${runs
      .map((run) => {
        const kind = run.status === "ok" ? "good" : run.status === "failed" ? "bad" : "warn";
        return `<div class="list-row">
          <div class="lhs">
            ${FH.badge(run.status || "unknown", kind)}
            <span class="mono">${fmt.datetime(run.started_at)}</span>
          </div>
          <span class="faint mono">${esc(run.file || "")}</span>
        </div>`;
      })
      .join("")}</div>`;
  }

  function bindHealth() {
    document.getElementById("issue-severity")?.addEventListener("change", () => {
      loadHealth().catch((error) => FH.toast(error.message, "bad"));
    });
    document.getElementById("reload-health")?.addEventListener("click", () => {
      loadHealth()
        .then(() => FH.toast("Health refreshed", "good"))
        .catch((error) => FH.toast(error.message, "bad"));
    });
  }

  bindFactors();
  bindSignals();
  bindHealth();
})();
