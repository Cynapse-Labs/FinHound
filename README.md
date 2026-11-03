# FinHound QuantPlatform

FinHound is a small quantitative-research platform for US equities on daily
bars. It runs the same basic workflow used by a larger research system:

```text
prices -> quality checks -> factors -> signal -> backtest -> API/dashboard
```

The project is an MVP. It is useful for understanding the pipeline and for
testing research ideas, but its results are not investment advice or evidence
of future performance.

## 1. What is included?

- **Ingestion:** OpenBB with the yfinance provider.
- **Storage:** raw Parquet files plus a DuckDB research database.
- **Quality control:** NYSE calendar checks and invalid-row filtering.
- **Factors:** momentum, reversal, volatility, distance from 52-week high, and
  average dollar volume.
- **Signals:** named, configurable weighted factor files.
- **Backtests:** long-only or long/short, capped equal weights, weekly/monthly
  rebalancing, costs, SPY and equal-weight benchmarks.
- **API:** FastAPI on port 8000.
- **Dashboard:** a small static browser UI served by FastAPI.
- **Tests and CI:** pytest, Ruff, and GitHub Actions.

The ingest and core services have separate dependency environments. They share
only files in `data/`; they do not share a Python environment or communicate
with each other over HTTP.

## 2. Start here

### Requirements

- Python 3.11
- Docker Engine and Docker Compose v2
- Internet access for the yfinance download

### Run locally: one setup command

From the repository root, run:

```bash
make setup
make all
```

`make setup` creates the two required virtual environments and installs the
exact pinned dependencies. `make all` runs ingestion, QC, factors, signal
generation, and backtesting in order.

Start the API in a second terminal:

```bash
PYTHONPATH=services/core/src DATA_ROOT=data \
  .venv-core/bin/uvicorn core.api:app --host 0.0.0.0 --port 8000
```

Then open:

- <http://localhost:8000/> — dashboard
- <http://localhost:8000/docs> — interactive API documentation
- <http://localhost:8000/openapi.json> — OpenAPI schema

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
| `make signal` | Create the configured signal Parquet file. |
| `make backtest` | Run the default signal backtest. |
| `make all` | Run all five stages in order. |
| `make test` | Run pytest. |
| `make lint` | Run Ruff lint and formatting checks. |
| `make build` | Build Docker images. |
| `make up` | Start the Docker Compose application. |
| `make setup` | Create both local virtual environments and install dependencies. |
| `make local` | Set up, run the full local pipeline, and start the API. |

Generated data is ignored by Git:

```text
data/
├── raw/prices/symbol=<SYMBOL>/data.parquet
├── ingest_runs.parquet
├── store.duckdb
├── signals/<name>.parquet
└── results/<run_id>/
    ├── equity.parquet
    └── positions.parquet
```

## 4. Configuration

### Universe

`config/universe.yaml` contains 50 current large-cap symbols plus `SPY`.
`SPY` is a benchmark and is marked non-tradable. The default start date is
`2015-01-01`.

Each entry has:

```yaml
- symbol: AAPL
  sector: Technology
  tradable: true
```

This is **not** a point-in-time historical universe. It is a current list
applied backward through history, so backtests have survivorship bias.

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

Backtest defaults are also in this file:

```yaml
backtest:
  rebalance_frequency: weekly
  commission_bps: 1.0
  slippage_bps: 2.0
  borrow_bps: 50.0
```

## 5. How the pipeline works

### Step 1: ingest prices

The ingest service uses OpenBB/yfinance and writes provider data to raw
Parquet. It adds:

- `symbol`
- `provider`
- `fetched_at`
- `adjusted`

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

Factors use only prices available through the close of the factor date:

| Factor | Calculation |
|---|---|
| `momentum_12_1` | 252-session return, skipping the most recent 21 sessions. |
| `reversal` | Negative 21-session return. |
| `volatility` | Negative 60-session realized volatility. |
| `distance_high` | Close divided by 252-session high minus one. |
| `dollar_volume` | 20-session average of close × volume. |

