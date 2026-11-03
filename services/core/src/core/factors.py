from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
import structlog

from .store import Store

log = structlog.get_logger()

# Every factor is oriented so that a larger value is a more attractive long
# signal. This keeps composite construction and the strategy wizard simple.
FACTOR_NAMES = (
    "momentum_12_1",
    "momentum_6_1",
    "momentum_3_1",
    "momentum_1m",
    "momentum_1w",
    "reversal",
    "volatility",
    "volatility_20",
    "volatility_60",
    "downside_vol_60",
    "drawdown_252",
    "distance_high",
    "distance_low_252",
    "rsi_14",
    "macd",
    "bollinger_z_20",
    "atr_14",
    "volume_trend",
    "dollar_volume",
    "amihud_illiquidity",
    "skew_60",
    "trend_50",
    "trend_200",
    "beta_60",
)

FACTOR_METADATA: dict[str, dict[str, str]] = {
    "momentum_12_1": {"label": "Momentum 12-1", "group": "momentum", "description": "12-month return skipping the most recent month."},
    "momentum_6_1": {"label": "Momentum 6-1", "group": "momentum", "description": "6-month return skipping the most recent month."},
    "momentum_3_1": {"label": "Momentum 3-1", "group": "momentum", "description": "3-month return skipping the most recent month."},
    "momentum_1m": {"label": "Momentum 1M", "group": "momentum", "description": "Trailing 21-session total return."},
    "momentum_1w": {"label": "Momentum 1W", "group": "momentum", "description": "Trailing 5-session total return."},
    "reversal": {
        "label": "Short-term reversal",
        "group": "reversal",
        "description": "Negative 1-month return; short-horizon mean reversion.",
    },
    "volatility": {"label": "Low volatility (60D)", "group": "risk", "description": "Negative 60-session realized volatility."},
    "volatility_20": {"label": "Low volatility (20D)", "group": "risk", "description": "Negative 20-session realized volatility."},
    "volatility_60": {"label": "Low volatility (60D)", "group": "risk", "description": "Negative 60-session realized volatility."},
    "downside_vol_60": {
        "label": "Low downside volatility",
        "group": "risk",
        "description": "Negative volatility of negative-return sessions.",
    },
    "drawdown_252": {"label": "Shallow drawdown", "group": "risk", "description": "Negative drawdown from the trailing 52-week high."},
    "distance_high": {"label": "Distance from 52W high", "group": "trend", "description": "Close relative to the trailing 52-week high."},
    "distance_low_252": {"label": "Distance from 52W low", "group": "trend", "description": "Close relative to the trailing 52-week low."},
    "rsi_14": {"label": "RSI (14D)", "group": "reversal", "description": "Centered 14-session relative strength index."},
    "macd": {"label": "MACD", "group": "trend", "description": "12/26 EMA spread normalized by price."},
    "bollinger_z_20": {
        "label": "Bollinger z-score",
        "group": "reversal",
        "description": "Close relative to its 20-session mean and dispersion.",
    },
    "atr_14": {"label": "Low ATR", "group": "risk", "description": "Negative 14-session average true range / price."},
    "volume_trend": {"label": "Volume trend", "group": "liquidity", "description": "5-day average volume vs. 20-day average volume."},
    "dollar_volume": {"label": "Dollar volume", "group": "liquidity", "description": "20-session average dollar volume."},
    "amihud_illiquidity": {"label": "Low illiquidity", "group": "liquidity", "description": "Negative Amihud illiquidity ratio."},
    "skew_60": {"label": "Positive skew", "group": "risk", "description": "Positive 60-session return skewness."},
    "trend_50": {"label": "Trend (50D)", "group": "trend", "description": "Close relative to its 50-session moving average."},
    "trend_200": {"label": "Trend (200D)", "group": "trend", "description": "Close relative to its 200-session moving average."},
    "beta_60": {"label": "Low beta", "group": "risk", "description": "Negative 60-session beta to the market."},
}


def _market_returns(prices: pd.DataFrame) -> pd.Series:
    close = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    returns = close.pct_change()
    if "SPY" in returns.columns:
        return returns["SPY"].rename("market")
    return returns.mean(axis=1).rename("market")


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    relative_strength = gain.div(loss.replace(0, np.nan))
    return 100 - 100 / (1 + relative_strength)


