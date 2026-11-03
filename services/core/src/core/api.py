from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import pandas as pd
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .analytics import drawdown_episodes, performance_metrics, rolling_risk
from .backtest import run_backtest
from .directions import (
    HORIZONS as DIRECTION_HORIZONS,
)
from .directions import (
    build_directions,
    directions_meta,
    symbol_history,
)
from .directions import (
    snapshot as directions_snapshot,
)
from .factors import compute_factors, factor_catalog
from .gamma import gamma_exposure, gamma_heatmap
from .qc import run_qc
from .qlib_engine import engine_status
from .qlib_engine import reset as reset_qlib
from .signals import generate_baseline_momentum, generate_signal
from .store import Store
from .strategies import (
    StrategyDefinition,
    delete_strategy,
    generate_strategy_signal,
    get_strategy,
    latest_factor_date,
    list_strategies,
    save_strategy,
    slugify,
    strategy_backtest_config,
    validate_strategy,
)

log = logging.getLogger("finhound.api")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

CORE_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = CORE_DIR / "static"
REPO_DIR = Path(__file__).resolve().parents[4] if len(Path(__file__).resolve().parents) > 4 else None

app = FastAPI(title="FinHound QuantPlatform", version="1.2.0")
store = Store(read_only=os.getenv("API_READ_ONLY", "0") == "1")
jobs: dict[str, dict[str, Any]] = {}
job_lock = threading.Lock()
pipeline_lock = threading.Lock()
cancel_requests: set[str] = set()
allowed_origins = [x for x in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if x]
if allowed_origins:
    app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

PUBLIC_PATHS = {"/health", "/", "/docs", "/openapi.json", "/redoc"}
JOBS_PATH = store.root / "jobs.json"
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "0") or 0)
RATE_WINDOW_SECONDS = 60
rate_buckets: dict[str, deque[float]] = defaultdict(deque)
rate_lock = threading.Lock()


def _read_only() -> bool:
    return os.getenv("API_READ_ONLY", "0") == "1"


def _persist_jobs_locked() -> None:
    try:
        JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        JOBS_PATH.write_text(json.dumps(jobs, indent=2, default=str))
    except OSError:  # pragma: no cover - persistence is best-effort
        pass


def _load_jobs() -> None:
    if not JOBS_PATH.exists():
        return
    try:
        saved = json.loads(JOBS_PATH.read_text())
    except (OSError, ValueError):
        return
    if isinstance(saved, dict):
        for job_id, payload in saved.items():
            if isinstance(payload, dict):
                jobs[job_id] = payload


def _update_job(job_id: str, payload: dict[str, Any]) -> None:
    with job_lock:
        jobs[job_id] = payload
        _persist_jobs_locked()


def _job_cancelled(job_id: str) -> bool:
    with job_lock:
        return job_id in cancel_requests


