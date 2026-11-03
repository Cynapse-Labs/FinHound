from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd
import structlog

from .store import Store

log = structlog.get_logger()

HORIZONS: dict[str, int] = {"1d": 1, "1w": 5, "1m": 21, "3m": 63}
HORIZON_LABELS = {"1d": "1 day", "1w": "1 week", "1m": "1 month", "3m": "3 months"}
HORIZON_TO_KEY = {value: key for key, value in HORIZONS.items()}

NATIVE_WEIGHTS: dict[int, dict[str, float]] = {
    1: {
        "reversal": 0.25,
        "momentum_1w": 0.15,
        "bollinger_z_20": 0.15,
        "volume_surprise_20": 0.15,
        "intraday_return": 0.15,
        "money_flow_20": 0.1,
        "price_efficiency_20": 0.1,
        "dollar_volume": 0.15,
    },
    5: {
        "momentum_1w": 0.2,
        "reversal": 0.15,
        "momentum_1m": 0.15,
        "volatility_20": 0.15,
        "volume_trend": 0.15,
        "residual_momentum_60": 0.2,
        "momentum_acceleration": 0.1,
    },
    21: {
        "momentum_3_1": 0.2,
        "momentum_1m": 0.15,
        "trend_50": 0.15,
        "volatility_60": 0.15,
        "distance_high": 0.15,
        "range_position_20": 0.2,
        "breakout_20": 0.1,
    },
    63: {
        "momentum_12_1": 0.25,
        "momentum_6_1": 0.2,
        "trend_200": 0.15,
        "volatility_60": 0.15,
        "drawdown_252": 0.1,
        "residual_momentum_60": 0.15,
        "trend_acceleration": 0.1,
        "idiosyncratic_vol_60": 0.1,
    },
}


def _artifact_dir(store: Store) -> Any:
    return store.root / "directions"


def _meta_path(store: Store) -> Any:
    return _artifact_dir(store) / "meta.json"


def _fingerprint(store: Store) -> str:
    prices = store.query(
        "SELECT count(*) AS rows, count(DISTINCT symbol) AS symbols, min(date) AS first_date, max(date) AS last_date FROM clean_prices"
    )
    factors = store.query("SELECT count(*) AS rows, max(date) AS last_date FROM factors")
    payload = {
        "prices": prices.iloc[0].to_dict() if not prices.empty else {},
        "factors": factors.iloc[0].to_dict() if not factors.empty else {},
    }
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def _close_panel(store: Store) -> pd.DataFrame:
    prices = store.query("SELECT date, symbol, close FROM clean_prices")
    if prices.empty:
        return pd.DataFrame()
    prices["date"] = pd.to_datetime(prices["date"]).dt.normalize()
    return prices.pivot(index="date", columns="symbol", values="close").sort_index()


def _native_raw(store: Store, horizons: list[int]) -> pd.DataFrame:
    available = set(store.factor_coverage()["factor"])
    frames: list[pd.DataFrame] = []
    for horizon in horizons:
        weights = {
            name: float(weight) for name, weight in NATIVE_WEIGHTS.get(horizon, {}).items() if name in available and float(weight) != 0
        }
        if not weights:
            continue
        terms = " + ".join("SUM(CASE WHEN factor = ? THEN standardized * ? ELSE 0 END)" for _ in weights)
        params: list[Any] = []
        for name, weight in weights.items():
            params.extend([name, weight])
        params.append(sum(abs(weight) for weight in weights.values()))
        placeholders = ", ".join("?" for _ in weights)
        params.extend(weights.keys())
        frame = store.query(
            f"SELECT date, symbol, ({terms}) / ? AS score FROM factors WHERE factor IN ({placeholders}) GROUP BY date, symbol",
            params,
        )
        if frame.empty:
            continue
        frame["horizon"] = int(horizon)
        frame["engine"] = "native"
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["date", "symbol", "horizon", "score", "engine"])
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    return out


def _zscore(frame: pd.DataFrame, column: str = "score") -> pd.Series:
    grouped = frame.groupby("date")[column]
    mean = grouped.transform("mean")
    std = grouped.transform("std").replace(0, np.nan)
    return ((frame[column] - mean) / std).fillna(0.0)


