# FinHound QuantPlatform

FinHound is a small quantitative-research platform for US equities on daily
bars. It runs the same basic workflow used by a larger research system:

```text
prices -> quality checks -> factors -> signal -> forecast -> backtest -> API/dashboard
```

The project is a production-hardened research platform: it has validated data
contracts, reproducible runs, persistence, observability, and a live dashboard.
Its results are still research output, not investment advice or evidence of
future performance.

## 1. What is included?

- **Ingestion:** OpenBB with the yfinance provider.
- **Storage:** raw Parquet files plus a DuckDB research database.
- **Quality control:** NYSE calendar checks and invalid-row filtering.
- **Universe:** a generated 5,126-symbol US equity list (5,125 tradable plus
  `SPY`) built from the NASDAQ Trader symbol directory.
- **Factors:** 24 oriented price, trend, volatility, liquidity, and risk
  factors with a browsable catalog.
- **Signals:** named, configurable weighted factor files.
- **Forecasts:** 1-day, 1-week, 1-month, and 3-month up/down signals with
  probability, expected return, confidence, and historical hit rate.
- **Strategies:** 32 built-in strategies plus a validated strategy wizard and
  persisted custom strategies.
- **Qlib integration (optional):** real Qlib binary datasets, Alpha158
  features, and walk-forward LightGBM models for forecast generation.
- **Backtests:** long-only or long/short, capped equal weights, weekly/monthly
  rebalancing, costs, causal risk overlays, SPY and equal-weight benchmarks.
- **API:** FastAPI on port 8000 with background jobs, request IDs, optional API
  keys, and CORS controls.
- **Dashboard:** a single-page research terminal served by FastAPI, with live
  charts, factor and signal research, multi-horizon forecasts, strategy
  creation, and backtest controls.
- **Tests and CI:** pytest, Ruff, and GitHub Actions.

The ingest and core services have separate dependency environments. They share
only files in `data/`; they do not share a Python environment or communicate
with each other over HTTP.

## 2. Start here

### Requirements

- Python 3.11
- Docker Engine and Docker Compose v2
- Internet access for dependency installation and the yfinance download

### Run locally: one setup command

From the repository root, run:

```bash
make setup
make all
```

`make setup` creates the two required virtual environments and installs the
exact pinned dependencies. It is safe to re-run: a virtual environment built
with the wrong Python version is recreated. If `uv` is installed (recommended),
setup uses it and can provision Python 3.11 automatically. Otherwise, install
Python 3.11 and make sure `python3.11` is on `PATH`.

Qlib is optional. Install it with `make qlib` (or `WITH_QLIB=1 make setup`).
Without it, forecasts and strategies use the native factor engine; with it,
`directions.engine: auto` trains walk-forward LightGBM models on Alpha158
features and falls back to native scores if training is unavailable.

`make all` runs ingestion, QC, factors, signal generation, multi-horizon
forecasts, and backtesting in order.

Start the API in a second terminal:

```bash
make api
```

Then open:

- <http://localhost:8000/> — dashboard
- <http://localhost:8000/docs> — interactive API documentation
- <http://localhost:8000/openapi.json> — OpenAPI schema

The dashboard is a single-page research terminal backed by the API:

- **Overview** — universe coverage, QC status, latest metrics, and pipeline/ingestion history.
- **Market data** — price and volume charts, period statistics, and recent sessions.
- **Factors** — factor history, cross-sectional leaders and laggards, and z-score drift.
- **Signals** — long/short candidates, score distribution, and scored-name coverage.
- **Forecast** — 1-day, 1-week, 1-month, and 3-month up/down signals, confidence, expected return, and per-symbol history.
- **Strategies** — browse built-ins, build or edit strategies in the wizard, preview holdings, and launch backtests.
- **Backtest** — run a simulation, inspect equity/drawdown/exposure/IC diagnostics, and reload prior runs.
- **Data health** — QC summary, issue detail, ingestion runs, and pipeline runs.