_load_jobs()


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    if RATE_LIMIT and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with rate_lock:
            bucket = rate_buckets[client]
            while bucket and now - bucket[0] > RATE_WINDOW_SECONDS:
                bucket.popleft()
            if len(bucket) >= RATE_LIMIT:
                return JSONResponse(
                    {"detail": "rate limit exceeded", "request_id": request_id},
                    status_code=429,
                    headers={"retry-after": str(RATE_WINDOW_SECONDS)},
                )
            bucket.append(now)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001 - return a stable error contract
        log.exception("unhandled_request_error", extra={"path": request.url.path, "request_id": request_id})
        return JSONResponse(
            {"detail": "internal server error", "request_id": request_id},
            status_code=500,
        )
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["x-request-id"] = request_id
    response.headers["x-response-time-ms"] = f"{duration_ms:.1f}"
    if os.getenv("LOG_REQUESTS", "0") == "1":
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 1),
                "request_id": request_id,
            },
        )
    return response


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    expected = os.getenv("API_KEY")
    path = request.url.path
    if expected and path not in PUBLIC_PATHS and not path.startswith("/static/") and request.headers.get("x-api-key") != expected:
        return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
    return await call_next(request)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def _records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Convert a frame to JSON-safe records (dates as ISO strings, NaN as null)."""
    if frame is None or frame.empty:
        return []
    out = frame.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[column]):
            out[column] = out[column].dt.strftime("%Y-%m-%d")
        elif out[column].dtype == object:
            out[column] = out[column].map(_safe_value)
    return json.loads(out.to_json(orient="records", date_format="iso"))


def _safe_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _one(frame: pd.DataFrame) -> dict[str, Any]:
    rows = _records(frame)
    return rows[0] if rows else {}


def _settings_path() -> Path | None:
    candidates: list[Any] = [
        os.getenv("SETTINGS_PATH"),
        "/config/settings.yaml",
        (REPO_DIR / "config" / "settings.yaml") if REPO_DIR else None,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def _signal_metadata() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    directory = store.root / "signals"
    if not directory.exists():
        return results
    for path in sorted(directory.glob("*.parquet")):
        try:
            frame = pd.read_parquet(path, columns=["date", "symbol"])
        except Exception:
            continue
        if frame.empty:
            results.append({"name": path.stem, "rows": 0, "symbols": 0, "first_date": None, "last_date": None})
            continue
        dates = pd.to_datetime(frame["date"])
        valid_scores = pd.read_parquet(path, columns=["score"])
        results.append(
            {
                "name": path.stem,
                "rows": int(len(frame)),
                "symbols": int(frame["symbol"].nunique()),
                "first_date": dates.min().date().isoformat(),
                "last_date": dates.max().date().isoformat(),
                "scored_rows": int(valid_scores["score"].notna().sum()),
            }
        )
    return results


def _pipeline_runs(limit: int = 20) -> list[dict[str, Any]]:
    directory = store.root / "pipeline_runs"
    if not directory.exists():
        return []
    runs: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), reverse=True)[:limit]:
        try:
            payload = yaml.safe_load(path.read_text()) or {}
        except Exception:
            payload = {"status": "unreadable"}
        payload["file"] = path.name
        runs.append(payload)
    return runs


def _ingest_runs(limit: int = 20) -> list[dict[str, Any]]:
    path = store.root / "ingest_runs.parquet"
    if not path.exists():
        return []
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return []
    return _records(frame.tail(limit).iloc[::-1])


def _record_pipeline_run(started: datetime, payload: dict[str, Any]) -> None:
    """Persist an API-triggered pipeline run next to CLI pipeline summaries."""
    try:
        directory = store.root / "pipeline_runs"
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "trigger": "api",
            **payload,
        }
        (directory / f"{started.strftime('%Y%m%dT%H%M%S%fZ')}.json").write_text(json.dumps(payload, indent=2, default=str))
    except OSError:
        pass


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_name: str = "momentum_reversal_liquidity"
    strategy_id: str | None = None
    strategy_name: str | None = None
    mode: str = Field("long_only", pattern="^(long_only|long_short)$")
    rebalance_frequency: str = Field("weekly", pattern="^(daily|weekly|monthly)$")
    quantile: float = Field(0.1, gt=0, le=1)
    max_position_weight: float = Field(0.25, gt=0, le=1)
    gross_exposure: float = Field(1.0, ge=0.05, le=2.0)
    long_fraction: float = Field(0.5, ge=0.05, le=0.95)
    target_volatility: float | None = Field(None, ge=0.01, le=1.5)
    drawdown_stop: float | None = Field(None, ge=0.01, le=0.95)
    drawdown_resume: float | None = Field(None, ge=0.0, le=0.95)
    max_gross_exposure: float = Field(1.0, ge=0.05, le=2.0)
    commission_bps: float = Field(1.0, ge=0, le=1000)
    slippage_bps: float = Field(2.0, ge=0, le=1000)
    borrow_bps: float = Field(50.0, ge=0, le=10000)
    start_date: date | None = None
    end_date: date | None = None
    split_date: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> BacktestConfig:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        if self.split_date and self.start_date and self.split_date < self.start_date:
            raise ValueError("split_date must be inside the backtest range")
        if self.split_date and self.end_date and self.split_date > self.end_date:
            raise ValueError("split_date must be inside the backtest range")
        if self.drawdown_resume is not None and self.drawdown_stop is not None and self.drawdown_resume > self.drawdown_stop:
            raise ValueError("drawdown_resume must be less than or equal to drawdown_stop")
        return self


class StrategyRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date | None = None
    end_date: date | None = None
    split_date: date | None = None
    force: bool = False


class DuplicateStrategyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=2, max_length=80)


class DirectionBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: str = Field("auto", pattern="^(auto|native|qlib)$")
    force: bool = False


class OptionGammaRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str | None = None
    expiration: date | None = None
    strike: float = Field(gt=0)
    option_type: str
    gamma: float = Field(gt=0)
    open_interest: float = Field(gt=0)
    contract_size: float = Field(100.0, gt=0)
    spot: float | None = Field(None, gt=0)


class GammaExposureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str | None = Field(None, min_length=1, max_length=20)
    spot: float | None = Field(None, gt=0)
    options: list[OptionGammaRow] = Field(min_length=1, max_length=100000)


class GammaHeatmapRequest(GammaExposureRequest):
    pass


@app.get("/health")
def health() -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        data = _one(
            store.query(
                """
                SELECT count(*) AS rows, count(DISTINCT symbol) AS symbols,
                       min(date) AS first_date, max(date) AS last_date,
                       max(fetched_at) AS last_ingest
                FROM clean_prices
                """
            )
        )
    except Exception as exc:  # noqa: BLE001 - health should still answer
        data = {"error": str(exc)}
    rows = int(data.get("rows") or 0)
    with job_lock:
        active_jobs = sum(1 for job in jobs.values() if job.get("status") in {"queued", "running"})
        total_jobs = len(jobs)
    return {
        "status": "ok" if rows else "degraded",
        "version": app.version,
        "read_only": _read_only(),
        "data": data,
        "engine": engine_status(store),
        "directions": directions_meta(store),
        "jobs": {"active": active_jobs, "total": total_jobs},
    }


@app.get("/metrics")
def metrics_endpoint() -> dict[str, Any]:
    """Lightweight operational counters for dashboards and probes."""
    counts: dict[str, Any] = {}
    for table in ("clean_prices", "qc_summary", "backtest_runs", "universe_membership"):
        try:
            counts[table] = int(store.query(f"SELECT count(*) AS rows FROM {table}").iloc[0]["rows"])
        except Exception:  # noqa: BLE001 - table may not exist before the first pipeline
            counts[table] = 0
    coverage = store.factor_coverage()
    counts["factors"] = int(coverage["rows"].fillna(0).sum()) if not coverage.empty else 0
    with job_lock:
        status_counts: dict[str, int] = defaultdict(int)
        for job in jobs.values():
            status_counts[str(job.get("status", "unknown"))] += 1
    return {"tables": counts, "jobs": dict(status_counts), "read_only": _read_only()}


@app.get("/engines")
def engines() -> dict[str, Any]:
    status = engine_status(store)
    status["native"] = {
        "available": True,
        "description": "Deterministic cross-sectional factor engine; always available.",
    }
    status["directions"] = directions_meta(store)
    return status


@app.get("/factors/catalog")
def factors_catalog() -> list[dict[str, Any]]:
    return factor_catalog()


@app.post("/options/gamma-exposure")
def options_gamma_exposure(body: GammaExposureRequest) -> dict[str, Any]:
    """Calculate strike-level option gamma exposure from a normalized chain."""
    frame = pd.DataFrame([row.model_dump() for row in body.options])
    try:
        return gamma_exposure(frame, spot=body.spot, symbol=body.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/options/gamma-heatmap")
def options_gamma_heatmap(body: GammaHeatmapRequest) -> dict[str, Any]:
    """Return expiration/strike gamma cells for heat-map rendering."""
    frame = pd.DataFrame([row.model_dump() for row in body.options])
    try:
        return gamma_heatmap(frame, spot=body.spot, symbol=body.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/overview")
def overview() -> dict[str, Any]:
    data = _one(
        store.query(
            """
            SELECT count(*) AS rows, count(DISTINCT symbol) AS symbols,
                   min(date) AS first_date, max(date) AS last_date,
                   max(fetched_at) AS last_ingest
            FROM clean_prices
            """
        )
    )
    qc = _one(
        store.query(
            """
            SELECT coalesce(sum(error_count), 0) AS errors,
                   coalesce(sum(warn_count), 0) AS warnings,
                   count(*) AS symbols
            FROM qc_summary
            """
        )
    )
    factor_coverage = store.factor_coverage()
    if factor_coverage.empty:
        factors = {"rows": 0, "factors": 0, "symbols": 0, "last_date": None}
    else:
        factors = {
            "rows": int(factor_coverage["rows"].fillna(0).sum()),
            "factors": int(len(factor_coverage)),
            "symbols": int(factor_coverage["symbols"].fillna(0).max()),
            "last_date": _safe_value(factor_coverage["last_date"].max()),
        }
    universe = _one(
        store.query(
            """
            SELECT count(*) AS total,
                   coalesce(sum(CASE WHEN tradable THEN 1 ELSE 0 END), 0) AS tradable
            FROM universe_membership
            """
        )
    )
    runs = store.query("SELECT run_id, created_at, metrics, config FROM backtest_runs ORDER BY created_at DESC")
    latest_backtest: dict[str, Any] | None = None
    if not runs.empty:
        row = runs.iloc[0]
        latest_backtest = {
            "run_id": row["run_id"],
            "created_at": _safe_value(row["created_at"]),
            "metrics": json.loads(row["metrics"] or "{}"),
            "config": json.loads(row["config"] or "{}"),
        }

    default_signal = None
    settings_path = _settings_path()
    if settings_path:
        try:
            settings = yaml.safe_load(settings_path.read_text()) or {}
            default_signal = (settings.get("signal") or {}).get("default")
        except Exception:
            default_signal = None

    return {
        "data": data,
        "universe": universe,
        "qc": qc,
        "factors": factors,
        "signals": _signal_metadata(),
        "backtests": {"count": int(len(runs)), "latest": latest_backtest},
        "ingest": {"last": (_ingest_runs(1) or [None])[0]},
        "pipeline": {"last": (_pipeline_runs(1) or [None])[0]},
        "default_signal": default_signal,
    }


@app.get("/universe")
def universe() -> list[dict[str, Any]]:
    return _records(store.query("SELECT DISTINCT symbol FROM clean_prices ORDER BY symbol"))


@app.get("/prices/{symbol}")
def prices(symbol: str, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    clauses = ["symbol = ?"]
    params: list[Any] = [symbol]
    if start:
        clauses.append("date >= CAST(? AS DATE)")
        params.append(start)
    if end:
        clauses.append("date <= CAST(? AS DATE)")
        params.append(end)
    return _records(store.query(f"SELECT * FROM clean_prices WHERE {' AND '.join(clauses)} ORDER BY date", params))


@app.get("/qc/summary")
def qc_summary() -> list[dict[str, Any]]:
    return _records(store.query("SELECT * FROM qc_summary ORDER BY symbol"))


@app.get("/qc/issues")
def qc_issues(
    severity: str | None = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 500,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM qc_issues"
    params: list[Any] = []
    if severity:
        sql += " WHERE severity = ?"
        params.append(severity)
    sql += " ORDER BY date DESC LIMIT ?"
    params.append(limit)
    return _records(store.query(sql, params))


@app.get("/factors/summary")
def factors_summary(
    on: Annotated[date | None, Query(alias="date")] = None,
    factor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    clauses = ["date = (SELECT max(date) FROM factors)"]
    params: list[Any] = []
    if on is not None:
        clauses = ["date = CAST(? AS DATE)"]
        params.append(on)
    if factor:
        clauses.append("factor = ?")
        params.append(factor)
    frame = store.query(
        f"SELECT date, symbol, factor, raw_value, standardized FROM factors WHERE {' AND '.join(clauses)}",
        params,
    )
    if frame.empty:
        return {"date": None, "factors": {}}
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["standardized"])
    selected_date = pd.to_datetime(frame["date"]).max().date().isoformat()
    result: dict[str, Any] = {"date": selected_date, "factors": {}}
    for name, group in frame.groupby("factor"):
        group = group.sort_values("standardized", ascending=False).reset_index(drop=True)
        group["rank"] = np.arange(1, len(group) + 1)
        items = _records(group[["symbol", "raw_value", "standardized", "rank"]])
        result["factors"][name] = {
            "count": len(items),
            "long": items[:limit],
            "short": items[-limit:][::-1],
            "items": items,
        }
    return result


@app.get("/factors/{symbol}")
def factors(symbol: str, factor: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM factors WHERE symbol = ?"
    params: list[Any] = [symbol]
    if factor:
        sql += " AND factor = ?"
        params.append(factor)
    return _records(store.query(sql + " ORDER BY date, factor", params))


@app.get("/signals")
def signals() -> list[str]:
    directory = store.root / "signals"
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.parquet"))


@app.get("/signals/directions/meta")
def direction_metadata() -> dict[str, Any]:
    return directions_meta(store)


@app.get("/signals/directions")
def direction_snapshot(
    horizon: str = "1m",
    on: Annotated[date | None, Query(alias="date")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    min_confidence: Annotated[float, Query(ge=0, le=1)] = 0.0,
) -> dict[str, Any]:
    if horizon not in DIRECTION_HORIZONS:
        raise HTTPException(422, f"horizon must be one of {', '.join(DIRECTION_HORIZONS)}")
    return directions_snapshot(store, DIRECTION_HORIZONS[horizon], on=on, limit=limit, min_confidence=min_confidence)


@app.get("/signals/directions/{symbol}")
def direction_symbol(
    symbol: str,
    horizon: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, Any]:
    sessions = None
    if horizon:
        if horizon not in DIRECTION_HORIZONS:
            raise HTTPException(422, f"horizon must be one of {', '.join(DIRECTION_HORIZONS)}")
        sessions = DIRECTION_HORIZONS[horizon]
    return symbol_history(store, symbol, sessions, limit)


@app.post("/directions", status_code=202)
def rebuild_directions(body: DirectionBuildRequest, _: None = Depends(require_api_key)) -> dict[str, str]:
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    if not pipeline_lock.acquire(blocking=False):
        raise HTTPException(409, "another analysis job is already running")
    job_id = uuid.uuid4().hex
    _update_job(job_id, {"status": "queued", "kind": "directions", "started_at": datetime.now(UTC).isoformat()})

    def worker() -> None:
        _update_job(job_id, {"status": "running", "kind": "directions"})
        try:
            frame = build_directions(store, force=body.force, engine=body.engine)
            if _job_cancelled(job_id):
                _update_job(job_id, {"status": "cancelled", "kind": "directions", "finished_at": datetime.now(UTC).isoformat()})
                return
            _update_job(
                job_id,
                {
                    "status": "completed",
                    "kind": "directions",
                    "rows": int(len(frame)),
                    "engine": body.engine,
                    "meta": directions_meta(store),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface failures to the dashboard
            _update_job(job_id, {"status": "failed", "kind": "directions", "error": str(exc), "finished_at": datetime.now(UTC).isoformat()})
        finally:
            pipeline_lock.release()

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pipeline_lock.release()
        raise
    return {"job_id": job_id}


@app.post("/directions/reset")
def reset_direction_artifacts(_: None = Depends(require_api_key)) -> dict[str, bool]:
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    reset_qlib(store)
    path = store.root / "directions" / "directions.parquet"
    if path.exists():
        path.unlink()
    meta = store.root / "directions" / "meta.json"
    if meta.exists():
        meta.unlink()
    return {"reset": True}


@app.get("/signals/{name}")
def signal(name: str) -> list[dict[str, Any]]:
    path = store.root / "signals" / f"{name}.parquet"
    if not path.exists():
        raise HTTPException(404, "signal not found")
    return _records(pd.read_parquet(path))


@app.get("/signals/{name}/snapshot")
def signal_snapshot(
    name: str,
    on: Annotated[date | None, Query(alias="date")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    path = store.root / "signals" / f"{name}.parquet"
    if not path.exists():
        raise HTTPException(404, "signal not found")
    source = "SELECT date, symbol, score FROM read_parquet(?) WHERE score IS NOT NULL AND isfinite(score)"
    history = store.query(
        f"SELECT date, count(*) AS count, avg(score) AS mean, stddev_samp(score) AS std FROM ({source}) GROUP BY date ORDER BY date",
        [str(path)],
    )
    if history.empty:
        return {"name": name, "date": None, "count": 0, "long": [], "short": [], "items": [], "history": []}

    history["date"] = pd.to_datetime(history["date"]).dt.date
    selected = on if on is not None else history["date"].iloc[-1]
    snapshot = store.query(
        f"SELECT symbol, score FROM ({source}) WHERE date = ? ORDER BY score DESC",
        [str(path), selected],
    )
    count = len(snapshot)
    scores = snapshot["score"].to_numpy(dtype=float)
    ranks = np.arange(1, count + 1)
    percentiles = (count - ranks) / max(count - 1, 1)
    items = [
        {"symbol": str(symbol), "score": float(score), "rank": int(rank), "percentile": float(percentile)}
        for symbol, score, rank, percentile in zip(snapshot["symbol"], scores, ranks, percentiles, strict=True)
    ]
    return {
        "name": name,
        "date": selected.isoformat(),
        "count": count,
        "mean": float(scores.mean()) if count else 0.0,
        "std": float(scores.std(ddof=1)) if count > 1 else 0.0,
        "min": float(scores.min()) if count else 0.0,
        "max": float(scores.max()) if count else 0.0,
        "positive": int((scores > 0).sum()) if count else 0,
        "first_date": history["date"].iloc[0].isoformat(),
        "last_date": history["date"].iloc[-1].isoformat(),
        "long": items[:limit],
        "short": items[-limit:][::-1] if count else [],
        "items": items,
        "history": _records(history),
    }


def _strategy_or_404(strategy_id: str) -> dict[str, Any]:
    definition = get_strategy(store, strategy_id)
    if not definition:
        raise HTTPException(404, "strategy not found")
    return definition


def _unique_strategy_id(base: str) -> str:
    candidate = slugify(base)
    existing = {item["id"] for item in list_strategies(store)}
    if candidate not in existing:
        return candidate
    for index in range(2, 1000):
        trial = f"{candidate[:56]}_{index}"
        if trial not in existing:
            return trial
    raise HTTPException(409, "could not allocate a unique strategy id")


@app.get("/strategies")
def strategies() -> list[dict[str, Any]]:
    return list_strategies(store)


@app.post("/strategies/validate")
def strategy_validation(definition: StrategyDefinition) -> dict[str, Any]:
    return validate_strategy(store, definition)


@app.post("/strategies/preview")
def strategy_preview_draft(definition: StrategyDefinition, limit: Annotated[int, Query(ge=1, le=100)] = 10) -> dict[str, Any]:
    payload = definition.model_dump()
    preview = _strategy_preview(payload, limit=limit, persist=False)
    preview["validation"] = validate_strategy(store, definition)
    preview["config"] = strategy_backtest_config(definition)
    return preview


@app.post("/strategies", status_code=201)
def create_strategy(definition: StrategyDefinition, _: None = Depends(require_api_key)) -> dict[str, Any]:
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    if get_strategy(store, definition.id):
        raise HTTPException(409, "a strategy with this id already exists")
    payload = definition.model_dump()
    payload["id"] = _unique_strategy_id(definition.id)
    payload["builtin"] = False
    return save_strategy(store, StrategyDefinition(**payload))


@app.get("/strategies/{strategy_id}")
def strategy_detail(strategy_id: str) -> dict[str, Any]:
    definition = _strategy_or_404(strategy_id)
    definition["validation"] = validate_strategy(store, StrategyDefinition(**{k: v for k, v in definition.items() if k != "validation"}))
    return definition


@app.put("/strategies/{strategy_id}")
def update_strategy(
    strategy_id: str,
    definition: StrategyDefinition,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    if definition.id != strategy_id:
        raise HTTPException(422, "the strategy id in the payload must match the URL")
    if strategy_id in {item["id"] for item in list_strategies(store) if item.get("builtin")}:
        raise HTTPException(409, "built-in strategies are immutable; duplicate the strategy first")
    if not get_strategy(store, strategy_id):
        raise HTTPException(404, "strategy not found")
    return save_strategy(store, definition)


@app.delete("/strategies/{strategy_id}")
def remove_strategy(strategy_id: str, _: None = Depends(require_api_key)) -> dict[str, bool]:
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    if not delete_strategy(store, strategy_id):
        raise HTTPException(409, "strategy not found or is a built-in")
    return {"deleted": True}


@app.post("/strategies/{strategy_id}/duplicate", status_code=201)
def duplicate_strategy(
    strategy_id: str,
    body: DuplicateStrategyRequest,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    source = _strategy_or_404(strategy_id)
    name = body.name or f"{source['name']} Copy"
    payload = {key: value for key, value in source.items() if key not in {"created_at", "updated_at", "builtin"}}
    payload["id"] = _unique_strategy_id(name)
    payload["name"] = name
    payload["builtin"] = False
    return save_strategy(store, StrategyDefinition(**payload))


def _strategy_preview(definition: dict[str, Any], limit: int = 10, persist: bool = False) -> dict[str, Any]:
    clean = {key: value for key, value in definition.items() if key != "validation"}
    # Previews only render the latest cross-section, so skip the full history
    # scan that a real strategy run needs.
    latest = latest_factor_date(store)
    since = latest - timedelta(days=7) if latest else None
    frame = generate_strategy_signal(store, StrategyDefinition(**clean), persist=persist, since=since)
    if frame.empty:
        return {"available": False, "items": [], "long": [], "short": [], "count": 0}
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    latest = frame["date"].max()
    day = frame[frame["date"] == latest].dropna(subset=["score"]).sort_values("score", ascending=False).reset_index(drop=True)
    day["rank"] = np.arange(1, len(day) + 1)
    items = _records(day[["symbol", "score", "rank"]])
    return {
        "available": True,
        "date": latest.isoformat(),
        "count": len(items),
        "mean_score": float(day["score"].mean()) if len(day) else 0.0,
        "std_score": float(day["score"].std(ddof=1)) if len(day) > 1 else 0.0,
        "long": items[:limit],
        "short": items[-limit:][::-1] if items else [],
        "items": items,
    }


@app.get("/strategies/{strategy_id}/preview")
def strategy_preview(
    strategy_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    definition = _strategy_or_404(strategy_id)
    preview = _strategy_preview(definition, limit=limit, persist=False)
    preview["strategy_id"] = strategy_id
    preview["validation"] = validate_strategy(store, StrategyDefinition(**{k: v for k, v in definition.items() if k != "validation"}))
    preview["config"] = strategy_backtest_config(definition)
    return preview


@app.post("/strategies/{strategy_id}/run", status_code=202)
def run_strategy(
    strategy_id: str,
    body: StrategyRunRequest | None = None,
    _: None = Depends(require_api_key),
) -> dict[str, str]:
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    definition = _strategy_or_404(strategy_id)
    if not pipeline_lock.acquire(blocking=False):
        raise HTTPException(409, "another analysis job is already running")
    body = body or StrategyRunRequest()
    overrides = {
        "start_date": body.start_date.isoformat() if body.start_date else None,
        "end_date": body.end_date.isoformat() if body.end_date else None,
        "split_date": body.split_date.isoformat() if body.split_date else None,
    }
    config = strategy_backtest_config(definition, overrides)
    job_id = uuid.uuid4().hex
    _update_job(job_id, {"status": "queued", "kind": "strategy", "strategy_id": strategy_id})
    if not _read_only():
        store.record_strategy_run(job_id, strategy_id, "queued")

    def worker() -> None:
        _update_job(job_id, {"status": "running", "kind": "strategy", "strategy_id": strategy_id})
        if not _read_only():
            store.record_strategy_run(job_id, strategy_id, "running")
        try:
            signal_frame = generate_strategy_signal(store, StrategyDefinition(**{k: v for k, v in definition.items() if k != "validation"}))
            if _job_cancelled(job_id):
                store.record_strategy_run(job_id, strategy_id, "cancelled")
                _update_job(job_id, {"status": "cancelled", "kind": "strategy", "strategy_id": strategy_id})
                return
            run_id, result = run_backtest(store, signal_frame, config)
            store.record_strategy_run(job_id, strategy_id, "completed", run_id=run_id)
            _update_job(
                job_id,
                {
                    "status": "completed",
                    "kind": "strategy",
                    "strategy_id": strategy_id,
                    "run_id": run_id,
                    "metrics": result.get("full", {}),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface failures to the dashboard
            if not _read_only():
                store.record_strategy_run(job_id, strategy_id, "failed", error=str(exc))
            _update_job(job_id, {"status": "failed", "kind": "strategy", "strategy_id": strategy_id, "error": str(exc)})
        finally:
            pipeline_lock.release()

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pipeline_lock.release()
        raise
    return {"job_id": job_id, "strategy_id": strategy_id}


@app.get("/strategies/{strategy_id}/runs")
def strategy_runs(strategy_id: str, limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, Any]]:
    _strategy_or_404(strategy_id)
    frame = store.query("SELECT * FROM strategy_runs WHERE strategy_id = ? ORDER BY created_at DESC LIMIT ?", [strategy_id, limit])
    return _records(frame)


@app.get("/ingest/runs")
def ingest_runs(limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, Any]]:
    return _ingest_runs(limit)


@app.get("/pipeline/runs")
def pipeline_runs(limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, Any]]:
    return _pipeline_runs(limit)


@app.post("/pipeline", status_code=202)
def run_pipeline(_: None = Depends(require_api_key)) -> dict[str, str]:
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    settings_path = _settings_path()
    if settings_path is None:
        raise HTTPException(409, "settings.yaml not found; set SETTINGS_PATH")
    if not pipeline_lock.acquire(blocking=False):
        raise HTTPException(409, "a pipeline run is already in progress")

    job_id = uuid.uuid4().hex
    _update_job(job_id, {"status": "queued", "kind": "pipeline"})

    def worker() -> None:
        started_at = datetime.now(UTC)
        signal_name = "momentum_reversal_liquidity"
        _update_job(job_id, {"status": "running", "kind": "pipeline", "started_at": started_at.isoformat()})
        try:
            settings = yaml.safe_load(settings_path.read_text()) or {}
            signal_name = (settings.get("signal") or {}).get("default", signal_name)
            _update_job(job_id, {"status": "running", "kind": "pipeline", "stage": "qc", "started_at": started_at.isoformat()})
            run_qc(store)
            if _job_cancelled(job_id):
                _update_job(job_id, {"status": "cancelled", "kind": "pipeline", "finished_at": datetime.now(UTC).isoformat()})
                return
            available = store.query("SELECT count(*) AS rows FROM clean_prices").iloc[0]["rows"]
            if not int(available):
                raise RuntimeError("no clean prices; run ingestion (`make ingest`) before rebuilding analytics")
            _update_job(job_id, {"status": "running", "kind": "pipeline", "stage": "factors", "started_at": started_at.isoformat()})
            compute_factors(store.query("SELECT * FROM clean_prices"), store)
            if _job_cancelled(job_id):
                _update_job(job_id, {"status": "cancelled", "kind": "pipeline", "finished_at": datetime.now(UTC).isoformat()})
                return
            _update_job(job_id, {"status": "running", "kind": "pipeline", "stage": "signals", "started_at": started_at.isoformat()})
            signal_frame = generate_signal(store, settings_path)
            generate_baseline_momentum(store)
            directions_error = None
            if not _job_cancelled(job_id):
                _update_job(job_id, {"status": "running", "kind": "pipeline", "stage": "directions", "started_at": started_at.isoformat()})
                try:
                    build_directions(store, engine=str((settings.get("directions") or {}).get("engine", "auto")))
                except Exception as exc:  # noqa: BLE001 - Qlib is optional; the pipeline keeps the factor analytics
                    directions_error = str(exc)
            if _job_cancelled(job_id):
                _update_job(job_id, {"status": "cancelled", "kind": "pipeline", "finished_at": datetime.now(UTC).isoformat()})
                return
            _update_job(job_id, {"status": "running", "kind": "pipeline", "stage": "backtest", "started_at": started_at.isoformat()})
            backtest_config = dict(settings.get("backtest") or {})
            backtest_config["signal_name"] = signal_name
            run_id, result = run_backtest(store, signal_frame, backtest_config)
            _record_pipeline_run(
                started_at,
                {
                    "status": "ok",
                    "run_id": run_id,
                    "signal_name": signal_name,
                    "metrics": result.get("full", {}),
                    "directions_error": directions_error,
                    "engine": engine_status(store).get("engine"),
                },
            )
            _update_job(
                job_id,
                {
                    "status": "completed",
                    "kind": "pipeline",
                    "run_id": run_id,
                    "signal_name": signal_name,
                    "metrics": result.get("full", {}),
                    "directions_error": directions_error,
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface failures to the dashboard
            _record_pipeline_run(started_at, {"status": "failed", "signal_name": signal_name, "error": str(exc)})
            _update_job(job_id, {"status": "failed", "kind": "pipeline", "error": str(exc), "finished_at": datetime.now(UTC).isoformat()})
        finally:
            pipeline_lock.release()

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pipeline_lock.release()
        raise
    return {"job_id": job_id}


@app.post("/backtests", status_code=202)
def backtests(config: BacktestConfig, _: None = Depends(require_api_key)) -> dict[str, str]:
    path = store.root / "signals" / f"{config.signal_name}.parquet"
    if not path.exists():
        raise HTTPException(404, "signal not found")
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    if not pipeline_lock.acquire(blocking=False):
        raise HTTPException(409, "another analysis job is already running")
    payload = config.model_dump()
    job_id = uuid.uuid4().hex
    _update_job(job_id, {"status": "queued", "kind": "backtest", "signal_name": config.signal_name})

    def worker() -> None:
        _update_job(job_id, {"status": "running", "kind": "backtest", "started_at": datetime.now(UTC).isoformat()})
        try:
            run_id, result = run_backtest(store, pd.read_parquet(path), payload)
            _update_job(
                job_id,
                {
                    "status": "completed",
                    "kind": "backtest",
                    "run_id": run_id,
                    "metrics": result.get("full", {}),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface failures to the dashboard
            _update_job(job_id, {"status": "failed", "kind": "backtest", "error": str(exc), "finished_at": datetime.now(UTC).isoformat()})
        finally:
            pipeline_lock.release()

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pipeline_lock.release()
        raise
    return {"job_id": job_id}


@app.get("/jobs")
def list_jobs(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
    with job_lock:
        items = [{"job_id": job_id, **payload} for job_id, payload in jobs.items()]
    items.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return items[:limit]


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    if _read_only():
        raise HTTPException(409, "API is read-only; start it with API_READ_ONLY=0")
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return {"job_id": job_id, "status": job.get("status"), "cancelled": False}
        cancel_requests.add(job_id)
        jobs[job_id] = {**job, "status": "cancelling", "cancel_requested_at": datetime.now(UTC).isoformat()}
        _persist_jobs_locked()
    return {"job_id": job_id, "status": "cancelling", "cancelled": True}


@app.get("/jobs/{job_id}")
@app.get("/backtests/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    with job_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {"job_id": job_id, **job}


@app.get("/backtests")
def list_backtests() -> list[dict[str, Any]]:
    return _records(store.query("SELECT * FROM backtest_runs ORDER BY created_at DESC"))


@app.get("/backtests/{run_id}")
def backtest(run_id: str) -> dict[str, Any]:
    rows = store.query("SELECT * FROM backtest_runs WHERE run_id = ?", [run_id])
    if rows.empty:
        raise HTTPException(404, "backtest not found")
    result = rows.iloc[0].to_dict()
    result["created_at"] = _safe_value(result.get("created_at"))
    result["config"] = json.loads(result["config"])
    result["metrics"] = json.loads(result["metrics"])
    return result


@app.get("/backtests/{run_id}/equity")
def equity(run_id: str) -> list[dict[str, Any]]:
    path = store.root / "results" / run_id / "equity.parquet"
    if not path.exists():
        raise HTTPException(404, "backtest not found")
    return _records(pd.read_parquet(path))


@app.get("/backtests/{run_id}/risk")
def backtest_risk(
    run_id: str,
    window: Annotated[int, Query(ge=2, le=756)] = 63,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> dict[str, Any]:
    """Return aggregate, rolling, and drawdown risk diagnostics for a run."""
    path = store.root / "results" / run_id / "equity.parquet"
    if not path.exists():
        raise HTTPException(404, "backtest equity not found")
    frame = pd.read_parquet(path)
    required = {"date", "daily_return"}
    if not required.issubset(frame.columns):
        raise HTTPException(422, "backtest equity is missing required return columns")
    returns = pd.Series(
        pd.to_numeric(frame["daily_return"], errors="coerce").to_numpy(),
        index=pd.to_datetime(frame["date"]),
        name="strategy",
    )
    benchmark = None
    if "SPY" in frame.columns:
        benchmark = pd.Series(
            pd.to_numeric(frame["SPY"], errors="coerce").pct_change().to_numpy(),
            index=pd.to_datetime(frame["date"]),
            name="SPY",
        )
    episodes = drawdown_episodes(returns).head(limit)
    rolling = rolling_risk(returns, window=window, benchmark=benchmark).tail(limit)
    return {
        "run_id": run_id,
        "window": window,
        "summary": performance_metrics(returns, benchmark=benchmark),
        "drawdowns": _records(episodes),
        "rolling": _records(rolling),
    }


@app.get("/backtests/{run_id}/research")
def research(
    run_id: str,
    kind: str | None = None,
    horizon: int | None = None,
) -> list[dict[str, Any]]:
    path = store.root / "results" / run_id / "research.parquet"
    if not path.exists():
        raise HTTPException(404, "backtest research not found")
    frame = pd.read_parquet(path)
    if kind:
        frame = frame[frame["kind"] == kind]
    if horizon is not None:
        frame = frame[frame["horizon"] == horizon]
    return _records(frame)


@app.get("/backtests/{run_id}/positions")
def positions(run_id: str, on: Annotated[date | None, Query(alias="date")] = None) -> dict[str, Any]:
    path = store.root / "results" / run_id / "positions.parquet"
    if not path.exists():
        raise HTTPException(404, "backtest positions not found")
    frame = pd.read_parquet(path)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce").fillna(0.0)
    active = frame[frame["weight"].abs() > 1e-12]
    if active.empty:
        return {"date": None, "positions": []}
    selected = active["date"].max()
    if on is not None:
        candidates = active.loc[active["date"] <= on, "date"]
        if not candidates.empty:
            selected = candidates.max()
    rows = active[active["date"] == selected].sort_values("weight", ascending=False)
    return {"date": selected.isoformat(), "positions": _records(rows[["symbol", "weight"]])}


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
