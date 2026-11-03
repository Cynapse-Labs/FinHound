/* FinHound dashboard: multi-horizon direction forecasts. */
"use strict";

(function () {
  const { fmt, esc } = FH;
  const state = FH.state;

  const HORIZONS = [
    { key: "1d", label: "1 day" },
    { key: "1w", label: "1 week" },
    { key: "1m", label: "1 month" },
    { key: "3m", label: "3 months" },
  ];

  function directionBadge(direction) {
    const kind = direction === "up" ? "up" : direction === "down" ? "down" : "neutral";
    const label = direction === "up" ? "UP" : direction === "down" ? "DOWN" : "NEUTRAL";
    return FH.badge(label, kind);
  }

  function forecastRow(row, index) {
    const confidence = FH.toNumber(row.confidence);
    return `<tr data-symbol="${esc(row.symbol)}">
      <td class="mono">${index + 1}</td>
      <td><strong class="mono">${esc(row.symbol)}</strong></td>
      <td>${directionBadge(row.direction)}</td>
      <td class="mono">${fmt.signed(row.score_z, 2)}</td>
      <td class="mono">${fmt.pct(row.prob_up, 1)}</td>
      <td class="mono">${fmt.pct(confidence, 1)}</td>
      <td class="mono ${FH.tone(row.expected_return)}">${fmt.signedPct(row.expected_return, 2)}</td>
      <td class="mono">${fmt.pct(row.hit_rate, 1)}</td>
      <td class="mono">${fmt.money(row.close, 2)}</td>
    </tr>`;
  }

  function boardTable(items) {
    if (!items || !items.length) return FH.empty("No forecasts for this horizon", "Rebuild forecasts or pick another date.");
    return `<div class="table-scroll"><table class="data-table">
      <thead><tr>
        <th>#</th><th>Symbol</th><th>Signal</th><th>Score z</th>
        <th>P(up)</th><th>Confidence</th><th>Expected</th><th>Hit rate</th><th>Close</th>
      </tr></thead>
      <tbody>${items.map(forecastRow).join("")}</tbody>
    </table></div>`;
  }

  function sideList(items) {
    if (!items || !items.length) return FH.empty("No names");
    return items
      .map((row) =>
        FH.listRow({
          rank: row.rank,
          symbol: row.symbol,
          value: `${fmt.pct(row.prob_up, 0)} · ${fmt.signedPct(row.expected_return, 2)}`,
          width: Math.max(4, Math.min(100, (FH.toNumber(row.confidence) || 0) * 100)),
          negative: row.direction === "down",
          meta: `${fmt.pct(row.confidence, 0)} conf`,
        })
      )
      .join("");
  }

  function renderMeta(meta, engine) {
    const node = document.getElementById("forecast-meta");
    if (!node) return;
    const qlib = engine || {};
    const rows = [
      ["Engine", qlib.engine || "native"],
      ["Qlib installed", qlib.available ? `Yes · ${qlib.qlib_version || "unknown version"}` : "No · native fallback"],
      ["Dataset ready", qlib.dataset_ready ? "Yes" : "No"],
      ["Predictions ready", qlib.predictions_ready ? "Yes" : "No"],
      ["Last built", qlib.last_built ? fmt.datetime(qlib.last_built) : "—"],
      ["Features", qlib.features || meta.features || "native factors"],
      ["Artifact rows", fmt.int(meta.rows || meta.predictions)],
      ["Artifacts from", fmt.date(meta.first_date)],
      ["Artifacts to", fmt.date(meta.last_date)],
      ["Symbols", fmt.int(meta.symbols)],
      ["Engines represented", (meta.engines || []).join(", ") || "—"],
      ["Qlib error", qlib.last_error || meta.qlib_error || "—"],
    ];
    node.innerHTML = `<dl class="detail-list">${rows
      .map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`)
      .join("")}</dl>`;
  }

  function renderSnapshot(snapshot) {
    const kpis = document.getElementById("forecast-kpis");
    const up = document.getElementById("forecast-up");
    const down = document.getElementById("forecast-down");
    const table = document.getElementById("forecast-table");
    const note = document.getElementById("forecast-board-note");
    if (!snapshot || !snapshot.available) {
      if (kpis) kpis.innerHTML = "";
      if (up) up.innerHTML = FH.empty("No forecast artifacts", "Run Rebuild forecasts to generate 1D/1W/1M/3M signals.");
      if (down) down.innerHTML = FH.empty("No forecast artifacts");
      if (table) table.innerHTML = FH.empty("No forecast artifacts", "The pipeline builds them after factors and signals.");
      if (note) note.textContent = "no snapshot";
      return;
    }
    if (kpis) {
      kpis.innerHTML = [
        FH.kpi({ label: "Horizon", value: esc(snapshot.horizon_key || ""), sub: `${fmt.date(snapshot.date)} · ${fmt.int(snapshot.count)} names` }),
        FH.kpi({ label: "Up signals", value: fmt.int(snapshot.bullish), sub: `${fmt.int(snapshot.neutral)} neutral`, tone: "up" }),
        FH.kpi({ label: "Down signals", value: fmt.int(snapshot.bearish), sub: `${fmt.int(snapshot.count - snapshot.bullish - snapshot.bearish)} mixed`, tone: "down" }),
        FH.kpi({ label: "Avg confidence", value: fmt.pct(snapshot.avg_confidence, 1), sub: `P(up) ${fmt.pct(snapshot.avg_prob_up, 1)}` }),
        FH.kpi({ label: "Avg hit rate", value: fmt.pct(snapshot.avg_hit_rate, 1), sub: "expanding, out of sample" }),
        FH.kpi({
          label: "Expected return",
          value: fmt.signedPct(snapshot.expected_return, 2),
          sub: "cross-sectional average",
          tone: FH.tone(snapshot.expected_return),
        }),
      ].join("");
    }
    if (up) up.innerHTML = sideList(snapshot.up || []);
    if (down) down.innerHTML = sideList(snapshot.down || []);
    if (table) table.innerHTML = boardTable(snapshot.items || []);
    if (note) note.textContent = `${fmt.int(snapshot.count)} scored names · engine ${snapshot.engine || "—"} · ${fmt.date(snapshot.first_date)} → ${fmt.date(snapshot.last_date)}`;
  }

  async function loadHistory() {
    const select = document.getElementById("fc-symbol");
    const symbol = state.forecast.symbol || select?.value || state.universe[0];
    if (!symbol) return;
    state.forecast.symbol = symbol;
    if (select && select.value !== symbol) select.value = symbol;
    try {
      const payload = await FH.api(
        `/signals/directions/${encodeURIComponent(symbol)}?horizon=${encodeURIComponent(state.forecast.horizon)}&limit=250`
      );
      const items = payload.items || [];
      state.forecast.history = items;
      const chart = FH.chart("forecast-history-chart");
      if (chart) {
        const labels = items.map((row) => row.date);
        chart.setData({
          labels,
          labelFormat: (value) => fmt.date(value),
          series: [
            { name: "Score (z)", values: items.map((row) => FH.toNumber(row.score_z)), color: FH.COLORS[0], area: true },
            { name: "P(up)", values: items.map((row) => FH.toNumber(row.prob_up)), color: FH.COLORS[1] },
          ],
          zeroLine: true,
          valueFormat: (value) => fmt.num(value, 2),
        });
      }
      const note = document.getElementById("forecast-symbol-note");
      if (note) {
        if (!items.length) {
          note.textContent = `No history yet for ${symbol} at this horizon.`;
        } else {
          const latest = items[items.length - 1];
          note.textContent = `${symbol} · ${items.length} sessions · latest ${fmt.date(latest.date)} is ${latest.direction} with ${fmt.pct(latest.prob_up, 0)} probability.`;
        }
      }
    } catch (error) {
      FH.toast(`Forecast history: ${error.message}`, "bad");
    }
  }

  function ensureForecastControls() {
    const symbolSelect = document.getElementById("fc-symbol");
    if (symbolSelect && !symbolSelect.options.length) {
      FH.fillSelect(symbolSelect, state.universe, state.forecast.symbol || state.universe[0]);
      state.forecast.symbol = symbolSelect.value || null;
    }
    document.querySelectorAll("#fc-horizon button").forEach((button) => {
      button.classList.toggle("active", button.dataset.horizon === state.forecast.horizon);
    });
    const dateInput = document.getElementById("fc-date");
    if (dateInput && state.forecast.snapshot?.date) dateInput.value = state.forecast.snapshot.date;
    const confidence = document.getElementById("fc-confidence");
    if (confidence) confidence.value = String(state.forecast.confidence || 0);
  }

  async function loadSnapshot() {
    const params = new URLSearchParams({
      horizon: state.forecast.horizon,
      limit: "60",
      min_confidence: String(state.forecast.confidence || 0),
    });
    if (state.forecast.date) params.set("date", state.forecast.date);
    const snapshot = await FH.api(`/signals/directions?${params.toString()}`);
    state.forecast.snapshot = snapshot;
    renderSnapshot(snapshot);
  }

  async function loadForecast() {
    if (!document.getElementById("fc-horizon")) return;
    ensureForecastControls();
    const [metaResult, engineResult] = await Promise.allSettled([FH.api("/signals/directions/meta"), FH.api("/engines")]);
    if (metaResult.status === "fulfilled") state.forecast.meta = metaResult.value;
    renderMeta(state.forecast.meta || {}, engineResult.status === "fulfilled" ? engineResult.value : null);
    const enginePill = document.getElementById("fc-engine");
    if (enginePill) {
      const engine = engineResult.status === "fulfilled" ? engineResult.value : {};
      enginePill.textContent = engine.engine === "qlib" ? `Qlib ${engine.qlib_version || ""}` : "Native engine";
      enginePill.className = `pill ${engine.available ? "good" : ""}`;
    }
    await loadSnapshot();
    await loadHistory();
  }
  FH.views.forecast = loadForecast;

  function bindForecast() {
    const horizon = document.getElementById("fc-horizon");
    if (!horizon) return;
    horizon.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-horizon]");
      if (!button) return;
      state.forecast.horizon = button.dataset.horizon;
      state.forecast.date = null;
      document.querySelectorAll("#fc-horizon button").forEach((node) => node.classList.toggle("active", node === button));
      Promise.all([loadSnapshot(), loadHistory()]).catch((error) => FH.toast(error.message, "bad"));
    });
    document.getElementById("fc-date")?.addEventListener("change", (event) => {
      state.forecast.date = event.target.value || null;
      loadSnapshot().catch((error) => FH.toast(error.message, "bad"));
    });
    document.getElementById("fc-confidence")?.addEventListener("change", (event) => {
      state.forecast.confidence = Number(event.target.value) || 0;
      loadSnapshot().catch((error) => FH.toast(error.message, "bad"));
    });
    document.getElementById("fc-symbol")?.addEventListener("change", (event) => {
      state.forecast.symbol = event.target.value;
      loadHistory();
    });
    document.getElementById("fc-rebuild")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Rebuilding…";
      try {
        const job = await FH.api("/directions", { method: "POST", body: JSON.stringify({ engine: "auto", force: true }) });
        await FH.pollJob(job.job_id, {
          onUpdate: (status) => {
            button.textContent = status.status === "running" ? "Building…" : "Queued…";
          },
          onError: (status) => FH.toast(`Forecast build failed: ${status.error || "unknown error"}`, "bad"),
        });
        state.forecast.date = null;
        await loadForecast();
        FH.toast("Forecasts rebuilt", "good");
      } catch (error) {
        FH.toast(`Forecast build: ${error.message}`, "bad");
      } finally {
        button.disabled = false;
        button.textContent = "Rebuild forecasts";
      }
    });
  }

  bindForecast();
})();