Refresh the universe file and add the optional Qlib stack with:

```bash
make universe   # regenerate config/universe.yaml from NASDAQ Trader
make qlib       # install pyqlib and LightGBM into .venv-core
```

The API opens DuckDB read-write by default so the dashboard can rebuild analytics
and launch backtests. If a separate pipeline process owns the database, start the
API with `API_READ_ONLY=1`; the dashboard then serves stored results, while
`POST /pipeline` and `POST /backtests` return HTTP 409.

For one local command that performs setup, runs the pipeline, and starts the
API, use:

```bash
make local
```

### Run with Docker: no Python setup

```bash
make up
```

Docker is the easiest option if Python is not installed. The repository
`data/` directory is mounted into both containers. The ingest container writes
raw data; the core container reads it and serves the API.

## 3. Daily commands

| Command | What it does |
|---|---|
| `make ingest` | Download or incrementally update raw prices. |
| `make qc` | Build DuckDB tables and remove rows with error-level QC issues. |
| `make factors` | Calculate and store factors. |
| `make signal` | Create the configured signal Parquet file and refresh forecasts. |
| `make directions` | Build 1-day, 1-week, 1-month, and 3-month forecasts. |
| `make backtest` | Run the default signal backtest. |
| `make optimize` | Build dashboard indexes and update DuckDB statistics. |
| `make all` | Run ingestion, QC, factors, signals, directions, and backtest in order. |
| `make universe` | Regenerate `config/universe.yaml` from the NASDAQ Trader directory. |
| `make qlib` | Install the optional Qlib/LightGBM forecasting stack. |
| `make test` | Run pytest. |
| `make lint` | Run Ruff lint and formatting checks. |
| `make build` | Build Docker images. |
| `make up` | Start the Docker Compose application. |
| `make api` | Serve the dashboard and API on <http://localhost:8000>. |
| `make setup` | Create or repair both local virtual environments and install pinned dependencies. |
| `make local` | Set up, run the full local pipeline, and start the API. |

Generated data is ignored by Git:

```text
data/
├── raw/prices/symbol=<SYMBOL>/data.parquet
├── ingest_runs.parquet
├── store.duckdb
├── signals/<name>.parquet
├── directions/
│   ├── directions.parquet
│   └── meta.json
├── qlib/                     # generated Qlib binary dataset when enabled
├── pipeline_runs/
└── results/<run_id>/
    ├── equity.parquet
    └── positions.parquet
```

## 4. Configuration

### Universe

`config/universe.yaml` is generated from the NASDAQ Trader symbol directory by
`scripts/build_universe.py` (`make universe`). The current file contains 5,126
symbols: 5,125 common/depositary equity lines plus `SPY`. `SPY` is a benchmark
and is marked non-tradable. The default start date is `2015-01-01`.

The builder drops ETFs, test issues, warrants, rights, units, preferreds, notes,
funds, and other non-common lines, and normalises class-share symbols to the
dash form used by Yahoo Finance (`BRK.B` becomes `BRK-B`). Regenerate it
periodically; listings change.

Each entry has:

```yaml
- symbol: AAPL
  sector: Technology
  tradable: true
```

The generated file marks sectors as `Unknown` except for `SPY`. This is **not**
a point-in-time historical universe. It is a current list applied backward
through history, so backtests have survivorship bias. `Store` reconciles the
generated `static_current_constituents` rows on startup while preserving any
curated or dated membership rows you add.

### Signal settings

`config/settings.yaml` defines signal weights and the liquidity threshold:

```yaml
signal:
  default: momentum_reversal_liquidity
  definitions:
    momentum_reversal_liquidity:
      weights:
        momentum_12_1: 0.4
        reversal: 0.1
        volatility: 0.2
        distance_high: 0.1
        dollar_volume: 0.2
      liquidity_min_dollar_volume: 10000000
```

The name describes the actual inputs: there is no fundamentals-based quality
factor. Add another named definition to create another signal family.

