/* FinHound dashboard: backtest runner, results, and run history. */
"use strict";

(function () {
  const { fmt, esc } = FH;
  const state = FH.state;

  function parseJson(value, fallback = {}) {
    if (value === null || value === undefined) return fallback;
    if (typeof value === "object") return value;
    try {
      return JSON.parse(value);
    } catch {
      return fallback;
    }
  }

  function ensureBacktestControls() {
    if (!state.signal.name) state.signal.name = state.signals[0] || null;
    FH.fillSelect(document.getElementById("bt-signal"), state.signals, state.signal.name);
  }

  async function loadBacktest() {
    ensureBacktestControls();
    await loadBacktestRuns();
    if (!state.runId && state.runs.length) state.runId = state.runs[0].run_id;
    if (state.runId) await loadBacktestRun(state.runId);
    else renderNoRun();
  }
  FH.views.backtest = loadBacktest;

  function renderNoRun() {
    const meta = document.getElementById("backtest-run-meta");
    if (meta) meta.textContent = "No run loaded";
    const kpis = document.getElementById("backtest-kpis");
    if (kpis) kpis.innerHTML = FH.empty("No backtests yet", "Configure a run above and press Run backtest.");
    ["equity-chart", "drawdown-chart", "exposure-chart", "ic-chart"].forEach((id) => FH.chart(id)?.clear());
    ["backtest-metrics", "backtest-positions", "backtest-repro", "ic-summary"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.innerHTML = "";
    });
  }

  async function loadBacktestRuns() {
    const runs = await FH.api("/backtests");
    state.runs = runs || [];
    renderRunHistory(state.runs);
    return state.runs;
  }

  function renderRunHistory(runs) {
    const node = document.getElementById("backtest-runs");
    if (!node) return;
    if (!runs.length) {
      node.innerHTML = FH.empty("No runs yet", "Configure a backtest above and press Run backtest.");
      return;
    }
    const rows = runs
      .map((run) => {
        const config = parseJson(run.config, {});
        const metrics = parseJson(run.metrics, {}).full || {};
        const selected = run.run_id === state.runId;
        return `<tr style="${selected ? "background:rgba(45,212,191,0.06)" : ""}">
          <td class="mono">${fmt.datetime(run.created_at)}</td>
          <td class="mono faint">${esc(fmt.shortId(run.run_id, 12))}</td>
          <td>${esc(config.signal_name || "—")}</td>
          <td>${esc(config.mode || "long_only")}</td>
          <td>${esc(config.rebalance_frequency || "—")}</td>
          <td class="num ${FH.tone(metrics.Sharpe)}">${fmt.num(metrics.Sharpe, 2)}</td>
          <td class="num ${FH.tone(metrics.CAGR)}">${fmt.pct(metrics.CAGR)}</td>
          <td class="num down">${fmt.pct(metrics.max_drawdown)}</td>
          <td><button class="ghost small" data-load-run="${esc(run.run_id)}">Load</button></td>
        </tr>`;
      })
      .join("");
    node.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Created</th><th>Run</th><th>Signal</th><th>Mode</th><th>Freq</th><th class="num">Sharpe</th><th class="num">CAGR</th><th class="num">Max DD</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  }

  async function loadBacktestRun(runId) {
    const detail = await FH.api(`/backtests/${encodeURIComponent(runId)}`);
    const [equity, positions, research] = await Promise.all([
      FH.api(`/backtests/${encodeURIComponent(runId)}/equity`).catch(() => []),
      FH.api(`/backtests/${encodeURIComponent(runId)}/positions`).catch(() => ({ positions: [] })),
      FH.api(`/backtests/${encodeURIComponent(runId)}/research?kind=ic&horizon=5`).catch(() => []),
    ]);
    state.runId = runId;
    state.run = detail;
    renderBacktest(detail, equity, positions, research);
    renderRunHistory(state.runs);
  }

  function metricValue(metrics, key, kind) {
    const value = metrics?.[key];
    if (value === null || value === undefined) return "—";
    if (kind === "pct") return fmt.pct(value);
    return fmt.num(value, 2);
  }

  function renderBacktest(detail, equity, positions, research) {
    const metrics = detail.metrics || {};
    const full = metrics.full || {};
    const config = detail.config || {};
    const meta = document.getElementById("backtest-run-meta");
    if (meta) {
      meta.innerHTML = `run <span class="mono">${esc(fmt.shortId(detail.run_id, 12))}</span> · ${esc(fmt.datetime(detail.created_at))}`;
    }

    document.getElementById("backtest-kpis").innerHTML = [
      FH.kpi({ label: "CAGR", value: metricValue(full, "CAGR", "pct"), tone: FH.tone(full.CAGR), sub: "full period" }),
      FH.kpi({ label: "Sharpe", value: metricValue(full, "Sharpe"), tone: FH.tone(full.Sharpe), sub: "annualized" }),
      FH.kpi({ label: "Sortino", value: metricValue(full, "Sortino"), tone: FH.tone(full.Sortino), sub: "downside adjusted" }),
      FH.kpi({ label: "Max drawdown", value: metricValue(full, "max_drawdown", "pct"), tone: "bad", sub: "peak to trough" }),
      FH.kpi({ label: "Volatility", value: metricValue(full, "annualized_volatility", "pct"), sub: "annualized" }),
      FH.kpi({ label: "Hit rate", value: metricValue(full, "hit_rate", "pct"), sub: "positive sessions" }),
      FH.kpi({ label: "Turnover", value: `${fmt.num(metrics.annual_turnover, 1)}x`, sub: "annualized, one-way" }),
      FH.kpi({ label: "Holdings", value: fmt.num(metrics.average_holdings, 1), sub: `gross ${fmt.pct(metrics.average_gross_exposure)}` }),
    ].join("");

    renderEquityChart(equity, config);
    renderDrawdownChart(equity);
    renderExposureChart(equity);
    renderMetricsTable(metrics);
    renderIc(metrics, research);
    renderPositions(positions);
    renderRepro(detail);
  }

  function renderEquityChart(equity, config) {
    const chart = FH.chart("equity-chart");
    if (!chart) return;
    if (!equity || !equity.length) {
      chart.clear();
      return;
    }
    const labels = equity.map((row) => row.date);
    chart.setData({
      type: "line",
      labels,
      series: [
        { name: "Strategy", color: "#2dd4bf", values: equity.map((row) => FH.toNumber(row.strategy)), width: 2.1 },
        { name: "SPY", color: "#818cf8", values: equity.map((row) => FH.toNumber(row.SPY)), width: 1.3, dash: [4, 3] },
        {
          name: "Equal weight",
          color: "#60a5fa",
          values: equity.map((row) => FH.toNumber(row.equal_weight)),
          width: 1.3,
          dash: [2, 3],
        },
      ],
      yFormat: (value) => `${fmt.num(value, 2)}x`,
      valueFormat: (value) => `${fmt.num(value, 3)}x`,
    });
  }

  function renderDrawdownChart(equity) {
    const chart = FH.chart("drawdown-chart");
    if (!chart) return;
    if (!equity || !equity.length) {
      chart.clear();
      return;
    }
    chart.setData({
      type: "line",
      labels: equity.map((row) => row.date),
      series: [{ name: "Drawdown", color: "#f87171", values: equity.map((row) => FH.toNumber(row.drawdown)), area: true }],
      zeroLine: true,
      yFormat: (value) => fmt.pct(value, 0),
      valueFormat: (value) => fmt.pct(value),
    });
  }

  function renderExposureChart(equity) {
    const chart = FH.chart("exposure-chart");
    if (!chart) return;
    if (!equity || !equity.length) {
      chart.clear();
      return;
    }
    chart.setData({
      type: "line",
      labels: equity.map((row) => row.date),
      series: [{ name: "Gross exposure", color: "#fbbf24", values: equity.map((row) => FH.toNumber(row.gross_exposure)), area: true }],
      includeZero: true,
      yMax: 1.05,
      yFormat: (value) => fmt.pct(value, 0),
      valueFormat: (value) => fmt.pct(value),
    });
  }

  function renderMetricsTable(metrics) {
    const node = document.getElementById("backtest-metrics");
    if (!node) return;
    const rows = [
      ["CAGR", "CAGR", "pct"],
      ["Annualized volatility", "annualized_volatility", "pct"],
      ["Sharpe", "Sharpe", "num"],
      ["Sortino", "Sortino", "num"],
      ["Max drawdown", "max_drawdown", "pct"],
      ["Calmar", "Calmar", "num"],
      ["Hit rate", "hit_rate", "pct"],
    ];
    const body = rows
      .map(
        ([label, key, kind]) => `<tr>
          <td>${label}</td>
          <td class="num">${metricValue(metrics.full, key, kind)}</td>
          <td class="num">${metricValue(metrics.in_sample, key, kind)}</td>
          <td class="num">${metricValue(metrics.out_of_sample, key, kind)}</td>
        </tr>`
      )
      .join("");
    node.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Metric</th><th class="num">Full</th><th class="num">In sample</th><th class="num">Out of sample</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
  }

  function renderIc(metrics, research) {
    const chart = FH.chart("ic-chart");
    if (chart) {
      if (!research || research.length < 2) {
        chart.clear();
      } else {
        chart.setData({
          type: "line",
          labels: research.map((row) => row.date),
          series: [{ name: "5d IC", color: "#818cf8", values: research.map((row) => FH.toNumber(row.value)), width: 1.4 }],
          zeroLine: true,
          yFormat: (value) => fmt.num(value, 2),
          valueFormat: (value) => fmt.num(value, 3),
        });
      }
    }
    const node = document.getElementById("ic-summary");
    if (!node) return;
    const ic = metrics.ic || {};
    const rankIc = metrics.rank_ic || {};
    const icIr = metrics.ic_ir || {};
    const tStat = metrics.t_stat || {};
    const spread = metrics.decile_spread || {};
    const horizons = Object.keys(ic).sort((a, b) => Number(a) - Number(b));
    if (!horizons.length) {
      node.innerHTML = FH.empty("No diagnostics", "Signal diagnostics were not recorded for this run.");
      return;
    }
    const chips = [];
    horizons.forEach((horizon) => {
      chips.push([`IC ${horizon}d`, fmt.num(ic[horizon], 4)]);
      chips.push([`Rank IC ${horizon}d`, fmt.num(rankIc[horizon], 4)]);
      chips.push([`IC IR ${horizon}d`, fmt.num(icIr[horizon], 3)]);
      chips.push([`t-stat ${horizon}d`, fmt.num(tStat[horizon], 2)]);
      chips.push([`Decile ${horizon}d`, fmt.num(spread[horizon], 4)]);
    });
    node.innerHTML = `<div class="metric-strip">${chips
      .map(([label, value]) => `<div class="metric-chip">${esc(label)}<strong>${esc(value)}</strong></div>`)
      .join("")}</div>`;
  }

  function renderPositions(payload) {
    const node = document.getElementById("backtest-positions");
    if (!node) return;
    const positions = payload?.positions || [];
    if (!positions.length) {
      node.innerHTML = FH.empty("No open positions", "The run ended with no active weights.");
      return;
    }
    const maxWeight = Math.max(...positions.map((row) => Math.abs(FH.toNumber(row.weight) || 0)), 0.0001);
    const rows = positions
      .map(
        (row) => `<tr>
          <td class="symbol">${esc(row.symbol)}</td>
          <td class="num ${FH.tone(row.weight)}">${fmt.pct(row.weight)}</td>
          <td><div class="bar-track" style="width:140px"><div class="bar-fill ${row.weight < 0 ? "neg" : ""}" style="width:${(Math.abs(row.weight) / maxWeight) * 100}%"></div></div></td>
        </tr>`
      )
      .join("");
    node.innerHTML = `<div class="panel-note" style="margin-bottom:8px">as of ${esc(fmt.date(payload.date))}</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Symbol</th><th class="num">Weight</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`;
  }

  function renderRepro(detail) {
    const node = document.getElementById("backtest-repro");
    if (!node) return;
    const items = [
      ["Run ID", detail.run_id],
      ["Created", fmt.datetime(detail.created_at)],
      ["Config hash", detail.config_hash],
      ["Git SHA", detail.git_sha],
      ["Data snapshot", detail.data_snapshot],
      ["Libraries", fmt.json(detail.library_versions, 180)],
      ["Config", fmt.json(detail.config, 220)],
    ];
    node.innerHTML = `<dl class="def-list">${items
      .map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value ?? "—")}</dd></div>`)
      .join("")}</dl>`;
  }

  function numberValue(id, fallback) {
    const node = document.getElementById(id);
    const value = node ? Number(node.value) : Number.NaN;
    return Number.isFinite(value) ? value : fallback;
  }

  function dateValue(id) {
    const value = document.getElementById(id)?.value;
    return value ? value : undefined;
  }

  function buildPayload() {
    const payload = {
      signal_name: document.getElementById("bt-signal")?.value || state.signal.name,
      mode: document.getElementById("bt-mode")?.value || "long_only",
      rebalance_frequency: document.getElementById("bt-frequency")?.value || "weekly",
      quantile: numberValue("bt-quantile", 0.1),
      max_position_weight: numberValue("bt-max-weight", 0.25),
      commission_bps: numberValue("bt-commission", 1),
      slippage_bps: numberValue("bt-slippage", 2),
      borrow_bps: numberValue("bt-borrow", 50),
    };
    const start = dateValue("bt-start");
    const end = dateValue("bt-end");
    const split = dateValue("bt-split");
    if (start) payload.start_date = start;
    if (end) payload.end_date = end;
    if (split) payload.split_date = split;
    return payload;
  }

  function setJobStatus(text, kind = "") {
    const node = document.getElementById("backtest-job");
    if (!node) return;
    node.className = `job-status ${kind}`;
    node.innerHTML = kind === "running" ? `<span class="spinner"></span>${esc(text)}` : esc(text);
  }

  async function submitBacktest(event) {
    event.preventDefault();
    const button = document.getElementById("bt-submit");
    const payload = buildPayload();
    if (payload.start_date && payload.end_date && payload.start_date > payload.end_date) {
      setJobStatus("Start date must be before the end date.", "failed");
      return;
    }
    if (button) button.disabled = true;
    setJobStatus("Submitting…", "running");
    try {
      const { job_id } = await FH.api("/backtests", { method: "POST", body: JSON.stringify(payload) });
      setJobStatus("Queued…", "running");
      await FH.pollJob(job_id, {
        onUpdate: (job) => {
          if (job.status === "queued") setJobStatus("Queued…", "running");
          if (job.status === "running") setJobStatus("Running portfolio simulation…", "running");
        },
        onDone: (job) => setJobStatus("Completed — loading results.", "completed"),
        onError: (job) => setJobStatus(`Failed: ${job.error || "unknown error"}`, "failed"),
      });
      const job = await FH.api(`/jobs/${job_id}`);
      if (job.status === "completed" && job.run_id) {
        await loadBacktestRuns();
        await loadBacktestRun(job.run_id);
        FH.toast("Backtest completed", "good");
      }
    } catch (error) {
      setJobStatus(`Failed: ${error.message}`, "failed");
      FH.toast(`Backtest: ${error.message}`, "bad");
    } finally {
      if (button) button.disabled = false;
    }
  }

  function bindBacktest() {
    document.getElementById("backtest-form")?.addEventListener("submit", submitBacktest);
    document.getElementById("reload-runs")?.addEventListener("click", () => {
      loadBacktestRuns()
        .then(() => FH.toast("Run history reloaded", "good"))
        .catch((error) => FH.toast(error.message, "bad"));
    });
    document.getElementById("backtest-runs")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-load-run]");
      if (!button) return;
      const runId = button.dataset.loadRun;
      loadBacktestRun(runId)
        .then(() => FH.toast("Run loaded", "good"))
        .catch((error) => FH.toast(error.message, "bad"));
    });
  }

  FH.openRun = function openRun(runId) {
    state.runId = runId;
    FH.activateTab("backtest");
    loadBacktestRun(runId).catch((error) => FH.toast(error.message, "bad"));
  };

  bindBacktest();
})();
