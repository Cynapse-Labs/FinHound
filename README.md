# FinHound QuantPlatform

FinHound QuantPlatform is a small, file-oriented quantitative research platform
for US equities on daily bars. It demonstrates the complete research path:

1. Download historical prices.
2. Store the vendor response as raw Parquet.
3. Run market-calendar-aware quality checks.
4. Compute price-only cross-sectional factors.
5. Produce a named signal file.
6. Backtest the signal with next-session execution and transaction costs.
7. Query the resulting data through FastAPI and a browser dashboard.

The project is intentionally designed around a stable file boundary. The
ingestion environment and the research/API environment are separate Python
environments and communicate through the mounted `data/` directory.

## Status

This repository contains the working MVP foundation. The core pipeline, API,
tests, linting configuration, Dockerfiles, configuration files, and dashboard
entry point are implemented. The dashboard is currently a deliberately small
offline-friendly JSON viewer rather than a full Chart.js research application.
Some advanced reporting requested for a production research platform—such as
complete decile tables, rebalance calendars, and richer API response models—
remain future work and are listed under [Known limitations](#known-limitations).

## Architecture

```text
                         shared mounted data volume
                    ┌────────────────────────────────┐
                    │                                │
┌───────────────────▼────────────┐      ┌────────────▼─────────────────────┐
│ ingest container               │      │ core container                    │
│ Python 3.11                    │      │ Python 3.11                       │
│                                │      │                                   │
│ OpenBB + yfinance provider     │      │ DuckDB store                       │
│ retryable provider adapter     │      │ QC and clean price tables         │
│ incremental Parquet writer     │      │ factors and signal generation     │
│                                │      │ backtester                         │
└───────────────────┬────────────┘      │ FastAPI + static dashboard         │
                    │                   └──────────────────┬──────────────────┘
                    ▼                                      │
          data/raw/prices/                                  ▼
             Parquet files                         http://localhost:8000
```

The containers do not call each other over HTTP. Ingestion writes files; core
reads those files and writes its own database, signals, and result files.

## Repository layout

```text
.
├── config/
│   ├── settings.yaml          Signal and backtest configuration
│   └── universe.yaml          Symbols, sectors, tradability, start date
├── data/                       Runtime data; ignored by Git
├── services/
│   ├── ingest/
│   │   ├── Dockerfile
│   │   ├── requirements.txt    Ingest-only dependencies
│   │   ├── src/ingest/
│   │   └── tests/
│   └── core/
│       ├── Dockerfile
│       ├── requirements.txt    Core/API dependencies
│       ├── src/core/
│       ├── static/
│       └── tests/
├── docker-compose.yml
├── Makefile
├── pyproject.toml              Ruff configuration
└── README.md
```

## Requirements

- Python 3.11 or newer
- Docker Engine
- Docker Compose v2 for `docker compose ...`
- Internet access for the OpenBB/yfinance ingestion step

The dependency manifests use exact versions. Ingestion and core deliberately
have separate requirements files so OpenBB cannot leak into the core runtime.

## Quick start

### Local Python workflow

Create environments for the two services:

```bash
python3.11 -m venv .venv-core
python3.11 -m venv .venv-ingest
.venv-core/bin/pip install -r services/core/requirements.txt
.venv-ingest/bin/pip install -r services/ingest/requirements.txt
```

Run the complete local pipeline:

```bash
make ingest
make qc
make factors
make signal
make backtest
```

Or run all pipeline stages in order:

```bash
make all
```

Start the API locally after generating data:

```bash
PYTHONPATH=services/core/src DATA_ROOT=data \
  .venv-core/bin/uvicorn core.api:app --host 0.0.0.0 --port 8000
```

Open:

- Dashboard: <http://localhost:8000/>
- Swagger/OpenAPI: <http://localhost:8000/docs>

### Docker Compose workflow

Build both isolated images:

```bash
docker compose build
```

Start the core API:

```bash
docker compose up --build
```

The compose file mounts the repository `data/` directory into both services
and mounts `config/` read-only. The ingest service can be run on demand:

```bash
docker compose run --rm ingest
```

The core service serves port `8000`.

## Make commands

| Command | Purpose |
|---|---|
| `make build` | Build the Docker images. |
| `make ingest` | Download/incrementally update raw vendor data. |
| `make qc` | Load raw prices, run QC, and write clean prices and summaries. |
| `make factors` | Compute and persist factor observations. |
| `make signal` | Generate the configured named signal Parquet file. |
| `make backtest` | Generate the signal if needed and run the default backtest. |
| `make all` | Run ingestion, QC, factors, signal, and backtest in order. |
| `make test` | Run the pytest suite. |
| `make lint` | Run Ruff lint and formatting checks. |
| `make up` | Build and start the Docker Compose application. |

For local commands, `PYTHON` can be overridden:

```bash
make test PYTHON=.venv-core/bin/python
```

## Configuration

### Universe

`config/universe.yaml` contains the current default universe:

- 50 large-cap US equity symbols across multiple sectors.
- `SPY` as a benchmark.
- `SPY` is marked `tradable: false`.
- Default history start date: `2015-01-01`.

The universe is a current-constituents list, not a point-in-time historical
membership database.

Each symbol entry has this shape:

```yaml
- symbol: AAPL
  sector: Technology
  tradable: true
```

### Settings

`config/settings.yaml` controls paths, signals, factor weights, liquidity
filtering, and default costs:

```yaml
signal:
  default: quality_momentum
  definitions:
    quality_momentum:
      weights:
        momentum_12_1: 0.4
        reversal: 0.1
        volatility: 0.2
        distance_high: 0.1
        dollar_volume: 0.2
      liquidity_min_dollar_volume: 10000000
backtest:
  commission_bps: 1.0
  slippage_bps: 2.0
  borrow_bps: 50.0
```

The signal definition supports multiple named signals. Add another definition
under `signal.definitions`, then generate it by passing its name to the signal
module or by writing a compatible Parquet file.

## Data lifecycle

### 1. Ingestion

The ingest service uses OpenBB with the yfinance provider. For each symbol it:

- Reads the latest stored date, if any.
- Requests only dates after that date.
- Normalizes the provider response to include `symbol`, `provider`,
  `fetched_at`, and `adjusted`.
- Removes duplicate `(symbol, date)` rows.
- Writes Parquet under:

```text
data/raw/prices/symbol=<SYMBOL>/data.parquet
```

An ingestion run also writes:

```text
data/ingest_runs.parquet
```

Failures are isolated per symbol and recorded rather than aborting the entire
run. The provider adapter is represented by the `HistoricalProvider` protocol,
which makes it possible to use a deterministic stub in tests or replace the
vendor later.

### 2. Store and quality control

The core service reads raw Parquet and creates `data/store.duckdb`. QC uses the
NYSE calendar from `pandas-market-calendars`.

Checks include:

- Missing trading days.
- Duplicate symbol/date rows.
- Non-positive prices.
- High below low.
- Open or close outside the daily high/low range.
- Absolute daily close return above 40%.
- Three or more consecutive zero-volume sessions.
- Stale series whose last observation is more than five trading days old.

Issues have `error` or `warn` severity. Error rows are excluded from
`clean_prices`; warning rows remain available for research and are reported in
the summary.

### 3. Factors

Factors are calculated per symbol using data available through the close of
the factor date:

| Factor | Definition |
|---|---|
| `momentum_12_1` | 252-session return ending 21 sessions before the current date. |
| `reversal` | Negative 21-session return. |
| `volatility` | Negative 60-session realized volatility, annualized. |
| `distance_high` | Current close divided by the rolling 252-session high minus one. |
| `dollar_volume` | 20-session average of close multiplied by volume. |

Every factor stores both the raw value and a cross-sectional standardized value.
Standardization winsorizes observations at the 1st and 99th percentiles and
then applies a z-score independently for each date and factor.

### 4. Signals

The configured signal is a weighted sum of standardized factors. The
dollar-volume factor also supplies the configured liquidity filter.

Signal files are written to:

```text
data/signals/<name>.parquet
```

## Signal file contract

The signal contract is intentionally small so future signal producers can be
implemented in another language or repository.

The file must contain exactly these columns:

| Column | Type | Meaning |
|---|---|---|
| `date` | `date` | Trading session on which the signal is observed. |
| `symbol` | `string` | Equity symbol. |
| `score` | `float64` | Cross-sectional score; higher is better. |

There must be no duplicate `(date, symbol)` keys. A score observed at the close
of `t` is not tradable at that same close. The backtester shifts the portfolio
weights by one session and executes at the close of `t+1`.

## Backtesting

The backtester:

1. Pivots clean close prices and the signal file.
2. Selects the top quantile for long-only mode.
3. Selects the top and bottom quantiles for long/short mode.
4. Applies equal weighting and a maximum individual position cap.
5. Shifts target weights by one session.
6. Calculates close-to-close portfolio returns.
7. Applies commission, slippage, and annualized short borrow costs.
8. Compares the strategy with SPY and an equal-weight tradable universe.
9. Writes an equity curve and positions for the generated run ID.

The API accepts this configuration:

```json
{
  "signal_name": "quality_momentum",
  "mode": "long_only",
  "quantile": 0.1,
  "max_position_weight": 0.1,
  "commission_bps": 1.0,
  "slippage_bps": 2.0,
  "borrow_bps": 50.0,
  "start_date": "2018-01-01",
  "end_date": "2024-12-31",
  "split_date": "2021-01-01"
}
```

Backtest results are persisted in both places:

```text
data/store.duckdb
data/results/<run_id>/equity.parquet
data/results/<run_id>/positions.parquet
```

The equity file contains strategy equity, SPY equity, equal-weight equity,
daily return, and drawdown. The positions file contains date, symbol, and
executed weight.

## DuckDB schema

The database is created automatically at `data/store.duckdb`.

### `raw_prices`

Raw ingested price observations:

```text
date DATE
symbol VARCHAR
open DOUBLE
high DOUBLE
low DOUBLE
close DOUBLE
volume DOUBLE
provider VARCHAR
fetched_at TIMESTAMP
adjusted BOOLEAN
```

### `clean_prices`

The same price shape after QC error rows have been excluded.

### `corporate_actions`

Reserved for provider-returned actions:

```text
date DATE
symbol VARCHAR
action VARCHAR
value DOUBLE
```

### `qc_issues`

One row per detected issue:

```text
date DATE
symbol VARCHAR
check_name VARCHAR
severity VARCHAR
detail VARCHAR
```

### `qc_summary`

Per-symbol coverage and issue counts:

```text
symbol VARCHAR
first_date DATE
last_date DATE
expected_days INTEGER
missing_days INTEGER
error_count INTEGER
warn_count INTEGER
```

### `ingest_runs`

Run-level ingestion metadata:

```text
run_id VARCHAR
started_at TIMESTAMP
rows INTEGER
failures INTEGER
errors VARCHAR
```

### `factors`

Long-form factor observations:

```text
date DATE
symbol VARCHAR
factor VARCHAR
raw_value DOUBLE
standardized DOUBLE
```

The intended key is `(date, symbol, factor)`.

### `backtest_runs`

Backtest metadata and JSON payloads:

```text
run_id VARCHAR
created_at TIMESTAMP
config VARCHAR
metrics VARCHAR
```

## API

The FastAPI application is in `services/core/src/core/api.py`.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Service health check. |
| GET | `/universe` | Symbols present in clean prices. |
| GET | `/prices/{symbol}` | Clean prices, optionally filtered by `start` and `end`. |
| GET | `/qc/summary` | Per-symbol QC coverage summary. |
| GET | `/qc/issues` | QC issues, optionally filtered by `severity`. |
| GET | `/factors/{symbol}` | Factor history, optionally filtered by factor name. |
| GET | `/signals` | Names of available signal files. |
| GET | `/signals/{name}` | Rows from a named signal file. |
| POST | `/backtests` | Run a backtest and return its run ID. |
| GET | `/backtests` | List persisted backtest runs. |
| GET | `/backtests/{id}` | Return one run's config and metrics. |
| GET | `/backtests/{id}/equity` | Return the persisted equity curve. |
| GET | `/` | Serve the dashboard page. |

Example:

```bash
curl http://localhost:8000/health
curl 'http://localhost:8000/prices/AAPL?start=2023-01-01&end=2023-12-31'
curl -X POST http://localhost:8000/backtests \
  -H 'content-type: application/json' \
  -d '{"signal_name":"quality_momentum","mode":"long_only","quantile":0.1}'
```

Interactive API documentation is available at `/docs`; the generated OpenAPI
schema is available at `/openapi.json`.

## Dashboard

The dashboard is served directly by FastAPI from
`services/core/static/index.html`. It requires no separate frontend build and
works with the same API process. The current page provides navigation for:

- Data health and QC summary.
- Factor history for a sample symbol.
- Available signals.
- Persisted backtest runs.

The static page is intentionally minimal at this MVP stage. A full offline
Chart.js dashboard can be added without changing the backend contracts.

## Testing

Run the tests and lint checks:

```bash
make test
make lint
```

The current tests cover:

- Hand-checked return metrics.
- Transaction cost reduction.
- Factor truncation/look-ahead invariance for prior dates.
- Invalid OHLC QC detection and clean-row exclusion.
- Ingestion idempotency with a stubbed provider.

Tests use synthetic data only. Production application code does not embed fake
market data.

## Design and correctness notes

### Look-ahead bias

Factor rolling windows use only current and prior observations. Momentum skips
the most recent 21 sessions. The backtester shifts target weights one session
before applying returns. This means a score computed at the close of `t` can
only affect performance starting with the close-to-close return for `t+1`.

### Idempotency

Ingestion reads the latest stored date, fetches only later dates, and removes
duplicate symbol/date keys before writing. Re-running ingestion for an already
complete date range should not add duplicate observations.

### File-only service boundary

Ingestion and core do not share an interpreter or database connection. Their
interface is raw Parquet plus the documented signal contract. This allows a
future ingestion provider or signal producer to be deployed independently.

## Known limitations

- The universe is a current-constituents list, so results carry survivorship
  bias.
- Free vendor data is not point-in-time and may contain errors.
- Fundamentals are excluded because they need point-in-time handling.
- Backtest results are research output and not evidence of future performance.
- The current dashboard is a compact JSON-oriented MVP view, not yet the full
  four-tab Chart.js interface.
- Rebalance frequency configuration is not yet exposed as a complete weekly or
  monthly scheduling model.
- Decile spread tables, full period/in-sample/out-of-sample metric presentation,
  and richer typed API response envelopes are not yet complete.
- Corporate-action persistence depends on the provider returning action data;
  the current basic ingestion path primarily handles daily price records.
- The free vendor path requires network access and can be rate-limited or
  temporarily unavailable.
- Docker Compose v2 is required for the documented `docker compose` commands.

## Extension points

The following boundaries are intended to remain stable:

1. Implement another `HistoricalProvider` for a different market-data vendor.
2. Add point-in-time universe membership and fundamentals without changing the
   signal file contract.
3. Add Qlib or Lean adapters that consume clean prices and signal Parquet.
4. Add FinanceToolkit-derived analytics in the core environment.
5. Add another signal producer that writes
   `data/signals/<name>.parquet`.
6. Replace the static page with a richer frontend while preserving the API.

## License

See [LICENSE](LICENSE).
