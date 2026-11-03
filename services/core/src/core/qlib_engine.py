from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import structlog

from .store import Store

log = structlog.get_logger()

SUPPORTED_HORIZONS = (1, 5, 21, 63)
FIELDS = ("open", "high", "low", "close", "volume", "vwap")


def _qlib_available() -> bool:
    if os.getenv("FINHOUND_QLIB", "1") == "0":
        return False
    return importlib.util.find_spec("qlib") is not None


def _version() -> str | None:
    try:
        return importlib.metadata.version("pyqlib")
    except importlib.metadata.PackageNotFoundError:
        return None


def _artifact_dir(store: Store) -> Path:
    return store.root / "qlib"


def _meta_path(store: Store) -> Path:
    return _artifact_dir(store) / "meta.json"


def _read_meta(store: Store) -> dict[str, Any]:
    path = _meta_path(store)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _fingerprint(store: Store) -> str:
    frame = store.query(
        """
        SELECT count(*) AS rows, count(DISTINCT symbol) AS symbols,
               min(date) AS first_date, max(date) AS last_date, max(fetched_at) AS last_ingest
        FROM clean_prices
        """
    )
    payload = frame.iloc[0].to_dict() if not frame.empty else {}
    payload["settings"] = {"fields": list(FIELDS)}
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def engine_status(store: Store | None = None) -> dict[str, Any]:
    available = _qlib_available()
    status: dict[str, Any] = {
        "engine": "qlib" if available else "native",
        "available": available,
        "qlib_version": _version(),
        "mode": "auto",
        "reason": None if available else "pyqlib is not installed; native factor forecasts are active",
    }
    if not available:
        status["reason"] = "pyqlib is not installed; native factor forecasts are active"
    if store is not None:
        meta = _read_meta(store)
        fingerprint = _fingerprint(store)
        predictions_path = _artifact_dir(store) / "forecasts_raw.parquet"
        status.update(
            {
                "dataset_ready": bool(meta.get("dataset_fingerprint")) and meta.get("dataset_fingerprint") == fingerprint,
                "predictions_ready": predictions_path.exists() and meta.get("forecast_fingerprint") == fingerprint,
                "last_built": meta.get("built_at"),
                "last_error": meta.get("error"),
                "horizons": meta.get("horizons", []),
                "features": meta.get("features"),
                "train_rows": meta.get("train_rows"),
                "test_rows": meta.get("test_rows"),
                "dataset_fingerprint": meta.get("dataset_fingerprint"),
                "forecast_fingerprint": meta.get("forecast_fingerprint"),
            }
        )
        if available and meta.get("error"):
            status["reason"] = meta["error"]
    return status


def _write_calendar(path: Path, dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(dates) + ("\n" if dates else ""))


