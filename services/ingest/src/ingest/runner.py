from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol

import pandas as pd
import structlog
import yaml

log = structlog.get_logger()


class HistoricalProvider(Protocol):
    def historical(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame: ...


class OpenBBProvider:
    def __init__(self) -> None:
        try:
            from openbb import obb
        except ImportError as exc:
            raise RuntimeError("openbb is required for ingestion") from exc
        self._obb = obb

    def historical(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        result = self._obb.equity.price.historical(symbol=symbol, start_date=start_date, end_date=end_date, provider="yfinance")
        return result.to_df() if hasattr(result, "to_df") else pd.DataFrame(result)


def _normalise(frame: pd.DataFrame, symbol: str, fetched_at: datetime) -> pd.DataFrame:
    out = frame.copy()
    out.columns = [str(c).lower().replace(" ", "_") for c in out.columns]
    if "date" not in out.columns:
        if isinstance(out.index, pd.DatetimeIndex):
            out = out.reset_index(names="date")
        elif str(out.index.name).lower() == "date":
            out = out.reset_index()
        else:
            raise ValueError("provider response has no date column")
    out["date"] = pd.to_datetime(out["date"], utc=True).dt.date
    # Providers occasionally return the current session before it has traded,
    # with a placeholder row whose prices are all null. Persisting it breaks
    # factor and backtest calculations, so drop it at the boundary.
    if "close" in out.columns:
        out = out.loc[out["close"].notna()]
    else:
        price_columns = [column for column in ("open", "high", "low") if column in out.columns]
        if price_columns:
            out = out.loc[~out[price_columns].isna().all(axis=1)]
    out["symbol"] = symbol
    out["provider"] = "yfinance"
    out["fetched_at"] = fetched_at
    if "adjusted" not in out.columns:
        out["adjusted"] = False
    return out


def _ingest_symbol(
    symbol: str,
    root: Path,
    default_start: date,
    end: date,
    provider: HistoricalProvider,
    provider_lock: Lock,
    sleep_seconds: float,
    retries: int,
) -> dict[str, object]:
    """Fetch one symbol, preserving the existing per-symbol parquet contract."""
    start = default_start
    target = root / f"symbol={symbol}"
    existing = pd.DataFrame()
    files = sorted(target.glob("*.parquet"))
    if files:
        existing = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
        if not existing.empty:
            latest = pd.to_datetime(existing["date"]).dt.date.max()
            if latest >= end:
                return {"rows": 0, "error": None, "skipped": True}
            start = latest.fromordinal(latest.toordinal() + 1)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            # OpenBB/yfinance creates curl-backed resources per request. Keep
            # those requests serialized even when local file work is parallel.
            with provider_lock:
                frame = provider.historical(symbol, start.isoformat(), end.isoformat())
            normal = _normalise(frame, symbol, datetime.now(UTC))
            if not existing.empty:
                normal = pd.concat([existing, normal], ignore_index=True)
            normal = normal.drop_duplicates(subset=["symbol", "date"], keep="last").sort_values("date")
            target.mkdir(parents=True, exist_ok=True)
            normal.to_parquet(target / "data.parquet", index=False)
            return {"rows": len(normal), "error": None, "skipped": False}
        except Exception as exc:  # noqa: BLE001 - one symbol must not stop the batch
            last_error = exc
            if attempt < retries:
                time.sleep(min(30.0, max(sleep_seconds, 2.0**attempt)))
    log.error("ingest_symbol_failed", symbol=symbol, error=str(last_error))
    return {"rows": 0, "error": str(last_error), "skipped": False}


def ingest(
    universe_path: str | Path = "/config/universe.yaml",
    data_root: str | Path = "/data",
    provider: HistoricalProvider | None = None,
    today: date | None = None,
    sleep_seconds: float = 0.25,
    workers: int = 1,
    retries: int = 2,
) -> dict[str, int]:
    config = yaml.safe_load(Path(universe_path).read_text())
    symbols = [str(item["symbol"]) for item in config["symbols"]]
    default_start = date.fromisoformat(config.get("start_date", "2015-01-01"))
    end = today or date.today()
    provider = provider or OpenBBProvider()
    provider_lock = Lock()
    root = Path(data_root) / "raw" / "prices"
    root.mkdir(parents=True, exist_ok=True)
    stats = {"symbols": 0, "rows": 0, "failures": 0}
    run_errors: list[dict[str, str]] = []

    def work(symbol: str) -> tuple[str, dict[str, object]]:
        result = _ingest_symbol(
            symbol,
            root,
            default_start,
            end,
            provider,
            provider_lock,
            sleep_seconds,
            retries,
        )
        if not result["skipped"]:
            time.sleep(sleep_seconds)
        return symbol, result

    workers = max(1, int(workers))
    if workers == 1:
        results = [work(symbol) for symbol in symbols]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(work, symbols))

    for symbol, result in results:
        if result["skipped"]:
            stats["symbols"] += 1
        elif result["error"] is None:
            stats["symbols"] += 1
            stats["rows"] += int(result["rows"])
        else:
            stats["failures"] += 1
            run_errors.append({"symbol": symbol, "error": str(result["error"])})
    Path(data_root).mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "started_at": datetime.now(UTC),
                **stats,
                "errors": json.dumps(run_errors, default=str),
            }
        ]
    ).to_parquet(Path(data_root) / "ingest_runs.parquet", index=False)
    return stats