def _annotate(raw: pd.DataFrame, close: pd.DataFrame, horizon: int, engine: str) -> pd.DataFrame:
    frame = raw[["date", "symbol", "score"]].copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"]).reset_index(drop=True)
    frame["score_z"] = _zscore(frame)
    daily_returns = close.pct_change()
    trailing_volatility = (daily_returns.rolling(20, min_periods=10).std() * np.sqrt(horizon)).shift(1)
    scale = trailing_volatility.median(axis=1).reindex(frame["date"]).to_numpy()
    fallback = float(np.nanmedian(trailing_volatility.to_numpy())) if trailing_volatility.notna().any().any() else 0.02 * np.sqrt(horizon)
    scale = np.where(np.isfinite(scale) & (scale > 0), scale, fallback)
    forward = close.shift(-(horizon + 1)).div(close.shift(-1)).sub(1)
    keys = pd.MultiIndex.from_frame(frame[["date", "symbol"]])
    current_price = close.stack().reindex(keys).to_numpy()
    realized = forward.stack().reindex(keys).to_numpy()
    if engine == "qlib":
        expected = np.clip(frame["score"].to_numpy(dtype=float), -0.5, 0.5)
    else:
        expected = frame["score_z"].to_numpy(dtype=float) * scale * 0.5
    logit = np.clip(frame["score_z"].to_numpy(dtype=float) * 0.9, -8, 8)
    probability = 1 / (1 + np.exp(-logit))
    direction = np.where(probability >= 0.55, "up", np.where(probability <= 0.45, "down", "neutral"))

    outcome = pd.Series(np.sign(frame["score_z"].to_numpy()) == np.sign(realized), index=frame.index, dtype="float64")
    outcome[~np.isfinite(realized)] = np.nan
    outcome[frame["score_z"].to_numpy() == 0] = np.nan
    hit_rate = outcome.groupby(frame["symbol"], sort=False).transform(lambda series: series.shift(1).expanding(min_periods=10).mean())
    hit_rate = hit_rate.fillna(0.5)
    probability_confidence = np.abs(probability - 0.5) * 2
    historical_edge = np.clip((hit_rate.to_numpy() - 0.5) * 2, -0.5, 1.0)
    confidence = np.clip(0.5 + 0.5 * probability_confidence * (0.65 + 0.35 * historical_edge), 0, 1)

    annotated = frame.copy()
    annotated["close"] = current_price
    annotated["expected_return"] = expected
    annotated["prob_up"] = probability
    annotated["direction"] = direction
    annotated["confidence"] = confidence
    annotated["hit_rate"] = hit_rate.to_numpy()
    annotated["forward_return"] = realized
    annotated["correct"] = np.where(np.isfinite(realized), outcome.to_numpy(), np.nan)
    annotated["horizon"] = int(horizon)
    annotated["engine"] = engine
    return annotated[
        [
            "date",
            "symbol",
            "horizon",
            "close",
            "score",
            "score_z",
            "expected_return",
            "prob_up",
            "direction",
            "confidence",
            "hit_rate",
            "forward_return",
            "correct",
            "engine",
        ]
    ]


def _raw_from_qlib(store: Store, horizons: list[int], force: bool) -> pd.DataFrame:
    from . import qlib_engine

    forecasts = qlib_engine.build_forecasts(store, force=force)
    if forecasts.empty:
        return pd.DataFrame(columns=["date", "symbol", "horizon", "score", "engine"])
    forecasts = forecasts[forecasts["horizon"].isin(horizons)].copy()
    forecasts["date"] = pd.to_datetime(forecasts["date"]).dt.normalize()
    return forecasts