def _symbol_factors(group: pd.DataFrame, market: pd.Series) -> pd.DataFrame:
    group = group.sort_values("date").set_index("date")
    group.index = pd.to_datetime(group.index)
    group.index.name = "date"
    close = group["close"].astype(float)
    high = group["high"].astype(float)
    low = group["low"].astype(float)
    volume = group["volume"].astype(float)
    returns = close.pct_change()
    dollar = close * volume

    def rolling_vol(window: int) -> pd.Series:
        return returns.rolling(window, min_periods=window).std() * np.sqrt(252)

    downside = returns.where(returns < 0, 0.0).rolling(60, min_periods=60).std() * np.sqrt(252)
    rolling_peak = close.rolling(252, min_periods=60).max()
    rolling_trough = close.rolling(252, min_periods=60).min()
    price_std_20 = close.rolling(20, min_periods=20).std()
    true_range = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    market_aligned = market.reindex(group.index)
    covariance = returns.rolling(60, min_periods=60).cov(market_aligned)
    market_variance = market_aligned.rolling(60, min_periods=60).var()

    values = pd.DataFrame(index=group.index)
    values["momentum_12_1"] = close.shift(21).div(close.shift(273)).sub(1)
    values["momentum_6_1"] = close.shift(21).div(close.shift(147)).sub(1)
    values["momentum_3_1"] = close.shift(21).div(close.shift(84)).sub(1)
    values["momentum_1m"] = close.div(close.shift(21)).sub(1)
    values["momentum_1w"] = close.div(close.shift(5)).sub(1)
    values["reversal"] = -values["momentum_1m"]
    values["volatility_60"] = -rolling_vol(60)
    values["volatility"] = values["volatility_60"]
    values["volatility_20"] = -rolling_vol(20)
    values["downside_vol_60"] = -downside
    values["drawdown_252"] = close.div(rolling_peak).sub(1)
    values["distance_high"] = close.div(rolling_peak).sub(1)
    values["distance_low_252"] = close.div(rolling_trough).sub(1)
    values["rsi_14"] = (_rsi(close) - 50) / 50
    values["macd"] = (ema12 - ema26).div(close)
    values["bollinger_z_20"] = close.sub(close.rolling(20, min_periods=20).mean()).div(price_std_20)
    values["atr_14"] = -true_range.rolling(14, min_periods=14).mean().div(close)
    values["volume_trend"] = volume.rolling(5, min_periods=5).mean().div(volume.rolling(20, min_periods=20).mean()).sub(1)
    values["dollar_volume"] = dollar.rolling(20, min_periods=20).mean()
    values["amihud_illiquidity"] = -returns.abs().div(dollar.replace(0, np.nan)).rolling(20, min_periods=20).mean() * 1e9
    values["skew_60"] = returns.rolling(60, min_periods=60).skew()
    values["trend_50"] = close.div(close.rolling(50, min_periods=50).mean()).sub(1)
    values["trend_200"] = close.div(close.rolling(200, min_periods=200).mean()).sub(1)
    values["beta_60"] = -covariance.div(market_variance.replace(0, np.nan))
    return values.replace([np.inf, -np.inf], np.nan)


def _standardize_series(values: pd.Series) -> pd.Series:
    """Winsorize and z-score one factor cross-section without a full-factor melt."""
    if values.empty:
        return values
    grouped = values.groupby(level=0)
    lower = grouped.transform("quantile", 0.01)
    upper = grouped.transform("quantile", 0.99)
    clipped = values.clip(lower=lower, upper=upper)
    means = clipped.groupby(level=0).transform("mean")
    standard_deviations = clipped.groupby(level=0).transform("std", ddof=0).replace(0, np.nan)
    standardized = (clipped - means) / standard_deviations
    active = clipped.notna().groupby(level=0).transform("sum").gt(1)
    return standardized.where(active, 0.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def compute_factors(
    prices: pd.DataFrame,
    store: Store | None = None,
    return_frame: bool | None = None,
) -> pd.DataFrame:
    """Compute the factor library one factor at a time.

    The long-format factor table is large for a broad universe. Standardizing
    and writing one factor at a time keeps peak memory bounded while preserving
    the exact returned frame when a caller asks for it (``return_frame``).
    """
    if prices.empty:
        return pd.DataFrame(columns=["date", "symbol", "factor", "raw_value", "standardized"])
    prices = prices.sort_values(["symbol", "date"]).copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.normalize()
    market = _market_returns(prices)
    result: list[pd.DataFrame] = []
    for symbol, group in prices.groupby("symbol", sort=False):
        values = _symbol_factors(group, market)
        values["symbol"] = symbol
        result.append(values.set_index("symbol", append=True))
    wide = pd.concat(result).sort_index()
    wide = wide.reindex(columns=list(FACTOR_NAMES))
    del result
    log.info("factor_panel_ready", rows=len(wide), symbols=wide.index.get_level_values(1).nunique())
    if return_frame is None:
        return_frame = store is None

    frames: list[pd.DataFrame] = []
    for index, factor in enumerate(FACTOR_NAMES):
        started = time.perf_counter()
        values = wide[factor]
        standardized = _standardize_series(values)
        keys = values.index
        long = pd.DataFrame(
            {
                "date": keys.get_level_values(0).date,
                "symbol": keys.get_level_values(1).to_numpy(),
                "factor": factor,
                "raw_value": values.to_numpy(dtype=float),
                "standardized": standardized.to_numpy(dtype=float),
            }
        )
        if store is not None:
            store.write_table(long, "factors", replace=index == 0)
            log.info("factor_written", factor=factor, rows=len(long), elapsed=round(time.perf_counter() - started, 1))
        if return_frame:
            frames.append(long)
        del long, standardized, values
    if store is not None:
        store.refresh_factor_stats()
    if return_frame:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame(columns=["date", "symbol", "factor", "raw_value", "standardized"])


def factor_catalog() -> list[dict[str, Any]]:
    return [{"name": name, **FACTOR_METADATA.get(name, {})} for name in FACTOR_NAMES]