The strategy catalog also includes research templates for statistical
arbitrage, pairs trading, market making, momentum/trend following,
cross-sectional mean reversion, machine learning, options volatility
arbitrage, high-frequency trading, and an AMD-focused regime strategy. Daily
OHLCV data cannot validate cointegration execution, options surface trades,
market making, or HFT; those entries are marked `research_only` or
`data_required` and list the missing data explicitly.

The uploaded Glucksman/Lahanis paper, *Online Quantitative Trading Strategies:
An Empirical Performance Overview Through Backtesting*, reinforces the need to
compare strategies with consistent data windows, transaction costs, turnover,
and out-of-sample validation. The Sharpe ranges in the catalog are research
priors supplied by the user, not promises or results generated by this
repository.

Backtest defaults are also in this file:

```yaml
backtest:
  rebalance_frequency: weekly
  commission_bps: 1.0
  slippage_bps: 2.0
  borrow_bps: 50.0
```

Backtest results also report tail-risk diagnostics (`VaR_95`, `CVaR_95`,
worst-day return, and tail ratio), maximum observed position weight, and
average concentration HHI. Invalid portfolio settings are rejected before
trading weights are generated.

The backtest performance contract also includes annualized downside-aware and
benchmark-relative measures: Sortino, Calmar, skew, excess kurtosis, Omega,
beta, alpha, tracking error, information ratio, and benchmark correlation.
Returns are cleaned of non-finite observations, constant or short samples
produce finite values, and long/short selection prevents a symbol from being
held on both sides of the book at a rebalance.

Each persisted backtest also exposes `GET /backtests/{run_id}/risk`, which
returns the aggregate metrics, causal rolling volatility/Sharpe/drawdown/beta,
and ranked drawdown episodes with recovery status. This endpoint is intended
for monitoring and model review; it does not replace independent execution,
limit, liquidity, or regulatory controls.

QC eligibility now keeps only symbols whose latest available market session
has a close of at least `$1` and volume of at least `5,000` shares. The full
history for excluded symbols is removed from `clean_prices` and an
`latest_session_eligibility` error is recorded in `qc_issues`; raw provider
data remains available for audit.