def build_directions(
    store: Store,
    horizons: tuple[int, ...] = tuple(HORIZONS.values()),
    *,
    force: bool = False,
    engine: str = "auto",
) -> pd.DataFrame:
    """Build calibrated multi-horizon up/down forecasts and persist them."""
    horizons = tuple(sorted({int(horizon) for horizon in horizons}))
    destination = _artifact_dir(store)
    path = destination / "directions.parquet"
    fingerprint = _fingerprint(store)
    meta = {}
    if _meta_path(store).exists():
        try:
            meta = json.loads(_meta_path(store).read_text())
        except (OSError, ValueError):
            meta = {}
    if not force and path.exists() and meta.get("fingerprint") == fingerprint and set(horizons).issubset(set(meta.get("horizons", []))):
        cached = pd.read_parquet(path)
        cached["date"] = pd.to_datetime(cached["date"]).dt.date
        return cached[cached["horizon"].isin(horizons)]

    close = _close_panel(store)
    if close.empty:
        return pd.DataFrame()
    raw_frames: list[pd.DataFrame] = []
    qlib_used = False
    qlib_error: str | None = None
    requested_engine = engine
    missing = list(horizons)
    if engine in {"auto", "qlib"}:
        try:
            from . import qlib_engine

            if qlib_engine.engine_status(store).get("available"):
                qlib_raw = _raw_from_qlib(store, list(horizons), force)
                if not qlib_raw.empty:
                    raw_frames.append(qlib_raw)
                    qlib_used = True
                    missing = [horizon for horizon in horizons if horizon not in set(qlib_raw["horizon"])]
                elif not qlib_error:
                    qlib_error = "Qlib returned no predictions"
            else:
                qlib_error = "pyqlib is not installed"
        except Exception as exc:  # noqa: BLE001 - degrade to native forecasts
            qlib_error = str(exc)
            log.warning("qlib_forecast_fallback", error=str(exc))
    if missing:
        native = _native_raw(store, missing)
        if not native.empty:
            raw_frames.append(native)
    if not raw_frames:
        return pd.DataFrame()
    raw = pd.concat(raw_frames, ignore_index=True)
    annotated: list[pd.DataFrame] = []
    for horizon in horizons:
        subset = raw[raw["horizon"] == horizon]
        if subset.empty:
            continue
        selected_engine = str(subset["engine"].iloc[0])
        annotated.append(_annotate(subset, close, horizon, selected_engine))
    if not annotated:
        return pd.DataFrame()
    result = pd.concat(annotated, ignore_index=True)
    result["date"] = pd.to_datetime(result["date"]).dt.date
    result = result.dropna(subset=["score_z"]).sort_values(["date", "horizon", "score_z"], ascending=[True, True, False])
    destination.mkdir(parents=True, exist_ok=True)
    result.to_parquet(path, index=False)
    final_engine = "qlib" if qlib_used else "native"
    _meta_path(store).write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "built_at": datetime.now(UTC).isoformat(),
                "horizons": sorted(result["horizon"].unique().tolist()),
                "rows": int(len(result)),
                "first_date": str(result["date"].min()),
                "last_date": str(result["date"].max()),
                "symbols": int(result["symbol"].nunique()),
                "engines": sorted(result["engine"].dropna().unique().tolist()),
                "engine": final_engine,
                "requested_engine": requested_engine,
                "qlib_horizons": sorted(result.loc[result["engine"] == "qlib", "horizon"].unique().tolist()),
                "native_horizons": sorted(result.loc[result["engine"] == "native", "horizon"].unique().tolist()),
                "qlib_error": qlib_error,
            },
            indent=2,
            default=str,
        )
    )
    return result


def load_directions(store: Store) -> pd.DataFrame:
    path = _artifact_dir(store) / "directions.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError):
        return pd.DataFrame()
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame


def get_forecast_frame(store: Store) -> pd.DataFrame:
    frame = load_directions(store)
    if frame.empty:
        try:
            from . import qlib_engine

            frame = qlib_engine.load_raw_forecasts(store)
        except Exception:  # noqa: BLE001
            return pd.DataFrame()
    if frame.empty:
        return frame
    out = frame[["date", "symbol", "horizon", "engine"]].copy()
    out["score"] = frame["score_z"] if "score_z" in frame.columns else frame["score"]
    return out


def _artifact_path(store: Store):
    return _artifact_dir(store) / "directions.parquet"


def _parquet_summary(store: Store, path) -> dict[str, Any]:  # noqa: ANN001 - Path type is stdlib
    frame = store.query(
        "SELECT count(*) AS rows, min(date) AS first_date, max(date) AS last_date, count(DISTINCT symbol) AS symbols FROM read_parquet(?)",
        [str(path)],
    )
    return frame.iloc[0].to_dict() if not frame.empty else {}


def directions_meta(store: Store) -> dict[str, Any]:
    path = _artifact_path(store)
    meta: dict[str, Any] = {}
    if _meta_path(store).exists():
        try:
            meta = json.loads(_meta_path(store).read_text())
        except (OSError, ValueError):
            meta = {}
    if path.exists():
        if "rows" not in meta:
            meta.update(_parquet_summary(store, path))
        for key in ("first_date", "last_date"):
            if meta.get(key):
                meta[key] = pd.Timestamp(meta[key]).date().isoformat()
        meta["available"] = int(meta.get("rows") or 0) > 0
    else:
        meta["available"] = False
    meta["horizons"] = [{"key": key, "label": label, "sessions": HORIZONS[key]} for key, label in HORIZON_LABELS.items()]
    if meta["available"]:
        meta.setdefault("first_date", None)
        meta.setdefault("last_date", None)
        meta.setdefault("symbols", 0)
        meta.setdefault("engines", [meta.get("engine")] if meta.get("engine") else [])
    return meta


