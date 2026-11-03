from __future__ import annotations

import numpy as np
import pandas as pd

from .store import Store

FACTOR_NAMES = ("momentum_12_1", "reversal", "volatility", "distance_high", "dollar_volume")


def compute_factors(prices: pd.DataFrame, store: Store | None = None) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=["date", "symbol", "factor", "raw_value", "standardized"])
    prices = prices.sort_values(["symbol", "date"]).copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.date
    result: list[pd.DataFrame] = []
    for symbol, g in prices.groupby("symbol", sort=False):
        g = g.sort_values("date").copy()
        close = g["close"].astype(float)
        dollar = close * g["volume"].astype(float)
        values = pd.DataFrame(
            {
                "date": g["date"],
                "symbol": symbol,
                "momentum_12_1": close.shift(21).div(close.shift(273)).sub(1),
                "reversal": -(close.div(close.shift(21)).sub(1)),
                "volatility": -close.pct_change().rolling(60, min_periods=60).std() * np.sqrt(252),
                "distance_high": close.div(close.rolling(252, min_periods=252).max()).sub(1),
                "dollar_volume": dollar.rolling(20, min_periods=20).mean(),
            }
        )
        result.append(values.melt(["date", "symbol"], var_name="factor", value_name="raw_value"))
    long = pd.concat(result, ignore_index=True)
    long["standardized"] = long.groupby(["date", "factor"])["raw_value"].transform(
        lambda s: (s.clip(s.quantile(0.01), s.quantile(0.99)) - s.clip(s.quantile(0.01), s.quantile(0.99)).mean())
        / s.clip(s.quantile(0.01), s.quantile(0.99)).std(ddof=0)
        if s.notna().sum() > 1
        else 0.0
    )
    long["standardized"] = long["standardized"].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if store:
        store.write_table(long, "factors")
    return long
