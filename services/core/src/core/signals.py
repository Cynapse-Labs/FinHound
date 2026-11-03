from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .store import Store


def _weighted_signal_sql(weights: dict[str, float], liquidity_threshold: float) -> tuple[str, list[Any]]:
    terms = " + ".join("SUM(CASE WHEN factor = ? THEN standardized * ? ELSE 0 END)" for _ in weights)
    params: list[Any] = []
    for name, weight in weights.items():
        params.extend([name, float(weight)])
    params.append(sum(abs(float(weight)) for weight in weights.values()))
    placeholders = ", ".join("?" for _ in weights)
    params.extend(weights.keys())
    score_sql = f"SELECT date, symbol, ({terms}) / ? AS score FROM factors WHERE factor IN ({placeholders}) GROUP BY date, symbol"
    if liquidity_threshold <= 0:
        return score_sql, params
    sql = (
        f"WITH score AS ({score_sql}), "
        "liquidity AS (SELECT date, symbol, raw_value FROM factors WHERE factor = 'dollar_volume') "
        "SELECT score.date, score.symbol, "
        "CASE WHEN liquidity.raw_value < ? THEN NULL ELSE score.score END AS score "
        "FROM score LEFT JOIN liquidity ON score.date = liquidity.date AND score.symbol = liquidity.symbol"
    )
    return sql, [*params, float(liquidity_threshold)]


def generate_signal(store: Store, settings_path: str | Path = "/config/settings.yaml", name: str | None = None) -> pd.DataFrame:
    settings: dict[str, Any] = yaml.safe_load(Path(settings_path).read_text())
    name = name or settings["signal"]["default"]
    definition = settings["signal"]["definitions"][name]
    available = set(store.factor_coverage()["factor"])
    weights = {factor: float(weight) for factor, weight in definition["weights"].items() if factor in available and float(weight) != 0}
    if not weights:
        return pd.DataFrame(columns=["date", "symbol", "score"])
    sql, params = _weighted_signal_sql(weights, float(definition.get("liquidity_min_dollar_volume", 0)))
    out = store.query(sql, params)
    out["date"] = pd.to_datetime(out["date"]).dt.date
    path = store.root / "signals"
    path.mkdir(exist_ok=True)
    out.to_parquet(path / f"{name}.parquet", index=False)
    return out


def generate_baseline_momentum(store: Store) -> pd.DataFrame:
    prices = store.query("SELECT date, symbol, close FROM clean_prices WHERE symbol <> 'SPY'")
    prices["date"] = pd.to_datetime(prices["date"]).dt.date
    close = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    score = close.shift(21).div(close.shift(273)).sub(1)
    full_index = pd.MultiIndex.from_product([score.index, score.columns], names=["date", "symbol"])
    out = score.stack().reindex(full_index).rename("score").reset_index()
    out.columns = ["date", "symbol", "score"]
    out["score"] = out["score"].astype("float64")
    path = store.root / "signals"
    path.mkdir(exist_ok=True)
    out.to_parquet(path / "momentum_12_1_baseline.parquet", index=False)
    return out
