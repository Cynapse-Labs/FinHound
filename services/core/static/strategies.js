/* FinHound dashboard: strategy library, validation, and creation wizard. */
"use strict";

(function () {
  const { fmt, esc } = FH;
  const state = FH.state;
  const ui = state.strategy;

  let factors = [];
  let bound = false;

  const PRESETS = {
    momentum: { momentum_12_1: 0.35, momentum_6_1: 0.3, momentum_3_1: 0.2, momentum_1m: 0.15 },
    reversal: { reversal: 0.5, bollinger_z_20: 0.3, rsi_14: 0.2 },
    defensive: { volatility_60: 0.35, downside_vol_60: 0.25, drawdown_252: 0.2, skew_60: 0.1, beta_60: 0.1 },
    trend: { trend_50: 0.35, trend_200: 0.25, distance_high: 0.2, macd: 0.2 },
    ensemble: {
      momentum_12_1: 0.18,
      momentum_3_1: 0.12,
      trend_50: 0.12,
      reversal: 0.1,
      volatility_60: 0.16,
      distance_high: 0.1,
      dollar_volume: 0.12,
      amihud_illiquidity: 0.1,
    },
  };

  const FALLBACK_FACTORS = [
    "momentum_12_1", "momentum_6_1", "momentum_3_1", "momentum_1m", "momentum_1w",
    "reversal", "volatility", "volatility_20", "volatility_60", "downside_vol_60",
    "drawdown_252", "distance_high", "distance_low_252", "rsi_14", "macd",
    "bollinger_z_20", "atr_14", "volume_trend", "dollar_volume", "amihud_illiquidity",
    "skew_60", "trend_50", "trend_200", "beta_60",
  ];

  const HORIZON_LABELS = { "1d": "1 day", "1w": "1 week", "1m": "1 month", "3m": "3 months" };
  const ENGINE_LABELS = { auto: "Auto", native: "Native", qlib: "Qlib" };

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function defaultDefinition() {
    return {
      id: "",
      name: "",
      description: "",
      category: "Custom",
      tags: [],
      engine: "auto",
      horizon: "1m",
      qlib_blend: 0.7,
      factor_weights: { momentum_6_1: 0.4, trend_50: 0.3, volatility_60: 0.3 },
      filters: { min_price: 1, min_dollar_volume: 1000000, trend: "none", volatility: "none", min_history: 252 },
      portfolio: {
        mode: "long_only",
        rebalance_frequency: "weekly",
        quantile: 0.2,
        max_position_weight: 0.1,
        gross_exposure: 1,
        long_fraction: 0.5,
        weighting: "equal",
      },
      risk: { target_volatility: null, drawdown_stop: null, drawdown_resume: null, max_gross_exposure: 1 },
      costs: { commission_bps: 1, slippage_bps: 2, borrow_bps: 50 },
      builtin: false,
      version: 1,
    };
  }

  function factorLabel(name) {
    const item = factors.find((entry) => entry.name === name);
    return item?.label || name;
  }

  function factorOptions(selected) {
    const names = factors.length ? factors.map((entry) => entry.name) : FALLBACK_FACTORS;
    return names
      .map((name) => {
        const item = factors.find((entry) => entry.name === name);
        const label = item?.label || name;
        return `<option value="${esc(name)}"${name === selected ? " selected" : ""}>${esc(label)}</option>`;
      })
      .join("");
  }

  function uniqueId(base) {
    const used = new Set((ui.items || []).map((item) => item.id));
    if (!used.has(base)) return base;
    for (let index = 2; index < 1000; index += 1) {
      const candidate = `${base}_${index}`;
      if (!used.has(candidate)) return candidate;
    }
    return `${base}_${Date.now()}`;
  }

  function slug(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 48);
  }

  /* -------------------------------- Library -------------------------------- */

  function renderLibrary() {
    const node = document.getElementById("strategy-library");
    if (!node) return;
    const query = (document.getElementById("strategy-search")?.value || "").trim().toLowerCase();
    const category = document.getElementById("strategy-category")?.value || "";
    const engine = document.getElementById("strategy-engine-filter")?.value || "";
    const items = (ui.items || []).filter((item) => {
      if (category && item.category !== category) return false;
      if (engine && item.engine !== engine) return false;
      if (!query) return true;
      const haystack = [item.id, item.name, item.description, item.category, ...(item.tags || [])].join(" ").toLowerCase();
      return haystack.includes(query);
    });
    if (!items.length) {
      node.innerHTML = FH.empty("No strategies match", "Clear the filters or create a new strategy.");
      return;
    }
    node.innerHTML = items
      .map((item) => {
        const weights = Object.entries(item.factor_weights || {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
        const chips = weights
          .slice(0, 4)
          .map(([name, weight]) => `<span class="chip">${esc(factorLabel(name))} <span class="mono">${fmt.signed(weight, 2)}</span></span>`)
          .join("");
        const tags = (item.tags || []).map((tag) => `<span class="tag">${esc(tag)}</span>`).join("");
        return `<article class="strategy-card" data-id="${esc(item.id)}">
          <header>
            <div>
              <h3>${esc(item.name)}</h3>
              <div class="strategy-sub">${esc(item.category)} · ${esc(ENGINE_LABELS[item.engine] || item.engine)} · ${esc(HORIZON_LABELS[item.horizon] || item.horizon)}</div>
            </div>
            ${item.builtin ? FH.badge("built-in", "neutral") : FH.badge("custom", "good")}
          </header>
          <p class="strategy-description">${esc(item.description || "No description yet.")}</p>
          <div class="strategy-chips">${chips}</div>
          <div class="strategy-tags">${tags}</div>
          <footer>
            <button class="ghost small" data-action="run" data-id="${esc(item.id)}">Run</button>
            <button class="ghost small" data-action="preview" data-id="${esc(item.id)}">Preview</button>
            <button class="ghost small" data-action="duplicate" data-id="${esc(item.id)}">Duplicate</button>
            <button class="ghost small" data-action="${item.builtin ? "inspect" : "edit"}" data-id="${esc(item.id)}">${item.builtin ? "Inspect" : "Edit"}</button>
            ${item.builtin ? "" : `<button class="ghost small danger" data-action="delete" data-id="${esc(item.id)}">Delete</button>`}
          </footer>
        </article>`;
      })
      .join("");
  }

  function populateCategories() {
    const select = document.getElementById("strategy-category");
    if (!select) return;
    const current = select.value;
    const categories = [...new Set((ui.items || []).map((item) => item.category).filter(Boolean))].sort();
    select.innerHTML = `<option value="">All categories</option>${categories
      .map((category) => `<option value="${esc(category)}">${esc(category)}</option>`)
      .join("")}`;
    if (categories.includes(current)) select.value = current;
  }

  function findStrategy(id) {
    return (ui.items || []).find((item) => item.id === id) || null;
  }

  /* -------------------------------- Wizard -------------------------------- */

  function addFactorRow(name = "", weight = "") {
    const container = document.getElementById("wizard-factors");
    if (!container) return;
    const row = document.createElement("div");
    row.className = "factor-weight-row";
    row.innerHTML = `<select class="factor-name">${factorOptions(name || "")}</select>
      <input class="factor-weight mono" type="number" step="0.05" min="-10" max="10" value="${weight === "" ? "0.2" : esc(weight)}">
      <button class="ghost small" type="button" data-remove-factor>Remove</button>`;
    container.appendChild(row);
  }

  function setFactorRows(weights) {
    const container = document.getElementById("wizard-factors");
    if (!container) return;
    container.innerHTML = "";
    for (const [name, weight] of Object.entries(weights || {})) addFactorRow(name, weight);
    if (!container.children.length) addFactorRow("momentum_6_1", 0.5);
  }

  function numberValue(id, fallback = 0) {
    const value = FH.toNumber(document.getElementById(id)?.value);
    return value === null ? fallback : value;
  }

  function optionalNumber(id) {
    const raw = document.getElementById(id)?.value;
    if (raw === "" || raw === undefined || raw === null) return null;
    const value = FH.toNumber(raw);
    return value === null ? null : value;
  }

  function collectDefinition() {
    const weights = {};
    document.querySelectorAll("#wizard-factors .factor-weight-row").forEach((row) => {
      const name = row.querySelector(".factor-name")?.value;
      const weight = FH.toNumber(row.querySelector(".factor-weight")?.value);
      if (name && weight !== null) weights[name] = weight;
    });
    const tags = (document.getElementById("sw-tags")?.value || "")
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean);
    return {
      id: (document.getElementById("sw-id")?.value || "").trim(),
      name: (document.getElementById("sw-name")?.value || "").trim(),
      description: (document.getElementById("sw-description")?.value || "").trim(),
      category: (document.getElementById("sw-category")?.value || "Custom").trim() || "Custom",
      tags,
      engine: document.getElementById("sw-engine")?.value || "auto",
      horizon: document.getElementById("sw-horizon")?.value || "1m",
      qlib_blend: numberValue("sw-qlib-blend", 0.7),
      factor_weights: weights,
      filters: {
        min_price: numberValue("sw-min-price", 1),
        min_dollar_volume: numberValue("sw-min-dollar", 1000000),
        trend: document.getElementById("sw-trend")?.value || "none",
        volatility: document.getElementById("sw-volatility")?.value || "none",
        min_history: Math.round(numberValue("sw-min-history", 252)),
      },
      portfolio: {
        mode: document.getElementById("sw-mode")?.value || "long_only",
        rebalance_frequency: document.getElementById("sw-frequency")?.value || "weekly",
        quantile: numberValue("sw-quantile", 0.2),
        max_position_weight: numberValue("sw-max-weight", 0.1),
        gross_exposure: numberValue("sw-gross", 1),
        long_fraction: numberValue("sw-long-fraction", 0.5),
        weighting: "equal",
      },
      risk: {
        target_volatility: optionalNumber("sw-target-vol"),
        drawdown_stop: optionalNumber("sw-drawdown-stop"),
        drawdown_resume: optionalNumber("sw-drawdown-resume"),
        max_gross_exposure: numberValue("sw-max-gross", 1),
      },
      costs: {
        commission_bps: numberValue("sw-commission", 1),
        slippage_bps: numberValue("sw-slippage", 2),
        borrow_bps: numberValue("sw-borrow", 50),
      },
      builtin: false,
      version: 1,
    };
  }

  function fillForm(definition) {
    const set = (id, value) => {
      const node = document.getElementById(id);
      if (node) node.value = value === null || value === undefined ? "" : value;
    };
    set("sw-name", definition.name);
    set("sw-id", definition.id);
    set("sw-description", definition.description);
    set("sw-category", definition.category);
    set("sw-tags", (definition.tags || []).join(", "));
    set("sw-engine", definition.engine);
    set("sw-horizon", definition.horizon);
    set("sw-qlib-blend", definition.qlib_blend);
    setFactorRows(definition.factor_weights);
    const filters = definition.filters || {};
    set("sw-min-price", filters.min_price);
    set("sw-min-dollar", filters.min_dollar_volume);
    set("sw-trend", filters.trend);
    set("sw-volatility", filters.volatility);
    set("sw-min-history", filters.min_history);
    const portfolio = definition.portfolio || {};
    set("sw-mode", portfolio.mode);
    set("sw-frequency", portfolio.rebalance_frequency);
    set("sw-quantile", portfolio.quantile);
    set("sw-max-weight", portfolio.max_position_weight);
    set("sw-gross", portfolio.gross_exposure);
    set("sw-long-fraction", portfolio.long_fraction);
    const risk = definition.risk || {};
    set("sw-target-vol", risk.target_volatility);
    set("sw-drawdown-stop", risk.drawdown_stop);
    set("sw-drawdown-resume", risk.drawdown_resume);
    set("sw-max-gross", risk.max_gross_exposure);
    const costs = definition.costs || {};
    set("sw-commission", costs.commission_bps);
    set("sw-slippage", costs.slippage_bps);
    set("sw-borrow", costs.borrow_bps);
    set("sw-start", "");
    set("sw-end", "");
    set("sw-split", "");
  }

  function openWizard(item, mode) {
    const panel = document.getElementById("strategy-wizard");
    if (!panel) return;
    ui.mode = mode || "create";
    const definition = item ? clone(item) : defaultDefinition();
    if (ui.mode === "duplicate") {
      definition.id = uniqueId(`${slug(definition.id || definition.name)}_copy`.replace(/^_+/, ""));
      definition.name = `${definition.name} Copy`;
      definition.builtin = false;
    }
    if (ui.mode === "create") {
      definition.id = definition.id || "";
    }
    ui.editing = ui.mode === "edit" ? definition.id : null;
    ui.validation = null;
    ui.preview = null;
    fillForm(definition);
    const title = document.getElementById("wizard-title");
    if (title) title.textContent = ui.mode === "edit" ? `Edit · ${definition.name}` : ui.mode === "duplicate" ? "Duplicate strategy" : "New strategy";
    const preview = document.getElementById("strategy-preview");
    if (preview) preview.innerHTML = "";
    const validation = document.getElementById("wizard-validation");
    if (validation) validation.className = "validation-box";
    if (validation) validation.textContent = "Validate or preview to see factor coverage and warnings.";
    panel.classList.remove("hidden");
    setStep(1);
    if (typeof panel.scrollIntoView === "function") panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function closeWizard() {
    document.getElementById("strategy-wizard")?.classList.add("hidden");
  }

  function setStep(step) {
    ui.step = Math.max(1, Math.min(5, step));
    document.querySelectorAll("#strategy-wizard .wizard-step").forEach((node) => {
      node.classList.toggle("active", Number(node.dataset.step) === ui.step);
    });
    document.querySelectorAll("#wizard-stepper .stepper-item").forEach((node) => {
      node.classList.toggle("active", Number(node.dataset.step) === ui.step);
      node.classList.toggle("done", Number(node.dataset.step) < ui.step);
    });
    const label = document.getElementById("wizard-step-label");
    if (label) label.textContent = `Step ${ui.step} of 5`;
    document.getElementById("wizard-back")?.classList.toggle("hidden", ui.step === 1);
    document.getElementById("wizard-next")?.classList.toggle("hidden", ui.step === 5);
    document.getElementById("wizard-save")?.classList.toggle("hidden", ui.step !== 5);
    if (ui.step === 5) updateSummary();
  }

  function updateSummary() {
    const definition = collectDefinition();
    const node = document.getElementById("wizard-summary");
    if (!node) return;
    const weights = Object.entries(definition.factor_weights || {});
    const rows = [
      ["Engine", ENGINE_LABELS[definition.engine] || definition.engine],
      ["Horizon", HORIZON_LABELS[definition.horizon] || definition.horizon],
      ["Mode", definition.portfolio.mode === "long_short" ? "Long / short" : "Long only"],
      ["Rebalance", definition.portfolio.rebalance_frequency],
      ["Quantile", fmt.pct(definition.portfolio.quantile, 1)],
      ["Max weight", fmt.pct(definition.portfolio.max_position_weight, 1)],
      ["Gross exposure", fmt.num(definition.portfolio.gross_exposure, 2)],
      ["Universe filter", definition.filters.trend === "none" ? "liquidity + history" : definition.filters.trend],
      ["Target volatility", definition.risk.target_volatility ? fmt.pct(definition.risk.target_volatility, 1) : "off"],
      ["Drawdown stop", definition.risk.drawdown_stop ? fmt.pct(definition.risk.drawdown_stop, 1) : "off"],
      ["Factors", String(weights.length)],
    ];
    node.innerHTML = `<dl class="detail-list">${rows
      .map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`)
      .join("")}</dl>
      <div class="strategy-chips">${weights
        .map(([name, weight]) => `<span class="chip">${esc(factorLabel(name))} <span class="mono">${fmt.signed(weight, 2)}</span></span>`)
        .join("")}</div>`;
  }

  function stepOneValid() {
    const definition = collectDefinition();
    if (!definition.name || definition.name.length < 2) {
      FH.toast("Give the strategy a name (2+ characters).", "bad");
      return false;
    }
    if (!/^[a-z0-9][a-z0-9_-]{1,63}$/.test(definition.id)) {
      FH.toast("Strategy id must be 2-64 lowercase letters, digits, dashes, or underscores.", "bad");
      return false;
    }
    return true;
  }

  function renderValidation(validation) {
    const node = document.getElementById("wizard-validation");
    if (!node) return;
    const errors = validation.errors || [];
    const warnings = validation.warnings || [];
    const coverage = validation.coverage || {};
    const valid = validation.valid !== false && !errors.length;
    node.className = `validation-box ${valid ? "ok" : "bad"}`;
    const coverageItems = Object.entries(coverage)
      .map(([name, stats]) => `<li>${esc(factorLabel(name))}: ${fmt.int(stats.rows)} rows · ${fmt.int(stats.symbols)} symbols</li>`)
      .join("");
    node.innerHTML = `<div class="validation-head">
        ${FH.badge(valid ? "VALID" : "NEEDS ATTENTION", valid ? "good" : "bad")}
        <span>${fmt.int(validation.estimated_positions)} estimated positions per side</span>
      </div>
      ${errors.length ? `<div><strong>Errors</strong><ul>${errors.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}
      ${warnings.length ? `<div><strong>Warnings</strong><ul>${warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}
      ${coverageItems ? `<div><strong>Factor coverage</strong><ul>${coverageItems}</ul></div>` : ""}`;
  }

  function draftPayload() {
    const definition = collectDefinition();
    if (!definition.id) definition.id = "draft_strategy";
    if (!definition.name) definition.name = "Draft Strategy";
    return definition;
  }

  async function validateDraft() {
    try {
      const validation = await FH.api("/strategies/validate", { method: "POST", body: JSON.stringify(draftPayload()) });
      ui.validation = validation;
      renderValidation(validation);
      return validation;
    } catch (error) {
      FH.toast(`Validation: ${error.message}`, "bad");
      return null;
    }
  }

  function renderPreview(preview) {
    const node = document.getElementById("strategy-preview");
    if (!node) return;
    if (!preview || preview.available === false) {
      node.innerHTML = FH.empty("No preview available", "Check the factor coverage and try again.");
      return;
    }
    const side = (items) =>
      (items || [])
        .map((row) =>
          FH.listRow({
            rank: row.rank,
            symbol: row.symbol,
            value: fmt.signed(row.score, 2),
            width: Math.max(4, Math.min(100, Math.abs(FH.toNumber(row.score) || 0) * 40)),
            negative: FH.toNumber(row.score) < 0,
          })
        )
        .join("");
    node.innerHTML = `<h3>Latest signal preview · ${fmt.date(preview.date)}</h3>
      <div class="kpi-grid compact">
        ${FH.kpi({ label: "Scored names", value: fmt.int(preview.count), sub: fmt.date(preview.date) })}
        ${FH.kpi({ label: "Mean score", value: fmt.signed(preview.mean_score, 2), sub: `std ${fmt.num(preview.std_score, 2)}` })}
      </div>
      <div class="grid two">
        <div><h4>Highest scores</h4>${side(preview.long)}</div>
        <div><h4>Lowest scores</h4>${side(preview.short)}</div>
      </div>`;
  }

  async function previewDraft() {
    const button = document.getElementById("wizard-preview");
    if (button) button.disabled = true;
    try {
      const preview = await FH.api("/strategies/preview?limit=8", { method: "POST", body: JSON.stringify(draftPayload()) });
      ui.preview = preview;
      if (preview.validation) {
        ui.validation = preview.validation;
        renderValidation(preview.validation);
      }
      renderPreview(preview);
      FH.toast("Preview generated", "good");
    } catch (error) {
      FH.toast(`Preview: ${error.message}`, "bad");
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function saveStrategy(event) {
    event.preventDefault();
    if (!stepOneValid()) {
      setStep(1);
      return;
    }
    const definition = collectDefinition();
    const button = document.getElementById("wizard-save");
    if (button) button.disabled = true;
    try {
      let saved;
      if (ui.mode === "edit") {
        saved = await FH.api(`/strategies/${encodeURIComponent(definition.id)}`, {
          method: "PUT",
          body: JSON.stringify(definition),
        });
      } else {
        saved = await FH.api("/strategies", { method: "POST", body: JSON.stringify(definition) });
      }
      ui.editing = saved.id;
      ui.mode = "edit";
      const title = document.getElementById("wizard-title");
      if (title) title.textContent = `Edit · ${saved.name}`;
      await loadStrategies();
      FH.toast(`Saved ${saved.name}`, "good");
    } catch (error) {
      FH.toast(`Save: ${error.message}`, "bad");
    } finally {
      if (button) button.disabled = false;
    }
  }

  function strategyRunPayload() {
    return {
      start_date: document.getElementById("sw-start")?.value || null,
      end_date: document.getElementById("sw-end")?.value || null,
      split_date: document.getElementById("sw-split")?.value || null,
    };
  }

  async function runStrategy(id) {
    const status = document.getElementById("strategy-run-status");
    if (status) status.textContent = "Queuing strategy backtest…";
    try {
      const job = await FH.api(`/strategies/${encodeURIComponent(id)}/run`, {
        method: "POST",
        body: JSON.stringify(strategyRunPayload()),
      });
      if (status) status.textContent = "Strategy job queued…";
      await FH.pollJob(job.job_id, {
        onUpdate: (payload) => {
          if (status) status.textContent = `${payload.status}${payload.stage ? ` · ${payload.stage}` : ""}…`;
        },
        onError: (payload) => {
          if (status) status.textContent = `Failed: ${payload.error || "unknown error"}`;
          FH.toast(`Strategy run failed: ${payload.error || "unknown error"}`, "bad");
        },
      });
      const finished = await FH.api(`/jobs/${encodeURIComponent(job.job_id)}`);
      if (finished.status === "completed" && finished.run_id) {
        FH.state.runId = finished.run_id;
        if (status) status.textContent = `Completed · run ${finished.run_id}`;
        FH.toast(`Strategy run completed: ${finished.run_id}`, "good");
        FH.activateTab("backtest");
      }
    } catch (error) {
      if (status) status.textContent = `Failed: ${error.message}`;
      FH.toast(`Strategy run: ${error.message}`, "bad");
    }
  }

  async function deleteStrategy(id) {
    const item = findStrategy(id);
    if (!item) return;
    if (!window.confirm(`Delete "${item.name}"? This cannot be undone.`)) return;
    try {
      await FH.api(`/strategies/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (ui.editing === id) closeWizard();
      await loadStrategies();
      FH.toast(`Deleted ${item.name}`, "good");
    } catch (error) {
      FH.toast(`Delete: ${error.message}`, "bad");
    }
  }

  /* ------------------------------- Lifecycle ------------------------------- */

  async function loadStrategies() {
    if (!document.getElementById("strategy-library")) return;
    try {
      const [items, catalog] = await Promise.allSettled([FH.api("/strategies"), FH.api("/factors/catalog")]);
      if (items.status === "fulfilled") ui.items = items.value || [];
      if (catalog.status === "fulfilled" && Array.isArray(catalog.value)) factors = catalog.value;
      populateCategories();
      renderLibrary();
    } catch (error) {
      FH.toast(`Strategies: ${error.message}`, "bad");
    }
  }
  FH.views.strategies = loadStrategies;

  function bindStrategyUI() {
    if (bound) return;
    bound = true;
    document.getElementById("strategy-new")?.addEventListener("click", () => openWizard(null, "create"));
    document.getElementById("wizard-close")?.addEventListener("click", closeWizard);
    document.getElementById("wizard-back")?.addEventListener("click", () => setStep(ui.step - 1));
    document.getElementById("wizard-next")?.addEventListener("click", () => {
      if (ui.step === 1 && !stepOneValid()) return;
      setStep(ui.step + 1);
    });
    document.getElementById("wizard-validate")?.addEventListener("click", validateDraft);
    document.getElementById("wizard-preview")?.addEventListener("click", previewDraft);
    document.getElementById("strategy-form")?.addEventListener("submit", saveStrategy);
    document.getElementById("sw-add-factor")?.addEventListener("click", () => addFactorRow("", 0.2));
    document.getElementById("wizard-factors")?.addEventListener("click", (event) => {
      if (event.target.closest("[data-remove-factor]")) event.target.closest(".factor-weight-row")?.remove();
    });
    document.getElementById("sw-preset")?.addEventListener("change", (event) => {
      const preset = PRESETS[event.target.value];
      if (preset) setFactorRows(preset);
    });
    document.getElementById("sw-name")?.addEventListener("input", (event) => {
      const idInput = document.getElementById("sw-id");
      if (idInput && (!idInput.value || idInput.dataset.auto === "1")) {
        idInput.value = slug(event.target.value);
        idInput.dataset.auto = "1";
      }
    });
    document.getElementById("sw-id")?.addEventListener("input", (event) => {
      event.target.dataset.auto = "0";
    });
    document.getElementById("strategy-search")?.addEventListener("input", renderLibrary);
    document.getElementById("strategy-category")?.addEventListener("change", renderLibrary);
    document.getElementById("strategy-engine-filter")?.addEventListener("change", renderLibrary);
    document.getElementById("strategy-library")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const id = button.dataset.id;
      const action = button.dataset.action;
      const item = findStrategy(id);
      if (!item) return;
      if (action === "run") runStrategy(id);
      else if (action === "preview") {
        openWizard(item, item.builtin ? "duplicate" : "edit");
        setStep(5);
        previewDraft();
      } else if (action === "duplicate") openWizard(item, "duplicate");
      else if (action === "edit") openWizard(item, "edit");
      else if (action === "inspect") openWizard(item, "duplicate");
      else if (action === "delete") deleteStrategy(id);
    });
  }

  bindStrategyUI();
})();
