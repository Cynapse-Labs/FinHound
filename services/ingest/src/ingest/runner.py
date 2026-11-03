from __future__ import annotations

import time
from datetime import UTC, date, datetime
from pathlib import Path
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
        else:
            raise ValueError("provider response has no date column")
    out["date"] = pd.to_datetime(out["date"], utc=True).dt.date
    out["symbol"] = symbol
    out["provider"] = "yfinance"
    out["fetched_at"] = fetched_at
    if "adjusted" not in out.columns:
        out["adjusted"] = False
    return out


def ingest(
    universe_path: str | Path = "/config/universe.yaml",
    data_root: str | Path = "/data",
    provider: HistoricalProvider | None = None,
    today: date | None = None,
    sleep_seconds: float = 0.25,
) -> dict[str, int]:
    config = yaml.safe_load(Path(universe_path).read_text())
    symbols = [item["symbol"] for item in config["symbols"]]
    default_start = date.fromisoformat(config.get("start_date", "2015-01-01"))
    end = today or date.today()
    provider = provider or OpenBBProvider()
    root = Path(data_root) / "raw" / "prices"
    root.mkdir(parents=True, exist_ok=True)
    stats = {"symbols": 0, "rows": 0, "failures": 0}
    run_errors: list[dict[str, str]] = []
    for symbol in symbols:
        start = default_start
        target = root / f"symbol={symbol}"
        existing = pd.DataFrame()
        files = sorted(target.glob("*.parquet"))
        if files:
            existing = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
            if not existing.empty:
                latest = pd.to_datetime(existing["date"]).dt.date.max()
                if latest >= end:
                    stats["symbols"] += 1
                    continue
                start = latest.fromordinal(latest.toordinal() + 1)
        try:
            frame = provider.historical(symbol, start.isoformat(), end.isoformat())
            normal = _normalise(frame, symbol, datetime.now(UTC))
            if not existing.empty:
                normal = pd.concat([existing, normal], ignore_index=True)
            normal = normal.drop_duplicates(subset=["symbol", "date"], keep="last").sort_values("date")
            target.mkdir(parents=True, exist_ok=True)
            normal.to_parquet(target / "data.parquet", index=False)
            stats["rows"] += len(normal)
            stats["symbols"] += 1
        except Exception as exc:
            stats["failures"] += 1
            run_errors.append({"symbol": symbol, "error": str(exc)})
            log.error("ingest_symbol_failed", symbol=symbol, error=str(exc))
        time.sleep(sleep_seconds)
    Path(data_root).mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"started_at": datetime.now(UTC), **stats, "errors": run_errors}]).to_parquet(
        Path(data_root) / "ingest_runs.parquet", index=False
    )
    return stats
