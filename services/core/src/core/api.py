from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .backtest import run_backtest
from .store import Store

app = FastAPI(title="QuantPlatform", version="1.0.0")
store = Store(read_only=os.getenv("API_READ_ONLY", "1") != "0")
jobs: dict[str, dict[str, Any]] = {}
job_lock = threading.Lock()
allowed_origins = [x for x in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if x]
if allowed_origins:
    app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    expected = os.getenv("API_KEY")
    public = {"/health", "/", "/docs", "/openapi.json", "/redoc"}
    if expected and request.url.path not in public and request.headers.get("x-api-key") != expected:
        return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
    return await call_next(request)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_name: str = "momentum_reversal_liquidity"
    mode: str = Field("long_only", pattern="^(long_only|long_short)$")
    rebalance_frequency: str = Field("weekly", pattern="^(daily|weekly|monthly)$")
    quantile: float = Field(0.1, gt=0, le=1)
    max_position_weight: float = Field(0.25, gt=0, le=1)
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
        return self


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/universe")
def universe() -> list[dict[str, Any]]:
    return store.query("SELECT DISTINCT symbol FROM clean_prices ORDER BY symbol").to_dict("records")


@app.get("/prices/{symbol}")
def prices(symbol: str, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    clauses = ["symbol = ?"]
    params: list[Any] = [symbol]
    if start:
        clauses.append("date >= ?")
        params.append(start)
    if end:
        clauses.append("date <= ?")
        params.append(end)
    return store.query(f"SELECT * FROM clean_prices WHERE {' AND '.join(clauses)} ORDER BY date", params).to_dict("records")


@app.get("/qc/summary")
def qc_summary() -> list[dict[str, Any]]:
    return store.query("SELECT * FROM qc_summary ORDER BY symbol").to_dict("records")


@app.get("/qc/issues")
def qc_issues(severity: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM qc_issues"
    params: list[Any] = []
    if severity:
        sql += " WHERE severity = ?"
        params.append(severity)
    return store.query(sql, params).to_dict("records")


@app.get("/factors/{symbol}")
def factors(symbol: str, factor: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM factors WHERE symbol = ?"
    params: list[Any] = [symbol]
    if factor:
        sql += " AND factor = ?"
        params.append(factor)
    return store.query(sql + " ORDER BY date, factor", params).to_dict("records")


@app.get("/signals")
def signals() -> list[str]:
    return [p.stem for p in (store.root / "signals").glob("*.parquet")]


@app.get("/signals/{name}")
def signal(name: str) -> list[dict[str, Any]]:
    path = store.root / "signals" / f"{name}.parquet"
    if not path.exists():
        raise HTTPException(404, "signal not found")
    return pd.read_parquet(path).to_dict("records")


@app.post("/backtests", status_code=202)
def backtests(config: BacktestConfig, _: None = Depends(require_api_key)) -> dict[str, str]:
    path = store.root / "signals" / f"{config.signal_name}.parquet"
    if not path.exists():
        raise HTTPException(404, "signal not found")
    job_id = uuid.uuid4().hex
    with job_lock:
        jobs[job_id] = {"status": "queued"}

    def worker() -> None:
        with job_lock:
            jobs[job_id] = {"status": "running"}
        try:
            run_id, _ = run_backtest(store, pd.read_parquet(path), config.model_dump(exclude={"signal_name"}))
            with job_lock:
                jobs[job_id] = {"status": "completed", "run_id": run_id}
        except Exception as exc:
            with job_lock:
                jobs[job_id] = {"status": "failed", "error": str(exc)}

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id}


@app.get("/backtests/jobs/{job_id}")
def backtest_job(job_id: str) -> dict[str, Any]:
    with job_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {"job_id": job_id, **job}


@app.get("/backtests")
def list_backtests() -> list[dict[str, Any]]:
    return store.query("SELECT * FROM backtest_runs ORDER BY created_at DESC").to_dict("records")


@app.get("/backtests/{run_id}")
def backtest(run_id: str) -> dict[str, Any]:
    rows = store.query("SELECT * FROM backtest_runs WHERE run_id = ?", [run_id])
    if rows.empty:
        raise HTTPException(404, "backtest not found")
    result = rows.iloc[0].to_dict()
    result["config"] = json.loads(result["config"])
    result["metrics"] = json.loads(result["metrics"])
    return result


@app.get("/backtests/{run_id}/equity")
def equity(run_id: str) -> list[dict[str, Any]]:
    path = store.root / "results" / run_id / "equity.parquet"
    if not path.exists():
        raise HTTPException(404, "backtest not found")
    return pd.read_parquet(path).to_dict("records")


@app.get("/backtests/{run_id}/research")
def research(run_id: str) -> list[dict[str, Any]]:
    path = store.root / "results" / run_id / "research.parquet"
    if not path.exists():
        raise HTTPException(404, "backtest research not found")
    return pd.read_parquet(path).to_dict("records")


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).parent.parent.parent / "static" / "index.html")
