/* FinHound dashboard: shared state, API access, navigation, overview and market data. */
"use strict";

(function () {
  const { fmt, esc } = FH;

  const TABS = {
    overview: ["Overview", "Pipeline health, coverage, signals, and the latest research run."],
    market: ["Market data", "Cleaned OHLCV, realized statistics, and price history."],
    factors: ["Factor research", "Cross-sectional factor exposures and long/short leaders."],
    signals: ["Signal research", "Composite rankings, dispersion, and universe coverage."],
    forecast: ["Multi-horizon forecast", "1-day to 3-month up/down signals from Qlib and native factor engines."],
    strategies: ["Strategy studio", "Build, validate, preview, and run reproducible strategies."],
    backtest: ["Strategy backtest", "Run portfolio simulations, inspect metrics, and revisit prior runs."],
    health: ["Data health", "Quality controls, missing sessions, ingestion, and pipeline runs."],
  };

  const state = {
    apiKey: localStorage.getItem("finhound.apiKey") || "",
    tab: "overview",
    overview: null,
    universe: [],
    signals: [],
    runs: [],
    qcSummary: [],
    market: { symbol: null, range: "3Y", rawBySymbol: {}, rows: [] },
    factor: { symbol: null, name: "momentum_12_1", rows: [], summary: null },
    signal: { name: null, limit: 10, date: null, snapshot: null },
    forecast: { horizon: "1m", date: null, confidence: 0, snapshot: null, meta: null, symbol: null, history: null },
    strategy: { items: [], selected: null, step: 1, editing: null, validation: null, preview: null },
    runId: null,
  };

  FH.state = state;
  FH.views = {};

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function toast(message, kind = "") {
    const node = document.getElementById("toast");
    if (!node) return;
    node.textContent = message;
    node.className = `toast show ${kind}`;
    window.clearTimeout(node._timer);
    node._timer = window.setTimeout(() => {
      node.className = `toast ${kind}`;
    }, 4200);
  }
  FH.toast = toast;

  function openSettings() {
    const modal = document.getElementById("settings-modal");
    const input = document.getElementById("api-key-input");
    if (!modal || !input) return;
    input.value = state.apiKey;
    modal.classList.remove("hidden");
    input.focus();
  }
  FH.openSettings = openSettings;

  function closeSettings() {
    document.getElementById("settings-modal")?.classList.add("hidden");
  }

  async function api(path, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    if (state.apiKey) headers["X-API-Key"] = state.apiKey;
    const response = await fetch(path, Object.assign({}, options, { headers }));
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        if (payload && payload.detail) {
          detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
        }
      } catch {
        /* response was not JSON */
      }
      if (response.status === 401) {
        toast("API key required. Add it under API key / settings.", "bad");
        openSettings();
      }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) return null;
    return response.json();
  }
  FH.api = api;

  FH.pollJob = async function pollJob(jobId, callbacks = {}) {
    for (let attempt = 0; attempt < 900; attempt += 1) {
      const job = await api(`/jobs/${encodeURIComponent(jobId)}`);
      if (callbacks.onUpdate) callbacks.onUpdate(job);
      if (job.status === "completed") {
        if (callbacks.onDone) callbacks.onDone(job);
        return job;
      }
      if (job.status === "failed") {
        if (callbacks.onError) callbacks.onError(job);
        return job;
      }
      await sleep(1200);
    }
    throw new Error("job timed out after 18 minutes");
  };

  function setConnection(online) {
    const dot = document.getElementById("conn-dot");
    const text = document.getElementById("conn-text");
    if (!dot || !text) return;
    dot.className = `dot ${online ? "online" : "offline"}`;
    text.textContent = online ? "API connected" : "API unreachable";
  }

  function setRangePill() {
    const pill = document.getElementById("data-range");
    if (!pill) return;
    const data = state.overview?.data;
    if (!data || !data.first_date) {
      pill.textContent = "No data loaded";
      return;
    }
    pill.innerHTML = `<strong>${esc(fmt.date(data.first_date))}</strong> → <strong>${esc(fmt.date(data.last_date))}</strong> · ${fmt.int(data.symbols)} symbols`;
  }

  function fillSelect(element, values, selected, labelFn) {
    if (!element) return;
    const list = values.map((value) => {
      const item = typeof value === "object" ? value : { value, label: labelFn ? labelFn(value) : value };
      return { value: item.value, label: item.label };
    });
    element.innerHTML = list
      .map((item) => `<option value="${esc(item.value)}"${String(item.value) === String(selected) ? " selected" : ""}>${esc(item.label)}</option>`)
      .join("");
  }
  FH.fillSelect = fillSelect;

  function clearLoading(scopeId) {
    const scope = document.getElementById(scopeId) || document;
    scope.querySelectorAll(".loading").forEach((node) => node.classList.remove("loading"));
  }
  FH.clearLoading = clearLoading;

  async function loadShared() {
    const results = await Promise.allSettled([api("/universe"), api("/signals"), api("/overview")]);
    if (results[0].status === "fulfilled") {
      state.universe = results[0].value.map((row) => row.symbol).filter(Boolean);
    }
    if (results[1].status === "fulfilled") {
      state.signals = results[1].value || [];
    }
    if (results[2].status === "fulfilled") {
      state.overview = results[2].value;
    }

    if (!state.market.symbol) {
      state.market.symbol = state.universe.find((symbol) => symbol !== "SPY") || state.universe[0] || null;
    }
    if (!state.factor.symbol) {
      state.factor.symbol = state.universe.find((symbol) => symbol !== "SPY") || state.universe[0] || null;
    }
    const defaultSignal = state.overview?.default_signal;
    if (!state.signal.name) {
      state.signal.name = state.signals.includes(defaultSignal) ? defaultSignal : state.signals[0] || null;
    }

    fillSelect(document.getElementById("market-symbol"), state.universe, state.market.symbol);
    fillSelect(document.getElementById("factor-symbol"), state.universe, state.factor.symbol);
    fillSelect(document.getElementById("signal-name"), state.signals, state.signal.name);
    fillSelect(document.getElementById("bt-signal"), state.signals, state.signal.name);
    setRangePill();
  }
  FH.loadShared = loadShared;

  function activateTab(name) {
    if (!TABS[name]) return;
    state.tab = name;
    document.querySelectorAll(".nav-item").forEach((button) => {
      button.classList.toggle("active", button.dataset.tab === name);
    });
    document.querySelectorAll(".tab").forEach((section) => {
      section.classList.toggle("active", section.id === `tab-${name}`);
    });
    const [title, subtitle] = TABS[name];
    const titleNode = document.getElementById("page-title");
    const subtitleNode = document.getElementById("page-subtitle");
    if (titleNode) titleNode.textContent = title;
    if (subtitleNode) subtitleNode.textContent = subtitle;
    const loader = FH.views[name];
    if (typeof loader === "function") {
      Promise.resolve()
        .then(() => loader())
        .then(() => clearLoading(`tab-${name}`))
        .catch((error) => toast(`${title}: ${error.message}`, "bad"));
    }
  }
  FH.activateTab = activateTab;

  function refreshActive() {
    state.market.rawBySymbol = {};
    return Promise.resolve()
      .then(() => loadShared())
      .then(() => (FH.views[state.tab] ? FH.views[state.tab]() : null))
      .then(() => clearLoading(`tab-${state.tab}`));
  }

  /* -------------------------------- Overview -------------------------------- */

  async function loadOverview() {
    const [overview, qc] = await Promise.all([api("/overview"), api("/qc/summary")]);
    state.overview = overview;
    state.qcSummary = qc;
    setRangePill();
    renderOverviewKpis(overview);
    renderLatestBacktest(overview);
    renderPipeline(overview);
    renderCoverage(qc, overview);
  }
  FH.views.overview = loadOverview;

  function renderOverviewKpis(overview) {
    const node = document.getElementById("overview-kpis");
    if (!node) return;
    const data = overview.data || {};
    const qc = overview.qc || {};
    const factors = overview.factors || {};
    const latest = overview.backtests?.latest?.metrics?.full || {};
    const backtestCount = overview.backtests?.count || 0;
    node.innerHTML = [
      FH.kpi({
        label: "Universe",
        value: fmt.int(data.symbols || overview.universe?.total),
        sub: `${fmt.int(overview.universe?.tradable)} tradable · ${fmt.int(data.rows)} rows`,
      }),
      FH.kpi({
        label: "Coverage",
        value:
          data.first_date && data.last_date
            ? `${fmt.int(Math.max(0, Math.round((new Date(data.last_date) - new Date(data.first_date)) / 31557600000)))}y`
            : "—",
        sub: `${fmt.date(data.first_date)} → ${fmt.date(data.last_date)}`,
      }),
      FH.kpi({
        label: "Factors",
        value: fmt.int(factors.rows),
        sub: `${fmt.int(factors.factors)} factors × ${fmt.int(factors.symbols)} symbols`,
      }),
      FH.kpi({
        label: "QC signal",
        value: fmt.int(qc.errors) === "0" ? "Clean" : `${fmt.int(qc.errors)} errors`,
        sub: `${fmt.int(qc.warnings)} warnings across ${fmt.int(qc.symbols)} symbols`,
        tone: Number(qc.errors) > 0 ? "bad" : Number(qc.warnings) > 0 ? "warn" : "good",
      }),
      FH.kpi({
        label: "Latest Sharpe",
        value: latest.Sharpe === undefined ? "—" : fmt.num(latest.Sharpe, 2),
        sub: latest.CAGR === undefined ? "no backtest yet" : `${fmt.pct(latest.CAGR)} CAGR · ${fmt.pct(latest.max_drawdown)} max DD`,
        tone: latest.Sharpe > 0 ? "good" : latest.Sharpe < 0 ? "bad" : "",
      }),
      FH.kpi({
        label: "Backtests",
        value: fmt.int(backtestCount),
        sub: `${overview.pipeline?.last?.status ? `last pipeline ${overview.pipeline.last.status}` : "no pipeline run"}`,
      }),
    ].join("");
  }

  function renderLatestBacktest(overview) {
    const node = document.getElementById("overview-latest-backtest");
    if (!node) return;
    const latest = overview.backtests?.latest;
    if (!latest) {
      node.innerHTML = FH.empty("No backtests yet", "Configure a run on the Backtest tab or rebuild analytics.");
      return;
    }
    const metrics = latest.metrics?.full || {};
    const config = latest.config || {};
    const chips = [
      ["Signal", config.signal_name || state.overview?.default_signal || "—"],
      ["Mode", config.mode || "long_only"],
      ["Rebalance", config.rebalance_frequency || "weekly"],
      ["Created", fmt.datetime(latest.created_at)],
    ]
      .map(([label, value]) => `<div class="metric-chip">${esc(label)}<strong>${esc(value)}</strong></div>`)
      .join("");
    const metricChips = [
      ["CAGR", fmt.pct(metrics.CAGR)],
      ["Sharpe", fmt.num(metrics.Sharpe, 2)],
      ["Sortino", fmt.num(metrics.Sortino, 2)],
      ["Vol", fmt.pct(metrics.annualized_volatility)],
      ["Max DD", fmt.pct(metrics.max_drawdown)],
      ["Hit rate", fmt.pct(metrics.hit_rate)],
    ]
      .map(([label, value]) => `<div class="metric-chip">${label}<strong>${esc(value)}</strong></div>`)
      .join("");
    node.innerHTML = `
      <div class="mono faint" style="margin-bottom:10px">run ${esc(latest.run_id)}</div>
      <div class="metric-strip">${chips}</div>
      <div class="metric-strip">${metricChips}</div>`;
    const button = document.getElementById("overview-open-backtest");
    if (button) {
      button.onclick = () => {
        state.runId = latest.run_id;
        activateTab("backtest");
      };
    }
  }

  function renderPipeline(overview) {
    const node = document.getElementById("overview-pipeline");
    if (!node) return;
    const pipeline = overview.pipeline?.last;
    const ingest = overview.ingest?.last;
    const signals = overview.signals || [];
    const pipelineRows = pipeline
      ? [
          ["Status", FH.badge(pipeline.status || "unknown", pipeline.status === "ok" ? "good" : pipeline.status === "failed" ? "bad" : "warn")],
          ["Started", esc(fmt.datetime(pipeline.started_at))],
          ["Finished", esc(fmt.datetime(pipeline.finished_at))],
          ["File", `<span class="mono faint">${esc(pipeline.file || "—")}</span>`],
        ]
      : [["Status", FH.badge("no runs", "neutral")]];
    const ingestRows = ingest
      ? [
          ["Ingest date", esc(fmt.date(ingest.started_at))],
          ["Symbols", esc(fmt.int(ingest.symbols))],
          ["Rows", esc(fmt.int(ingest.rows))],
          ["Failures", ingest.failures ? FH.badge(`${ingest.failures} failures`, "bad") : FH.badge("none", "good")],
        ]
      : [["Ingest", FH.badge("no runs", "neutral")]];
    const signalRows = signals.length
      ? signals.map(
          (signal) =>
            `<div class="list-row"><div class="lhs"><strong class="mono">${esc(signal.name)}</strong>${
              signal.name === overview.default_signal ? FH.badge("default", "accent") : ""
            }</div><span class="faint mono">${fmt.int(signal.rows)} rows · ${fmt.int(signal.symbols)} symbols</span></div>`
        )
      : [FH.empty("No signal files")];
    node.innerHTML = `
      <div class="def-list">
        <div><dt>Pipeline</dt><dd>${pipelineRows.map(([k, v]) => `<div style="display:flex;justify-content:space-between;gap:10px;margin:3px 0"><span class="faint">${k}</span><span>${v}</span></div>`).join("")}</dd></div>
        <div><dt>Ingestion</dt><dd>${ingestRows.map(([k, v]) => `<div style="display:flex;justify-content:space-between;gap:10px;margin:3px 0"><span class="faint">${k}</span><span>${v}</span></div>`).join("")}</dd></div>
      </div>
      <h3>Signal artifacts</h3>
      <div class="list">${signalRows.join("")}</div>`;
  }

  function renderCoverage(qc, overview) {
    const node = document.getElementById("overview-coverage");
    const note = document.getElementById("overview-coverage-note");
    if (!node) return;
    if (note) note.textContent = `${qc.length} symbols · ${fmt.date(overview.data?.first_date)} → ${fmt.date(overview.data?.last_date)}`;
    if (!qc.length) {
      node.innerHTML = FH.empty("No quality summary", "Run the pipeline to populate qc_summary.");
      return;
    }
    const rows = qc
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

  /* ------------------------------- Market data ------------------------------ */

  async function loadMarket() {
    const symbol = state.market.symbol;
    if (!symbol) {
      renderMarket();
      return;
    }
    if (!state.market.rawBySymbol[symbol]) {
      state.market.rawBySymbol[symbol] = await api(`/prices/${encodeURIComponent(symbol)}`);
    }
    renderMarket();
  }
  FH.views.market = loadMarket;

  function filterMarketRows() {
    const all = state.market.rawBySymbol[state.market.symbol] || [];
    if (!all.length) return [];
    if (state.market.range === "MAX") return all;
    const years = Number.parseInt(state.market.range, 10);
    const latest = new Date(all[all.length - 1].date);
    const start = new Date(latest);
    start.setFullYear(start.getFullYear() - years);
    const startIso = start.toISOString().slice(0, 10);
    return all.filter((row) => String(row.date).slice(0, 10) >= startIso);
  }

  function renderMarket() {
    const rows = filterMarketRows();
    const symbol = state.market.symbol;
    state.market.rows = rows;
    const asof = document.getElementById("market-asof");
    const label = document.getElementById("market-price-label");
    if (label) label.textContent = rows.length ? `${symbol} · ${fmt.date(rows[0].date)} → ${fmt.date(rows[rows.length - 1].date)}` : symbol || "";
    if (!rows.length) {
      if (asof) asof.textContent = "No data";
      document.getElementById("market-kpis").innerHTML = FH.empty("No price data", "Run ingestion for this symbol.");
      FH.chart("market-price-chart")?.clear();
      FH.chart("market-volume-chart")?.clear();
      document.getElementById("market-table").innerHTML = FH.empty("No sessions");
      return;
    }

    const closes = rows.map((row) => FH.toNumber(row.close)).filter((value) => value !== null);
    const volumes = rows.map((row) => FH.toNumber(row.volume)).filter((value) => value !== null);
    const last = closes[closes.length - 1];
    const previous = closes.length > 1 ? closes[closes.length - 2] : null;
    const first = closes[0];
    const daily = closes.slice(1).map((value, index) => value / closes[index] - 1).filter((value) => Number.isFinite(value));
    const mean = daily.reduce((sum, value) => sum + value, 0) / (daily.length || 1);
    const variance = daily.length > 1 ? daily.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (daily.length - 1) : 0;
    const volatility = Math.sqrt(variance) * Math.sqrt(252);
    let peak = -Infinity;
    let maxDrawdown = 0;
    closes.forEach((value) => {
      peak = Math.max(peak, value);
      maxDrawdown = Math.min(maxDrawdown, value / peak - 1);
    });
    const avgVolume = volumes.length ? volumes.reduce((sum, value) => sum + value, 0) / volumes.length : null;
    const periodReturn = first ? last / first - 1 : null;
    const dayChange = previous ? last / previous - 1 : null;

    if (asof) asof.innerHTML = `<strong>${esc(symbol)}</strong> · ${esc(fmt.date(rows[rows.length - 1].date))}`;
    document.getElementById("market-kpis").innerHTML = [
      FH.kpi({
        label: "Last close",
        value: fmt.money(last),
        sub: dayChange === null ? "" : `<span class="${FH.tone(dayChange)}">${fmt.signedPct(dayChange)}</span> today`,
      }),
      FH.kpi({
        label: "Period return",
        value: `<span class="${FH.tone(periodReturn)}">${fmt.signedPct(periodReturn)}</span>`,
        sub: `${rows.length} sessions · ${state.market.range}`,
        tone: periodReturn > 0 ? "good" : periodReturn < 0 ? "bad" : "",
      }),
      FH.kpi({
        label: "Realized vol",
        value: fmt.pct(volatility),
        sub: "annualized, daily returns",
      }),
      FH.kpi({
        label: "Max drawdown",
        value: `<span class="down">${fmt.pct(maxDrawdown)}</span>`,
        sub: "peak to trough, close basis",
        tone: "bad",
      }),
      FH.kpi({
        label: "Avg volume",
        value: fmt.compact(avgVolume),
        sub: "shares per session",
      }),
    ].join("");

    const labels = rows.map((row) => row.date);
    FH.chart("market-price-chart")?.setData({
      type: "line",
      labels,
      series: [{ name: symbol, color: "#2dd4bf", values: rows.map((row) => FH.toNumber(row.close)), area: true }],
      yFormat: (value) => `$${fmt.num(value, value >= 100 ? 0 : 2)}`,
      valueFormat: (value) => fmt.money(value),
    });

    const volumeColors = rows.map((row, index) => {
      const close = FH.toNumber(row.close);
      const prior = index ? FH.toNumber(rows[index - 1].close) : null;
      const up = prior === null || close === null ? true : close >= prior;
      return up ? "rgba(52,211,153,0.55)" : "rgba(248,113,113,0.55)";
    });
    FH.chart("market-volume-chart")?.setData({
      type: "bars",
      labels,
      series: [{ name: "Volume", values: rows.map((row) => FH.toNumber(row.volume)) }],
      barColors: (value, index) => volumeColors[index] || "rgba(148,163,184,0.5)",
      yFormat: (value) => fmt.compact(value),
      valueFormat: (value) => fmt.int(value),
      maxBarWidth: 6,
    });

    const recent = rows.slice(-30).reverse();
    const body = recent
      .map((row, index) => {
        const previousRow = recent[index + 1];
        const change = previousRow ? FH.toNumber(row.close) / FH.toNumber(previousRow.close) - 1 : null;
        return `<tr>
          <td class="mono">${fmt.date(row.date)}</td>
          <td class="num">${fmt.money(row.open)}</td>
          <td class="num">${fmt.money(row.high)}</td>
          <td class="num">${fmt.money(row.low)}</td>
          <td class="num">${fmt.money(row.close)}</td>
          <td class="num ${FH.tone(change)}">${change === null ? "—" : fmt.signedPct(change)}</td>
          <td class="num">${fmt.compact(row.volume)}</td>
        </tr>`;
      })
      .join("");
    document.getElementById("market-table").innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Date</th><th class="num">Open</th><th class="num">High</th><th class="num">Low</th><th class="num">Close</th><th class="num">Change</th><th class="num">Volume</th></tr></thead>
      <tbody>${body}</tbody>
    </table></div>`;
  }

  /* --------------------------------- Wiring --------------------------------- */

  function bindNavigation() {
    document.querySelectorAll(".nav-item").forEach((button) => {
      button.addEventListener("click", () => activateTab(button.dataset.tab));
    });
  }

  function bindSettings() {
    document.getElementById("settings-btn")?.addEventListener("click", openSettings);
    document.getElementById("api-key-cancel")?.addEventListener("click", closeSettings);
    document.getElementById("settings-modal")?.addEventListener("click", (event) => {
      if (event.target.id === "settings-modal") closeSettings();
    });
    document.getElementById("api-key-clear")?.addEventListener("click", () => {
      state.apiKey = "";
      localStorage.removeItem("finhound.apiKey");
      document.getElementById("api-key-input").value = "";
      closeSettings();
      toast("API key cleared");
    });
    document.getElementById("api-key-save")?.addEventListener("click", () => {
      state.apiKey = document.getElementById("api-key-input").value.trim();
      if (state.apiKey) localStorage.setItem("finhound.apiKey", state.apiKey);
      else localStorage.removeItem("finhound.apiKey");
      closeSettings();
      toast(state.apiKey ? "API key saved" : "No API key set");
      refreshActive().catch((error) => toast(error.message, "bad"));
    });
    document.getElementById("refresh-btn")?.addEventListener("click", (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Refreshing…";
      refreshActive()
        .then(() => toast("Dashboard refreshed", "good"))
        .catch((error) => toast(error.message, "bad"))
        .finally(() => {
          button.disabled = false;
          button.textContent = "Refresh";
        });
    });
    document.getElementById("pipeline-btn")?.addEventListener("click", (event) => {
      const button = event.currentTarget;
      const run = async () => {
        button.disabled = true;
        button.textContent = "Rebuilding…";
        try {
          const { job_id } = await api("/pipeline", { method: "POST" });
          await FH.pollJob(job_id, {
            onUpdate: (job) => {
              button.textContent = job.status === "running" ? "Rebuilding…" : "Queued…";
            },
            onDone: (job) => {
              if (job.run_id) state.runId = job.run_id;
              toast("Pipeline completed. Refreshing data.", "good");
            },
            onError: (job) => toast(`Pipeline failed: ${job.error || "unknown error"}`, "bad"),
          });
          await refreshActive();
        } catch (error) {
          toast(`Pipeline: ${error.message}`, "bad");
        } finally {
          button.disabled = false;
          button.textContent = "Rebuild analytics";
        }
      };
      if (window.confirm("Run QC, factors, signals, and a fresh backtest?")) run();
    });
  }

  function bindMarket() {
    document.getElementById("market-symbol")?.addEventListener("change", (event) => {
      state.market.symbol = event.target.value;
      loadMarket().catch((error) => toast(error.message, "bad"));
    });
    document.getElementById("market-range")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-range]");
      if (!button) return;
      state.market.range = button.dataset.range;
      document.querySelectorAll("#market-range button").forEach((node) => node.classList.toggle("active", node === button));
      renderMarket();
    });
  }

  async function checkConnection() {
    try {
      await api("/health");
      setConnection(true);
    } catch {
      setConnection(false);
    }
  }

  async function init() {
    bindNavigation();
    bindSettings();
    bindMarket();
    document.querySelectorAll("#market-range button").forEach((button) => {
      button.classList.toggle("active", button.dataset.range === state.market.range);
    });
    await checkConnection();
    try {
      await loadShared();
    } catch (error) {
      toast(error.message, "bad");
    }
    if (state.overview && !(state.overview.data && state.overview.data.rows)) {
      toast("No cleaned price data yet — run `make ingest` and then Rebuild analytics.", "bad");
    }
    activateTab(state.tab);
  }

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