Options research also exposes `POST /options/gamma-heatmap`, returning
expiration/strike gamma cells, axes, spot, and the maximum absolute exposure
for direct heat-map rendering.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATA_ROOT` | `/data` | Root for Parquet files and `store.duckdb`. The Makefile sets `data`. |
| `UNIVERSE_PATH` | `/config/universe.yaml` | Universe file read during QC and startup membership sync. |
| `SETTINGS_PATH` | `/config/settings.yaml` | Signal, forecast engine, and backtest settings. |
| `API_READ_ONLY` | `0` | Set to `1` to open DuckDB read-only and reject write routes (`POST /pipeline`, `POST /backtests`, strategy writes and runs, direction rebuilds). |
| `API_KEY` | unset | When set, API requests must send a matching `X-API-Key` header. The dashboard stores the key in local storage. |
| `CORS_ALLOW_ORIGINS` | unset | Comma-separated browser origins allowed to call the API. |
| `ACTIVE_JOBS` | `1` | Number of concurrent background analysis jobs. |
| `RATE_LIMIT_PER_MINUTE` | `0` | Optional per-client request limit; `0` disables it. |
| `FINHOUND_QLIB` | `1` | Set to `0` to disable Qlib detection and force the native factor engine. |
| `INGEST_WORKERS` | `1` | Workers for local symbol file work; provider requests remain serialized to avoid curl/file-descriptor exhaustion. |
| `INGEST_SLEEP_SECONDS` | `0.05` | Delay between symbol downloads. |
| `INGEST_RETRIES` | `2` | Retry count for a failed symbol download. |

A DuckDB database supports only one writer. Run either the pipeline or a
writable API against a given `DATA_ROOT`, not both at once. When the pipeline
owns the database, start the API with `API_READ_ONLY=1` and it will serve the
last completed results.

## 5. How the pipeline works

### Step 1: ingest prices

The ingest service uses OpenBB/yfinance and writes provider data to raw
Parquet. It adds:

- `symbol`
- `provider`
- `fetched_at`
- `adjusted`

The factor library includes momentum, reversal, trend, volatility, drawdown,
liquidity, market-beta, overnight/intraday behavior, range position, volume
surprise, residual momentum, correlation, volatility stability, and realized
skew features. All factors are winsorized and cross-sectionally standardized
before signal construction. The extended library also includes multi-horizon
momentum and trend acceleration, price efficiency and autocorrelation, gap and
range-volatility decomposition, upside/downside capture, downside beta,
idiosyncratic volatility, beta stability, volume z-scores, OBV and money flow,
price-volume correlation, turnover trend, drawdown recovery, and breakout
signals.

### Options gamma exposure

`POST /options/gamma-exposure` accepts a normalized option chain and returns
net dollar gamma, call/put contributions, strike concentration, and an
estimated gamma-flip strike. The calculation uses
`gamma * open_interest * contract_size * spot^2 * 1%`; calls are positive and
puts negative under the standard dealer-position convention. This is an
estimate from public chain data, not a direct dealer-position observation.

It reads the latest stored date and requests only later dates. Duplicate
`(symbol, date)` rows are removed, so a second run is idempotent.

Failures are isolated by symbol and recorded in `data/ingest_runs.parquet`.
One bad ticker does not stop the other tickers.

The `HistoricalProvider` protocol in
`services/ingest/src/ingest/runner.py` is the replacement point for another
vendor. Synthetic providers are used only by tests.

### Step 2: run quality checks

The core service loads raw prices into DuckDB and checks:

- missing NYSE trading days
- duplicate rows
- non-positive prices
- high below low
- open/close outside the high-low range
- absolute close return above 40%
- zero-volume streaks of at least three sessions
- stale series older than five trading sessions

Error rows are excluded from `clean_prices`. Warning rows remain available but
are reported in `qc_issues` and `qc_summary`.

The 40% return check is a diagnostic for possible unadjusted splits. The
`adjusted` column records the provider's adjustment indication; free vendor
data is not guaranteed to be point-in-time or error-free.

### Step 3: calculate factors

Factors use only prices available through the close of the factor date. Every
factor is oriented so that a larger value is the more attractive long signal.

| Group | Factors |
|---|---|
| Momentum | `momentum_12_1`, `momentum_6_1`, `momentum_3_1`, `momentum_1m`, `momentum_1w` |
| Reversal | `reversal`, `rsi_14`, `bollinger_z_20` |
| Trend | `distance_high`, `distance_low_252`, `macd`, `trend_50`, `trend_200` |
| Risk | `volatility`, `volatility_20`, `volatility_60`, `downside_vol_60`, `drawdown_252`, `atr_14`, `skew_60`, `beta_60` |
| Liquidity | `volume_trend`, `dollar_volume`, `amihud_illiquidity` |

Examples: `momentum_12_1` is the 252-session return skipping the most recent 21
sessions; `reversal` is the negative 21-session return; `volatility` is negative
60-session realized volatility; `distance_high` is close divided by the
252-session high minus one; `dollar_volume` is the 20-session average of
close × volume. `GET /factors/catalog` returns the label, group, and description
for each factor.

Each factor is stored in long format with:

- `raw_value`
- cross-sectional `standardized` value

Standardization winsorizes at the 1st and 99th percentiles and then z-scores
each date/factor cross-section. For a broad universe, the pipeline computes and
writes one factor at a time instead of materializing the full long-format table
in pandas.

### Step 4: generate a signal

The signal is a weighted sum of standardized factors. Dollar volume is also
used as a minimum liquidity filter.

Output:

```text
data/signals/<name>.parquet
```

### Step 5: build forecasts and strategies

The direction stage turns the latest factor cross-section into 1-day,
1-week, 1-month, and 3-month `up`/`down` signals. Each row carries:

- `expected_return`
- `prob_up`
- `direction` and `confidence`
- trailing per-symbol `hit_rate`

The default engine is `auto`: it uses Qlib when the optional stack is installed
and the dataset can be trained, and otherwise falls back to native factor
weights. `config/settings.yaml` selects the engine:

```yaml
directions:
  engine: auto   # auto | native | qlib