Each factor is stored in long format with:

- `raw_value`
- cross-sectional `standardized` value

Standardization winsorizes at the 1st and 99th percentiles and then z-scores
each date/factor cross-section.

### Step 4: generate a signal

The signal is a weighted sum of standardized factors. Dollar volume is also
used as a minimum liquidity filter.

Output:

```text
data/signals/<name>.parquet
```

### Step 5: backtest

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
| GET | `/universe` | Lists symbols in clean prices. |
| GET | `/prices/{symbol}` | Returns prices; supports `start` and `end`. |
| GET | `/qc/summary` | Returns per-symbol coverage and issue counts. |
| GET | `/qc/issues` | Returns QC issues; supports `severity`. |
| GET | `/factors/{symbol}` | Returns factor history; supports `factor`. |
| GET | `/signals` | Lists generated signal names. |
| GET | `/signals/{name}` | Returns one signal file. |
| POST | `/backtests` | Runs a backtest and returns a run ID. |
| GET | `/backtests` | Lists stored backtests. |
| GET | `/backtests/{id}` | Returns config and metrics for one run. |
| GET | `/backtests/{id}/equity` | Returns an equity curve. |
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
| `factors` | Date/symbol/factor raw and standardized values. |
| `backtest_runs` | Run ID, JSON config, and JSON metrics. |

Per-run Parquet files are written under `data/results/<run_id>/`.

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
- factor truncation/look-ahead invariance
- invalid OHLC quality checks
- ingestion idempotency with a stub provider

GitHub Actions runs the same test and lint commands on pushes and pull
requests. Production downloads are not used in tests; tests use synthetic
fixtures and provider stubs.

## 10. Important limitations

> **These are current blockers, not theoretical concerns:** the universe is
> still a static current-constituents list, free vendor data is not
> point-in-time, and the 50-name universe is small for cross-sectional
> statistics. Do not interpret historical results as free of survivorship bias.

- The universe is a current-constituents list, so results carry survivorship
  bias. A production version needs point-in-time membership and delisted names
  from a vendor such as Tiingo, Polygon, Sharadar, or Norgate.
- Free vendor data is not point-in-time and may contain errors.
- Corporate-action handling depends on what the provider returns. Verify split
  and dividend adjustment semantics before using results for investment
  decisions.
- The static-universe fallback records a survivorship-bias warning in run
  metadata; dated membership must be populated before the universe is
  point-in-time.
- Fundamentals are excluded because they require point-in-time data handling.
- Backtest results are research output and not evidence of future performance.
- The default dashboard is intentionally small; it provides data-health,
  factor, signal-research, and backtest views but is not a production terminal.
- A 50-name cross-section is small. IC and factor z-scores are more stable
  with a broader, point-in-time universe.

## 11. Where to extend it

- **New market-data provider:** implement `HistoricalProvider`.
- **Point-in-time universe:** add effective membership dates and delisted
  securities to the universe/store layer.
- **New factor:** add a column calculation in `core.factors`.
- **New signal:** add a named definition or write the signal file contract.
- **New backtest engine:** consume clean prices and signal Parquet.
- **Qlib/Lean/FinanceToolkit:** integrate at the clean-price or signal-file
  boundary instead of coupling to ingestion.
- **Richer UI:** keep the existing API routes and replace the static page.

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
| GitHub Actions test/lint workflow | Done |
| Point-in-time universe and delisted securities | **Blocked** |
| Licensed point-in-time market data | **Blocked** |
| Complete corporate-action/total-return validation | **Blocked** |
| Production container image digests and deployment target | **Blocked** |

The blocked data items require an owner decision about vendor, licensing,
history coverage, and deployment environment; the repository does not pretend
that a static current universe solves survivorship bias.

The API defaults to `API_READ_ONLY=1`, so it opens DuckDB read-only while a
pipeline process owns writes. The pipeline should publish a completed database
before restarting the API against it; this avoids concurrent DuckDB writers.
Set `API_READ_ONLY=0` only for local initialization or development.

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
