# Operations runbook

## Nightly run failed

1. Inspect the ingest container logs: `docker compose logs ingest`.
2. Check `data/ingest_runs.parquet` for symbol-level failures.
3. Retry `docker compose run --rm ingest`.
4. Re-run `make qc`, `make factors`, `make signal`, and `make backtest`.

## QC threshold or bad data

Inspect `GET /qc/summary` and `GET /qc/issues?severity=error`. Do not delete
raw Parquet. Correct the provider/configuration issue, then rebuild clean tables
from raw data with `make qc`.

## Provider rate limiting

Stop repeated retries, increase the configured request interval, and retry the
failed ingest later. The provider adapter isolates failures by symbol.

## API unhealthy

Check `curl http://localhost:8000/health`, then inspect
`docker compose logs core`. Restart the core container without deleting
`data/store.duckdb`.

## Rebuild from raw Parquet

The raw source of truth is `data/raw/prices/`. Remove only derived outputs
(`data/store.duckdb`, `data/signals/`, and `data/results/`) and run:

```bash
make qc
make factors
make signal
make backtest
```

Never remove raw files unless the source data has been independently archived.
