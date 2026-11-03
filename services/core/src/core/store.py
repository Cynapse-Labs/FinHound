from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import structlog
import yaml

log = structlog.get_logger()


class Store:
    def __init__(self, root: str | Path | None = None, read_only: bool = False) -> None:
        root = root or os.getenv("DATA_ROOT", "/data")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
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
            CREATE TABLE IF NOT EXISTS factor_stats(
              factor VARCHAR PRIMARY KEY, rows BIGINT, symbols BIGINT, first_date DATE, last_date DATE, updated_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS backtest_runs(run_id VARCHAR PRIMARY KEY, created_at TIMESTAMP, config VARCHAR,
              metrics VARCHAR);
            CREATE TABLE IF NOT EXISTS universe_membership(
              symbol VARCHAR, start_date DATE, end_date DATE, index_name VARCHAR, tradable BOOLEAN
            );
            CREATE TABLE IF NOT EXISTS strategies(
              id VARCHAR PRIMARY KEY, name VARCHAR, definition VARCHAR, builtin BOOLEAN,
              created_at TIMESTAMP, updated_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS strategy_runs(
              job_id VARCHAR PRIMARY KEY, strategy_id VARCHAR, run_id VARCHAR, status VARCHAR,
              created_at TIMESTAMP, finished_at TIMESTAMP, error VARCHAR
            );
            """
        ).fetchall()
        columns = {row[1] for row in self.db.execute("PRAGMA table_info('backtest_runs')").fetchall()}
        for name in ("git_sha", "config_hash", "data_snapshot", "library_versions", "strategy_id", "strategy_name"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE backtest_runs ADD COLUMN {name} VARCHAR")
        universe_types = {row[1]: str(row[2]).upper() for row in self.db.execute("PRAGMA table_info('universe_membership')").fetchall()}
        if universe_types.get("start_date") != "DATE" or universe_types.get("end_date") != "DATE":
            self.db.execute("DROP TABLE universe_membership").fetchall()
            self.db.execute(
                """
                CREATE TABLE universe_membership(
                  symbol VARCHAR, start_date DATE, end_date DATE, index_name VARCHAR, tradable BOOLEAN
                )
                """
            ).fetchall()
        self._load_static_universe()

    def _load_static_universe(self) -> None:
        config_path = Path(os.getenv("UNIVERSE_PATH", "/config/universe.yaml"))
        if not config_path.exists():
            return
        # A curated/dated membership table is authoritative. When only the
        # generated static snapshot is present, reconcile it on startup so a
        # refreshed universe.yaml takes effect without dropping the database.
        custom = self.db.execute("SELECT count(*) FROM universe_membership WHERE index_name <> 'static_current_constituents'").fetchall()[
            0
        ][0]
        if custom:
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
        with self._lock:
            self.db.execute("DELETE FROM universe_membership WHERE index_name = 'static_current_constituents'").fetchall()
        self.write_table(pd.DataFrame(rows), "universe_membership", replace=False)

    def refresh_factor_stats(self) -> None:
        """Materialize per-factor coverage so the dashboard avoids full scans."""
        with self._lock:
            self.db.execute("DELETE FROM factor_stats").fetchall()
            self.db.execute(
                """
                INSERT INTO factor_stats
                SELECT factor, count(*) AS rows, count(DISTINCT symbol) AS symbols,
                       min(date) AS first_date, max(date) AS last_date, now() AS updated_at
                FROM factors GROUP BY factor
                """
            ).fetchall()

    def factor_coverage(self) -> pd.DataFrame:
        """Return per-factor coverage, using the materialized table when present."""
        frame = self.query("SELECT factor, rows, symbols, first_date, last_date FROM factor_stats")
        if frame.empty:
            frame = self.query(
                "SELECT factor, count(*) AS rows, count(DISTINCT symbol) AS symbols, "
                "min(date) AS first_date, max(date) AS last_date FROM factors GROUP BY factor"
            )
        return frame

    def optimize(self) -> None:
        """Build the indexes that keep broad-universe dashboard queries usable.

        `factors` already carries a primary-key index whose leading column is
        `date`, so latest-cross-section scans use it. These indexes cover the
        per-factor and per-symbol history lookups that would otherwise be full
        table scans at 5,000 symbols.
        """
        statements = (
            "CREATE INDEX IF NOT EXISTS idx_clean_prices_symbol_date ON clean_prices(symbol, date)",
            "CREATE INDEX IF NOT EXISTS idx_universe_symbol ON universe_membership(symbol)",
            "CREATE INDEX IF NOT EXISTS idx_factors_factor_date ON factors(factor, date)",
            "CREATE INDEX IF NOT EXISTS idx_factors_symbol_date ON factors(symbol, date)",
        )
        with self._lock:
            for statement in statements:
                started = time.perf_counter()
                self.db.execute(statement).fetchall()
                log.info("index_ready", statement=statement, elapsed=round(time.perf_counter() - started, 1))
            self.db.execute("ANALYZE").fetchall()
            log.info("store_optimized")

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
        with self._lock:
            self.db.register("incoming", frame)
            try:
                self.db.execute(
                    f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM incoming"
                    if replace
                    else f"INSERT INTO {table} SELECT * FROM incoming"
                ).fetchall()
            finally:
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
        with self._lock:
            self.db.execute(
                """INSERT INTO backtest_runs
                (run_id, created_at, config, metrics, git_sha, config_hash, data_snapshot, library_versions,
                 strategy_id, strategy_name)
                VALUES (?, current_timestamp, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    run_id,
                    json.dumps(config, default=str),
                    json.dumps(metrics, default=str),
                    *(metadata or self.reproducibility_metadata(config)).values(),
                    config.get("strategy_id"),
                    config.get("strategy_name"),
                ],
            ).fetchall()
        out = self.root / "results" / run_id
        out.mkdir(parents=True, exist_ok=True)
        equity.to_parquet(out / "equity.parquet", index=False)
        positions.to_parquet(out / "positions.parquet", index=False)
        if research is not None:
            research.to_parquet(out / "research.parquet", index=False)

    def new_run_id(self) -> str:
        return uuid.uuid4().hex

    def query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        with self._lock:
            return self.db.execute(sql, params or []).fetchdf()

    def list_custom_strategies(self) -> list[dict[str, Any]]:
        frame = self.query("SELECT * FROM strategies WHERE builtin = false ORDER BY updated_at DESC")
        strategies: list[dict[str, Any]] = []
        for row in frame.to_dict(orient="records"):
            try:
                definition = json.loads(row["definition"])
            except (TypeError, ValueError):
                continue
            definition["builtin"] = False
            definition["created_at"] = str(row.get("created_at") or "")
            definition["updated_at"] = str(row.get("updated_at") or "")
            strategies.append(definition)
        return strategies

    def get_custom_strategy(self, strategy_id: str) -> dict[str, Any] | None:
        frame = self.query("SELECT * FROM strategies WHERE id = ? AND builtin = false", [strategy_id])
        if frame.empty:
            return None
        row = frame.iloc[0]
        try:
            definition = json.loads(row["definition"])
        except (TypeError, ValueError):
            return None
        definition["builtin"] = False
        definition["created_at"] = str(row.get("created_at") or "")
        definition["updated_at"] = str(row.get("updated_at") or "")
        return definition

    def save_custom_strategy(self, definition: dict[str, Any]) -> None:
        payload = {key: value for key, value in definition.items() if key not in {"created_at", "updated_at", "builtin"}}
        with self._lock:
            self.db.execute(
                """
                INSERT INTO strategies (id, name, definition, builtin, created_at, updated_at)
                VALUES (?, ?, ?, false, now(), now())
                ON CONFLICT (id) DO UPDATE SET
                  name = excluded.name,
                  definition = excluded.definition,
                  updated_at = now()
                """,
                [definition["id"], definition.get("name", definition["id"]), json.dumps(payload, default=str)],
            ).fetchall()

    def delete_custom_strategy(self, strategy_id: str) -> bool:
        with self._lock:
            existing = self.db.execute("SELECT count(*) FROM strategies WHERE id = ? AND builtin = false", [strategy_id]).fetchone()[0]
            if not existing:
                return False
            self.db.execute("DELETE FROM strategies WHERE id = ? AND builtin = false", [strategy_id]).fetchall()
        return True

    def record_strategy_run(
        self,
        job_id: str,
        strategy_id: str,
        status: str,
        run_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self.db.execute(
                """
                INSERT INTO strategy_runs (job_id, strategy_id, run_id, status, created_at, finished_at, error)
                VALUES (?, ?, ?, ?, now(), now(), ?)
                ON CONFLICT (job_id) DO UPDATE SET
                  run_id = excluded.run_id, status = excluded.status,
                  finished_at = now(), error = excluded.error
                """,
                [job_id, strategy_id, run_id, status, error],
            ).fetchall()
