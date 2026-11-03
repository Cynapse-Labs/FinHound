# FinHound QuantPlatform

## Architecture and data flow

```text
OpenBB/yfinance -> ingest container -> data/raw/*.parquet
                                      |
                         shared volume (files only)
                                      v
                 core: DuckDB -> QC -> factors -> signals -> backtests
                                      |
                              FastAPI + dashboard
```

The ingest image has its own Python environment and only writes raw Parquet and an
ingest run log. The core image reads those files, stores queryable tables in
`data/store.duckdb`, and serves the API on port 8000.

Run `make build`, then `make all`, or use `make up` to start the API container.
The default universe is a current-constituents list of 50 large-cap US equities
plus non-tradable SPY. `make test` and `make lint` run the checks.

## Signal file contract

Signal producers and consumers exchange `data/signals/<name>.parquet`. It must
contain exactly three columns: `date` (`date`), `symbol` (`string`), and
`score` (`float64`), with at most one row per symbol/date. A signal observed at
the close of date `t` is executed at the close of `t+1`; this lag is enforced by
the backtester.

## Tables

`raw_prices` and `clean_prices` contain date, symbol, OHLCV, provider,
fetched_at, and adjusted. `corporate_actions` contains date, symbol, action,
and value. `qc_issues` contains date, symbol, check_name, severity, and detail.
`qc_summary` contains per-symbol coverage and issue counts. `ingest_runs`
contains run metadata. `factors` is keyed by date/symbol/factor and stores
raw_value and standardized. `backtest_runs` stores run id, timestamp, full JSON
config, and JSON metrics. Per-run equity and positions are Parquet under
`data/results/<run_id>/`.

## Known limitations

- The universe is a current-constituents list, so results carry survivorship bias.
- Free vendor data is not point-in-time and may contain errors.
- Fundamentals are excluded because they need point-in-time handling.
- Backtest results are research output and not evidence of future performance.

Extension points are the `HistoricalProvider` protocol, the signal Parquet
contract, and the DuckDB tables: Qlib, Lean, FinanceToolkit, and a second signal
producer can be added without changing the file boundary.