def snapshot(
    store: Store,
    horizon: int = 21,
    on: Any | None = None,
    limit: int = 25,
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    path = _artifact_path(store)
    empty = {"available": False, "horizon": int(horizon), "date": None, "items": [], "count": 0}
    if not path.exists():
        return empty
    horizon = int(horizon)
    stats = store.query(
        "SELECT min(date) AS first_date, max(date) AS last_date, count(*) AS rows FROM read_parquet(?) WHERE horizon = ?",
        [str(path), horizon],
    )
    if stats.empty or int(stats.iloc[0]["rows"] or 0) == 0:
        return empty
    first_date = stats.iloc[0]["first_date"]
    last_date = stats.iloc[0]["last_date"]
    if on is not None:
        selected_frame = store.query(
            "SELECT max(date) AS selected FROM read_parquet(?) WHERE horizon = ? AND date <= CAST(? AS DATE)",
            [str(path), horizon, pd.Timestamp(on).date()],
        )
        selected = selected_frame.iloc[0]["selected"] if not selected_frame.empty else None
        selected = selected or last_date
    else:
        selected = last_date
    selected_date = pd.Timestamp(selected).date()
    day = store.query(
        "SELECT * FROM read_parquet(?) WHERE horizon = ? AND date = CAST(? AS DATE) AND confidence >= ? ORDER BY score_z DESC",
        [str(path), horizon, selected_date, float(min_confidence)],
    )
    day = day.reset_index(drop=True)
    count = len(day)
    if count:
        day["rank"] = np.arange(1, count + 1)
        day["percentile"] = (count - day["rank"]) / max(count - 1, 1)
    items = _records(day)
    bullish = int((day["direction"] == "up").sum()) if count else 0
    bearish = int((day["direction"] == "down").sum()) if count else 0
    neutral = int((day["direction"] == "neutral").sum()) if count else 0
    return {
        "available": True,
        "horizon": horizon,
        "horizon_key": HORIZON_TO_KEY.get(horizon),
        "date": selected_date.isoformat(),
        "count": count,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "avg_confidence": float(day["confidence"].mean()) if count else 0.0,
        "avg_prob_up": float(day["prob_up"].mean()) if count else 0.0,
        "avg_hit_rate": float(day["hit_rate"].where(day["hit_rate"] > 0).mean()) if count else 0.0,
        "expected_return": float(day["expected_return"].mean()) if count else 0.0,
        "engine": day["engine"].mode().iloc[0] if count else None,
        "first_date": pd.Timestamp(first_date).date().isoformat(),
        "last_date": pd.Timestamp(last_date).date().isoformat(),
        "up": items[:limit],
        "down": items[-limit:][::-1] if count else [],
        "items": items,
    }


def symbol_history(store: Store, symbol: str, horizon: int | None = None, limit: int = 500) -> dict[str, Any]:
    path = _artifact_path(store)
    symbol = symbol.upper()
    if not path.exists():
        return {"symbol": symbol, "items": []}
    sql = "SELECT * FROM read_parquet(?) WHERE symbol = ?"
    params: list[Any] = [str(path), symbol]
    if horizon is not None:
        sql += " AND horizon = ?"
        params.append(int(horizon))
    sql += " ORDER BY date DESC, horizon DESC LIMIT ?"
    params.append(int(limit))
    frame = store.query(sql, params)
    if frame.empty:
        return {"symbol": symbol, "items": []}
    frame = frame.sort_values(["date", "horizon"]).reset_index(drop=True)
    return {"symbol": symbol, "items": _records(frame)}


def forecast_scores(store: Store, horizon: int, since: date | None = None) -> pd.DataFrame:
    """Load only one horizon's forecast scores for strategy blending."""
    path = _artifact_path(store)
    if not path.exists():
        return pd.DataFrame(columns=["date", "symbol", "score"])
    sql = "SELECT date, symbol, score_z AS score FROM read_parquet(?) WHERE horizon = ?"
    params: list[Any] = [str(path), int(horizon)]
    if since is not None:
        sql += " AND date >= ?"
        params.append(since)
    frame = store.query(sql, params)
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    out = frame.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[column]):
            out[column] = out[column].dt.strftime("%Y-%m-%d")
        elif out[column].dtype == object:
            out[column] = out[column].map(_safe)
    return json.loads(out.to_json(orient="records", date_format="iso"))


def _safe(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def qlib_enabled() -> bool:
    return os.getenv("FINHOUND_QLIB", "1") != "0"
