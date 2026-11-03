from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


class Store:
    def __init__(self, root: str | Path | None = None) -> None:
        root = root or os.getenv("DATA_ROOT", "/data")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(self.root / "store.duckdb"))
        self.init_schema()

    def init_schema(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_prices AS
            SELECT * FROM (SELECT CAST(NULL AS DATE) date, CAST(NULL AS VARCHAR) symbol,
            CAST(NULL AS DOUBLE) open, CAST(NULL AS DOUBLE) high, CAST(NULL AS DOUBLE) low,
            CAST(NULL AS DOUBLE) "close", CAST(NULL AS DOUBLE) volume, CAST(NULL AS VARCHAR) provider,
            CAST(NULL AS TIMESTAMP) fetched_at, CAST(NULL AS BOOLEAN) adjusted) WHERE false;
            CREATE TABLE IF NOT EXISTS clean_prices AS SELECT * FROM raw_prices;
            CREATE TABLE IF NOT EXISTS corporate_actions(date DATE, symbol VARCHAR, action VARCHAR, value DOUBLE);
            CREATE TABLE IF NOT EXISTS qc_issues(date DATE, symbol VARCHAR, check_name VARCHAR, severity VARCHAR, detail VARCHAR);
            CREATE TABLE IF NOT EXISTS qc_summary(symbol VARCHAR PRIMARY KEY, first_date DATE, last_date DATE,
              expected_days INTEGER, missing_days INTEGER, error_count INTEGER, warn_count INTEGER);
            CREATE TABLE IF NOT EXISTS ingest_runs(run_id VARCHAR, started_at TIMESTAMP, rows INTEGER, failures INTEGER, errors VARCHAR);
            CREATE TABLE IF NOT EXISTS factors(date DATE, symbol VARCHAR, factor VARCHAR, raw_value DOUBLE, standardized DOUBLE,
              PRIMARY KEY(date, symbol, factor));
            CREATE TABLE IF NOT EXISTS backtest_runs(run_id VARCHAR PRIMARY KEY, created_at TIMESTAMP, config VARCHAR,
              metrics VARCHAR);
            """
        )

    def load_raw(self) -> pd.DataFrame:
        files = list((self.root / "raw" / "prices").glob("symbol=*/*.parquet"))
        if not files:
            return pd.DataFrame()
        frame = pd.concat((pd.read_parquet(p) for p in files), ignore_index=True)
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        return frame.drop_duplicates(["symbol", "date"])

    def write_table(self, frame: pd.DataFrame, table: str, replace: bool = True) -> None:
        self.db.register("incoming", frame)
        self.db.execute(
            f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM incoming" if replace else f"INSERT INTO {table} SELECT * FROM incoming"
        )
        self.db.unregister("incoming")

    def save_backtest(
        self,
        run_id: str,
        config: dict[str, Any],
        metrics: dict[str, Any],
        equity: pd.DataFrame,
        positions: pd.DataFrame,
        research: pd.DataFrame | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO backtest_runs VALUES (?, current_timestamp, ?, ?)",
            [run_id, json.dumps(config, default=str), json.dumps(metrics, default=str)],
        )
        out = self.root / "results" / run_id
        out.mkdir(parents=True, exist_ok=True)
        equity.to_parquet(out / "equity.parquet", index=False)
        positions.to_parquet(out / "positions.parquet", index=False)
        if research is not None:
            research.to_parquet(out / "research.parquet", index=False)

    def new_run_id(self) -> str:
        return uuid.uuid4().hex

    def query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        return self.db.execute(sql, params or []).fetchdf()