```

Qlib forecasts use a generated binary dataset, Alpha158 features, and
walk-forward LightGBM models. Rebuild forecasts from the dashboard, `POST
/directions`, or `make directions`. The resulting artifact lives in
`data/directions/directions.parquet` with metadata in `meta.json`.

The 23 built-in strategies combine factor weights, filters, portfolio
construction, risk overlays, and an optional forecast blend. The Strategies tab
runs the same validation, preview, and backtest path as the API. Custom
strategies are persisted in DuckDB; built-ins are immutable and can be
duplicated before editing.

### Step 6: backtest

The backtester:

1. Reads clean close prices and a signal file.
2. Selects the top quantile, or top and bottom quantiles.
3. Uses equal weights while redistributing weight so the cap does not leave
   unintended cash.
4. Rebalances daily, weekly, or monthly.
5. Executes a signal observed at close `t` at close `t+1`.
6. Charges commission and slippage on turnover.
7. Charges annualized borrow cost on short exposure.
8. Compares strategy equity with SPY and equal-weight universe equity.

Research diagnostics include full, in-sample, and out-of-sample performance,
IC, rank IC, IC IR, decile spread, turnover, and average holdings.

## 6. Signal file contract

Signal producers and consumers exchange this exact file shape:

```text
data/signals/<name>.parquet
```

| Column | Type | Meaning |
|---|---|---|
| `date` | `date` | Session where the signal is observed. |
| `symbol` | `string` | Equity symbol. |
| `score` | `float64` | Higher means more attractive. |

There must be no duplicate `(date, symbol)` keys. Signal scores are not
executed on the same close that creates them; execution is delayed to `t+1`.
This contract is the extension point for a second signal producer, Qlib,
Lean, FinanceToolkit, or another language.

## 7. API reference

| Method | Route | Description |
|---|---|---|
| GET | `/health` | Returns service health. |
| GET | `/metrics` | Returns lightweight process and job metrics. |
| GET | `/engines` | Reports native and optional Qlib availability. |
| GET | `/overview` | Dashboard summary: coverage, QC, factors, signals, forecasts, latest run. |
| GET | `/universe` | Lists symbols in clean prices and the stored membership. |
| GET | `/prices/{symbol}` | Returns prices; supports `start` and `end`. |
| GET | `/qc/summary` | Returns per-symbol coverage and issue counts. |
| GET | `/qc/issues` | Returns QC issues; supports `severity` and `limit`. |
| GET | `/factors/catalog` | Returns factor labels, groups, and descriptions. |
| GET | `/factors/summary` | Returns one cross-section; supports `date`, `factor`, `limit`. |
| GET | `/factors/{symbol}` | Returns factor history; supports `factor`. |
| GET | `/signals` | Lists generated signal names. |
| GET | `/signals/{name}` | Returns one signal file. |
| GET | `/signals/{name}/snapshot` | Ranks one signal date; supports `date` and `limit`. |
| GET | `/signals/directions/meta` | Returns forecast horizon metadata and artifact coverage. |
| GET | `/signals/directions` | Returns the multi-horizon forecast snapshot; supports `horizon`, `date`, `limit`, `min_confidence`. |
| GET | `/signals/directions/{symbol}` | Returns forecast history for one symbol; supports `horizon` and `limit`. |
| POST | `/directions` | Queues a forecast rebuild; optional body selects `engine` and `force`. |
| POST | `/directions/reset` | Clears the cached Qlib dataset and forecast artifact. |
| GET | `/strategies` | Lists built-in and custom strategies with validation state. |
| POST | `/strategies/validate` | Validates a draft strategy definition. |
| POST | `/strategies/preview` | Previews a draft strategy's latest holdings and backtest config. |
| POST | `/strategies` | Creates a custom strategy. |
| GET | `/strategies/{strategy_id}` | Returns one strategy and its validation. |
| PUT | `/strategies/{strategy_id}` | Updates a custom strategy. |
| DELETE | `/strategies/{strategy_id}` | Deletes a custom strategy. |
| POST | `/strategies/{strategy_id}/duplicate` | Duplicates a strategy into a new custom strategy. |
| GET | `/strategies/{strategy_id}/preview` | Previews stored strategy holdings and config. |
| POST | `/strategies/{strategy_id}/run` | Queues a strategy backtest with optional date overrides. |
| GET | `/strategies/{strategy_id}/runs` | Lists recent runs for a strategy. |
| GET | `/ingest/runs` | Returns recent ingestion runs. |
| GET | `/pipeline/runs` | Returns recent pipeline runs. |
| POST | `/pipeline` | Runs QC, factors, signals, directions, and a backtest in the background. |
| POST | `/backtests` | Queues a backtest and returns a job ID. |
| GET | `/jobs` | Lists background jobs. |
| GET | `/jobs/{job_id}` | Returns background job status. |
| POST | `/jobs/{job_id}/cancel` | Requests cooperative cancellation of a job. |
| GET | `/backtests` | Lists stored backtests. |
| GET | `/backtests/{id}` | Returns config, metrics, and reproducibility metadata. |
| GET | `/backtests/{id}/equity` | Returns an equity curve. |
| GET | `/backtests/{id}/research` | Returns IC, rank IC, and decile research rows. |
| GET | `/backtests/{id}/positions` | Returns the latest or selected-date positions. |
| GET | `/` | Serves the dashboard. |

Example:

```bash
curl http://localhost:8000/health
curl 'http://localhost:8000/prices/AAPL?start=2023-01-01&end=2023-12-31'
curl -X POST http://localhost:8000/backtests \
  -H 'content-type: application/json' \
  -d '{
    "signal_name": "momentum_reversal_liquidity",
    "mode": "long_only",
    "quantile": 0.1,
    "max_position_weight": 0.1,
    "rebalance_frequency": "weekly"
  }'