def _write_instruments(path: Path, bounds: dict[str, tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{symbol}\t{start}\t{end}" for symbol, (start, end) in sorted(bounds.items())]
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def _write_feature(path: Path, values: pd.Series, calendar: pd.Index) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    aligned = pd.to_numeric(values.reindex(calendar), errors="coerce").to_numpy(dtype="<f4")
    payload = np.hstack([np.array([0.0], dtype="<f4"), aligned]).astype("<f4")
    payload.tofile(path)


def ensure_qlib_dataset(store: Store, force: bool = False) -> Path:
    """Materialize the cleaned price panel into Qlib's local binary format."""
    if not _qlib_available():
        raise RuntimeError("pyqlib is not installed")
    destination = _artifact_dir(store)
    fingerprint = _fingerprint(store)
    meta = _read_meta(store)
    if (
        not force
        and meta.get("dataset_fingerprint") == fingerprint
        and (destination / "calendars" / "day.txt").exists()
        and (destination / "instruments" / "all.txt").exists()
    ):
        return destination

    prices = store.query("SELECT date, symbol, open, high, low, close, volume FROM clean_prices")
    if prices.empty:
        raise RuntimeError("no clean prices available for the Qlib dataset")
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.normalize()
    prices["symbol"] = prices["symbol"].astype(str)
    prices = prices.drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"])
    prices["vwap"] = (prices["high"].astype(float) + prices["low"].astype(float) + prices["close"].astype(float)) / 3.0

    calendar = pd.Index(sorted(prices["date"].dropna().unique()))
    calendar_strings = [pd.Timestamp(value).strftime("%Y-%m-%d") for value in calendar]
    if len(calendar) < 60:
        raise RuntimeError("at least 60 sessions are required to build a Qlib dataset")

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    _write_calendar(destination / "calendars" / "day.txt", calendar_strings)

    bounds: dict[str, tuple[str, str]] = {}
    feature_count = 0
    for symbol, group in prices.groupby("symbol", sort=True):
        group = group.set_index("date")
        valid = group["close"].dropna()
        if valid.empty:
            continue
        bounds[symbol] = (
            pd.Timestamp(valid.index.min()).strftime("%Y-%m-%d"),
            pd.Timestamp(valid.index.max()).strftime("%Y-%m-%d"),
        )
        for field in FIELDS:
            _write_feature(destination / "features" / symbol.lower() / f"{field}.day.bin", group[field], calendar)
            feature_count += 1
    _write_instruments(destination / "instruments" / "all.txt", bounds)

    meta = {
        "dataset_fingerprint": fingerprint,
        "dataset_built_at": datetime.now(UTC).isoformat(),
        "symbols": len(bounds),
        "sessions": len(calendar),
        "features": feature_count,
        "first_date": calendar_strings[0],
        "last_date": calendar_strings[-1],
    }
    _meta_path(store).write_text(json.dumps(meta, indent=2, default=str))
    log.info("qlib_dataset_written", symbols=len(bounds), sessions=len(calendar), features=feature_count)
    return destination


def _ensure_qlib_init(store: Store, force: bool = False) -> None:
    import qlib
    from qlib.constant import REG_US

    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    destination = ensure_qlib_dataset(store, force=force)
    qlib.init(
        provider_uri=str(destination),
        region=REG_US,
        expression_cache=None,
        dataset_cache=None,
        joblib_backend="threading",
        logging_level="ERROR",
        kernels=1,
    )


def _handler_class(horizon: int):  # type: ignore[no-untyped-def]
    from qlib.contrib.data.handler import Alpha158

    class HorizonAlpha158(Alpha158):
        def get_label_config(self):  # type: ignore[no-untyped-def]
            return [f"Ref($close, -{horizon + 1})/Ref($close, -1) - 1"], ["LABEL0"]

    HorizonAlpha158.__name__ = f"Alpha158H{horizon}"
    return HorizonAlpha158


def _model():  # type: ignore[no-untyped-def]
    from qlib.contrib.model.gbdt import LGBModel

    return LGBModel(
        loss="mse",
        learning_rate=0.04,
        num_leaves=31,
        max_depth=6,
        min_data_in_leaf=40,
        feature_fraction=0.85,
        bagging_fraction=0.85,
        bagging_freq=5,
        lambda_l2=1.0,
        num_boost_round=150,
        early_stopping_rounds=15,
        verbosity=-1,
        seed=42,
    )


def _fold_boundaries(calendar: pd.Index, folds: int = 2) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Return expanding train windows and the following out-of-sample window."""
    if len(calendar) < 120:
        raise RuntimeError("at least 120 sessions are required for a Qlib walk-forward model")
    start = pd.Timestamp(calendar[0])
    folds = max(1, min(folds, 4))
    first_train_fraction = 0.5 if folds > 1 else 0.65
    result: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    for index in range(folds):
        train_end_position = first_train_fraction + (1 - first_train_fraction) * (index / max(folds, 1))
        test_end_position = first_train_fraction + (1 - first_train_fraction) * ((index + 1) / folds)
        train_end_index = max(60, min(len(calendar) - 2, int(len(calendar) * train_end_position)))
        test_end_index = max(train_end_index + 1, min(len(calendar), int(len(calendar) * test_end_position)))
        train_end = pd.Timestamp(calendar[train_end_index])
        test_end = pd.Timestamp(calendar[test_end_index - 1])
        result.append((start, train_end, train_end, test_end))
    return result


def build_forecasts(store: Store, horizons: tuple[int, ...] = SUPPORTED_HORIZONS, force: bool = False) -> pd.DataFrame:
    """Train per-horizon LightGBM models over Qlib Alpha158 features."""
    if not _qlib_available():
        raise RuntimeError("pyqlib is not installed")
    from qlib.data.dataset import DatasetH

    destination = _artifact_dir(store)
    forecast_path = destination / "forecasts_raw.parquet"
    fingerprint = _fingerprint(store)
    meta = _read_meta(store)
    if (
        not force
        and forecast_path.exists()
        and meta.get("forecast_fingerprint") == fingerprint
        and set(map(int, meta.get("horizons", []))).issuperset(horizons)
    ):
        return pd.read_parquet(forecast_path)

    _ensure_qlib_init(store, force=force)
    calendar_frame = store.query("SELECT min(date) AS first_date, max(date) AS last_date FROM clean_prices")
    first_date = pd.Timestamp(calendar_frame.iloc[0]["first_date"])
    last_date = pd.Timestamp(calendar_frame.iloc[0]["last_date"])
    calendar = pd.bdate_range(first_date, last_date)
    folds = _fold_boundaries(calendar)
    output: list[pd.DataFrame] = []
    train_rows = 0
    test_rows = 0
    errors: list[str] = []
    for horizon in horizons:
        try:
            handler_class = _handler_class(horizon)
            handler = handler_class(
                instruments="all",
                start_time=first_date.strftime("%Y-%m-%d"),
                end_time=last_date.strftime("%Y-%m-%d"),
                fit_start_time=folds[-1][0].strftime("%Y-%m-%d"),
                fit_end_time=folds[-1][1].strftime("%Y-%m-%d"),
                infer_processors=[],
                learn_processors=[{"class": "DropnaLabel"}],
            )
            predictions: list[pd.Series] = []
            for train_start, train_end, test_start, test_end in folds:
                dataset = DatasetH(
                    handler=handler,
                    segments={
                        "train": (train_start.strftime("%Y-%m-%d"), train_end.strftime("%Y-%m-%d")),
                        "test": (test_start.strftime("%Y-%m-%d"), test_end.strftime("%Y-%m-%d")),
                    },
                )
                model = _model()
                model.fit(dataset)
                prediction = model.predict(dataset, segment="test")
                if prediction is not None and not prediction.empty:
                    predictions.append(prediction)
                    test_rows += len(prediction)
                train_rows += len(dataset.prepare("train", col_set=["feature"]))
            if not predictions:
                raise RuntimeError("the model produced no predictions")
            stacked = pd.concat(predictions)
            stacked = stacked[~stacked.index.duplicated(keep="last")]
            frame = stacked.rename("score").reset_index()
            frame.columns = ["date", "symbol", "score"]
            frame["date"] = pd.to_datetime(frame["date"]).dt.date
            frame["symbol"] = frame["symbol"].astype(str)
            frame["horizon"] = int(horizon)
            frame["engine"] = "qlib"
            output.append(frame)
            log.info("qlib_horizon_trained", horizon=horizon, predictions=len(frame))
        except Exception as exc:  # noqa: BLE001 - keep other horizons alive
            errors.append(f"{horizon}: {exc}")
            log.warning("qlib_horizon_failed", horizon=horizon, error=str(exc))
    if not output:
        message = "; ".join(errors) or "Qlib model training failed"
        meta.update({"error": message, "forecast_fingerprint": None, "built_at": datetime.now(UTC).isoformat()})
        _meta_path(store).write_text(json.dumps(meta, indent=2, default=str))
        raise RuntimeError(message)
    forecasts = pd.concat(output, ignore_index=True)
    forecasts = forecasts.dropna(subset=["score"]).drop_duplicates(["date", "symbol", "horizon"], keep="last")
    destination.mkdir(parents=True, exist_ok=True)
    forecasts.to_parquet(forecast_path, index=False)
    meta.update(
        {
            "forecast_fingerprint": fingerprint,
            "built_at": datetime.now(UTC).isoformat(),
            "horizons": sorted(forecasts["horizon"].unique().tolist()),
            "predictions": int(len(forecasts)),
            "train_rows": train_rows,
            "test_rows": test_rows,
            "error": "; ".join(errors) if errors else None,
            "folds": len(folds),
            "features": "Alpha158",
            "model": "LightGBM",
        }
    )
    _meta_path(store).write_text(json.dumps(meta, indent=2, default=str))
    return forecasts


def load_raw_forecasts(store: Store) -> pd.DataFrame:
    path = _artifact_dir(store) / "forecasts_raw.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["date", "symbol", "horizon", "score", "engine"])
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError):
        return pd.DataFrame(columns=["date", "symbol", "horizon", "score", "engine"])
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame


def reset(store: Store) -> None:
    destination = _artifact_dir(store)
    if destination.exists():
        shutil.rmtree(destination)
    log.info("qlib_artifacts_reset", path=str(destination))
