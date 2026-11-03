from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml


class Store:
    def __init__(self, root: str | Path | None = None, read_only: bool = False) -> None:
        root = root or os.getenv("DATA_ROOT", "/data")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(self.root / "store.duckdb"), read_only=read_only)
        if not read_only:
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
            CREATE TABLE IF NOT EXISTS universe_membership(
              symbol VARCHAR, start_date DATE, end_date DATE, index_name VARCHAR, tradable BOOLEAN
            );
            """
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info('backtest_runs')").fetchall()}
        for name in ("git_sha", "config_hash", "data_snapshot", "library_versions"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE backtest_runs ADD COLUMN {name} VARCHAR")
        self._load_static_universe()

    def _load_static_universe(self) -> None:
        config_path = Path(os.getenv("UNIVERSE_PATH", "/config/universe.yaml"))
        if self.db.execute("SELECT count(*) FROM universe_membership").fetchone()[0] or not config_path.exists():
            return
        config = yaml.safe_load(config_path.read_text())
        rows = [
            {
                "symbol": item["symbol"],
                "start_date": config.get("start_date", "1900-01-01"),
                "end_date": None,
                "index_name": "static_current_constituents",
                "tradable": bool(item.get("tradable", True)),
            }
            for item in config["symbols"]
        ]
        self.write_table(pd.DataFrame(rows), "universe_membership")

    def reproducibility_metadata(self, config: dict[str, Any]) -> dict[str, str]:
        try:
            git_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.root.parent, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            git_sha = "unknown"
        clean = self.query("SELECT count(*) AS rows, max(fetched_at) AS max_fetched_at, max(date) AS max_date FROM clean_prices")
        snapshot = json.dumps(clean.iloc[0].to_dict(), default=str, sort_keys=True)
        versions = {}
        for package in ("duckdb", "pandas", "numpy", "pyarrow", "fastapi"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "unknown"
        return {
            "git_sha": git_sha,
            "config_hash": hashlib.sha256(json.dumps(config, default=str, sort_keys=True).encode()).hexdigest(),
            "data_snapshot": hashlib.sha256(snapshot.encode()).hexdigest(),
            "library_versions": json.dumps(versions, sort_keys=True),
        }

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
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO backtest_runs
            (run_id, created_at, config, metrics, git_sha, config_hash, data_snapshot, library_versions)
            VALUES (?, current_timestamp, ?, ?, ?, ?, ?, ?)""",
            [
                run_id,
                json.dumps(config, default=str),
                json.dumps(metrics, default=str),
                *(metadata or self.reproducibility_metadata(config)).values(),
            ],
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