```

## 8. Database tables

The database is `data/store.duckdb`.

| Table | Contents |
|---|---|
| `raw_prices` | Provider price rows before QC. |
| `clean_prices` | Raw prices after removing error rows. |
| `corporate_actions` | Date, symbol, action, and value. |
| `qc_issues` | Individual warnings and errors. |
| `qc_summary` | Per-symbol coverage and issue counts. |
| `ingest_runs` | Ingestion run counts and errors. |
| `universe_membership` | Symbol membership with effective dates and tradability. |
| `factors` | Date/symbol/factor raw and standardized values. |
| `factor_stats` | Materialized per-factor coverage used by the dashboard and wizard. |
| `strategies` | Validated custom strategy definitions as JSON. |
| `strategy_runs` | Strategy backtest job state, run IDs, and errors. |
| `backtest_runs` | Run ID, JSON config, and JSON metrics. |

Per-run Parquet files are written under `data/results/<run_id>/`. Forecast and
Qlib artifacts live under `data/directions/` and `data/qlib/`.

## 9. Testing and CI

Run locally:

```bash
make test
make lint
```

The tests cover:

- hand-computed metrics
- transaction costs
- capped portfolio sizing
- weekly rebalance markers
- causal risk overlays and turnover accounting
- factor truncation/look-ahead invariance
- streaming factor and signal generation
- invalid OHLC and trading-day quality checks
- ingestion idempotency, parallelism, retries, and failure isolation with a stub provider
- null placeholder-session filtering and delisting-return grace periods
- multi-horizon forecast snapshots and symbol history
- strategy validation, persistence, previews, and built-in immutability
- API contracts, read-only mode, background jobs, and job cancellation

GitHub Actions runs the same test and lint commands on pushes and pull
requests. Production downloads are not used in tests; tests use synthetic
fixtures and provider stubs. Qlib tests skip when the optional stack is not
installed.

## 10. Important limitations

> **These are current blockers, not theoretical concerns:** the generated
> 5,126-symbol universe is still a static current-constituents snapshot, and
> free vendor data is not point-in-time. Do not interpret historical results as
> free of survivorship bias.

- The universe is a current-constituents list, so results carry survivorship
  bias. A production version needs point-in-time membership and delisted names
  from a vendor such as Tiingo, Polygon, Sharadar, or Norgate.
- Free vendor data is not point-in-time and may contain errors. Not every
  symbol in the generated directory snapshot will have a complete 2015-to-today
  history; recent IPOs and ticker changes reduce effective coverage.
- Corporate-action handling depends on what the provider returns. Verify split
  and dividend adjustment semantics before using results for investment
  decisions.
- The static-universe fallback records a survivorship-bias warning in run
  metadata; dated membership must be populated before the universe is
  point-in-time.
- Fundamentals are excluded because they require point-in-time data handling.
- Backtest results are research output and not evidence of future performance.
- The dashboard is a research terminal, not a production trading UI. It has no
  user accounts, no TLS, and only optional shared-API-key protection.
- A 5,000-name history is materially larger than the original 50-name sample:
  expect a multi-gigabyte `factors` table, longer factor and forecast builds,
  and more expensive full-history queries. The pipeline streams factor and
  signal computation and `make optimize` builds the indexes used by the
  dashboard; production use should add partitioning for deeper history.

## 11. Where to extend it

- **New market-data provider:** implement `HistoricalProvider`.
- **Point-in-time universe:** add effective membership dates and delisted
  securities to the universe/store layer.
- **New factor:** add a named calculation in `core.factors` and register its metadata.
- **New signal:** add a named definition or write the signal file contract.
- **New strategy:** use the Strategies wizard or add a built-in definition in `core.strategies`.
- **New forecast engine:** implement the engine seam in `core.directions` and `core.qlib_engine`.
- **New backtest engine:** consume clean prices and signal Parquet.
- **Further UI work:** add authentication, saved layouts, and scheduling on top
  of the existing dashboard and API routes.

## License

See [LICENSE](LICENSE).

## Production readiness

| Area | Status |
|---|---|
| Separate ingest/core environments | Done |
| File-based service boundary | Done |
| Raw Parquet and DuckDB research store | Done |
| QC, factors, signals, and t+1 backtesting | Done |
| Reproducibility metadata | Done |
| API key, background backtest jobs, and CORS controls | Done |
| Multi-horizon forecasts with native fallback | Done |
| Strategy wizard, built-ins, and persisted custom strategies | Done |
| Optional Qlib Alpha158/LightGBM forecasting | Done |
| Broad 5,126-symbol generated universe | Done |
| Research dashboard with live pipeline and backtest controls | Done |
| GitHub Actions test/lint workflow | Done |
| Point-in-time universe and delisted securities | **Blocked** |
| Licensed point-in-time market data | **Blocked** |
| Complete corporate-action/total-return validation | **Blocked** |
| Production container image digests and deployment target | **Blocked** |

The blocked data items require an owner decision about vendor, licensing,
history coverage, and deployment environment; the repository does not pretend
that a static current universe solves survivorship bias.

DuckDB allows only one writer per database. Do not run a pipeline writer and a
writable API against the same `DATA_ROOT` at the same time; start the API with
`API_READ_ONLY=1` while the pipeline owns the database.

## Open owner decisions

These are intentionally not guessed:

- Which licensed point-in-time market-data vendor and contract should be used?
- Which deployment target, hostname, TLS termination, and persistent-volume
  model should be supported?
- Should API authentication be a shared API key, an identity provider, or
  another mechanism?
- What QC error and ingestion-failure thresholds should page an operator?
- What total-return and delisting-return policy should be approved for the
  production research benchmark?
