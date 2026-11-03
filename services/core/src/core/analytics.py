from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _clean_returns(values: pd.Series | Any) -> pd.Series:
    returns = pd.Series(values, dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    return returns


def performance_metrics(
    returns: pd.Series,
    periods: int = 252,
    benchmark: pd.Series | None = None,
    risk_free_rate: float = 0.0,
) -> dict[str, float]:
    """Calculate stable daily performance, tail, and benchmark-relative statistics."""
    if periods <= 0:
        raise ValueError("periods must be positive")
    if not np.isfinite(risk_free_rate):
        raise ValueError("risk_free_rate must be finite")
    series = _clean_returns(returns)
    empty = {
        "CAGR": 0.0,
        "annualized_volatility": 0.0,
        "Sharpe": 0.0,
        "Sortino": 0.0,
        "max_drawdown": 0.0,
        "Calmar": 0.0,
        "hit_rate": 0.0,
        "VaR_95": 0.0,
        "CVaR_95": 0.0,
        "tail_ratio": 0.0,
        "worst_day": 0.0,
        "best_day": 0.0,
        "skew": 0.0,
        "excess_kurtosis": 0.0,
        "omega_ratio": 0.0,
        "benchmark_beta": 0.0,
        "alpha": 0.0,
        "tracking_error": 0.0,
        "information_ratio": 0.0,
        "benchmark_correlation": 0.0,
    }
    if series.empty:
        return empty

    daily_rf = (1 + risk_free_rate) ** (1 / periods) - 1
    excess = series - daily_rf
    volatility = float(excess.std(ddof=1) * np.sqrt(periods)) if len(excess) > 1 else 0.0
    downside = np.minimum(excess.to_numpy(), 0.0)
    downside_deviation = float(np.sqrt(np.mean(downside**2)) * np.sqrt(periods))
    equity = (1 + series).cumprod()
    years = len(series) / periods
    terminal = float(equity.iloc[-1])
    cagr = terminal ** (1 / years) - 1 if terminal > 0 else -1.0
    drawdown = equity / equity.cummax() - 1
    max_drawdown = float(drawdown.min())
    var_95 = float(series.quantile(0.05))
    tail = series[series <= var_95]
    cvar_95 = float(tail.mean()) if not tail.empty else var_95
    upper_tail = float(series.quantile(0.95))
    gains = float(excess[excess > 0].sum())
    losses = float(-excess[excess < 0].sum())

    result = {
        "CAGR": float(cagr),
        "annualized_volatility": volatility,
        "Sharpe": float(excess.mean() / excess.std(ddof=1) * np.sqrt(periods)) if len(excess) > 1 and excess.std(ddof=1) > 0 else 0.0,
        "Sortino": float(excess.mean() * periods / downside_deviation) if downside_deviation > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "Calmar": float(cagr / abs(max_drawdown)) if max_drawdown < 0 else 0.0,
        "hit_rate": float((series > 0).mean()),
        "VaR_95": var_95,
        "CVaR_95": cvar_95,
        "tail_ratio": float(upper_tail / abs(var_95)) if var_95 < 0 else 0.0,
        "worst_day": float(series.min()),
        "best_day": float(series.max()),
        "skew": float(series.skew()) if len(series) > 2 and np.isfinite(series.skew()) else 0.0,
        "excess_kurtosis": float(series.kurt()) if len(series) > 3 and np.isfinite(series.kurt()) else 0.0,
        "omega_ratio": gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0),
        **{key: 0.0 for key in ("benchmark_beta", "alpha", "tracking_error", "information_ratio", "benchmark_correlation")},
    }

    if benchmark is not None:
        aligned = pd.concat({"portfolio": series, "benchmark": _clean_returns(benchmark)}, axis=1).dropna()
        if len(aligned) > 1:
            portfolio = aligned["portfolio"]
            market = aligned["benchmark"]
            covariance = portfolio.cov(market)
            variance = market.var(ddof=1)
            beta = covariance / variance if variance > 0 else 0.0
            active = portfolio - market
            tracking_error = float(active.std(ddof=1) * np.sqrt(periods))
            result.update(
                benchmark_beta=float(beta),
                alpha=float((portfolio.mean() - beta * market.mean()) * periods),
                tracking_error=tracking_error,
                information_ratio=float(active.mean() / active.std(ddof=1) * np.sqrt(periods)) if active.std(ddof=1) > 0 else 0.0,
                benchmark_correlation=float(portfolio.corr(market)) if market.std(ddof=1) > 0 else 0.0,
            )
    return {key: (0.0 if not np.isfinite(value) else float(value)) for key, value in result.items()}


def drawdown_episodes(returns: pd.Series) -> pd.DataFrame:
    """Return peak-to-recovery drawdown episodes ordered by severity."""
    series = _clean_returns(returns)
    columns = ["start", "trough", "end", "depth", "days_to_trough", "days_underwater", "recovered"]
    if series.empty:
        return pd.DataFrame(columns=columns)
    equity = (1 + series).cumprod()
    drawdown = equity.div(equity.cummax()).sub(1)
    episodes: list[dict[str, Any]] = []
    in_episode = False
    start = trough = None
    trough_depth = 0.0
    for timestamp, value in drawdown.items():
        if value < 0 and not in_episode:
            in_episode = True
            start = timestamp
            trough = timestamp
            trough_depth = float(value)
        elif in_episode and value < trough_depth:
            trough = timestamp
            trough_depth = float(value)
        if in_episode and value >= 0:
            episodes.append(
                {
                    "start": start,
                    "trough": trough,
                    "end": timestamp,
                    "depth": trough_depth,
                    "days_to_trough": int(series.index.get_loc(trough) - series.index.get_loc(start)),
                    "days_underwater": int(series.index.get_loc(timestamp) - series.index.get_loc(start)),
                    "recovered": True,
                }
            )
            in_episode = False
    if in_episode:
        last = series.index[-1]
        episodes.append(
            {
                "start": start,
                "trough": trough,
                "end": None,
                "depth": trough_depth,
                "days_to_trough": int(series.index.get_loc(trough) - series.index.get_loc(start)),
                "days_underwater": int(series.index.get_loc(last) - series.index.get_loc(start) + 1),
                "recovered": False,
            }
        )
    result = pd.DataFrame(episodes, columns=columns)
    if result.empty:
        return result
    return result.sort_values(["depth", "start"], ascending=[True, True], ignore_index=True)


def rolling_risk(
    returns: pd.Series,
    window: int = 63,
    benchmark: pd.Series | None = None,
    periods: int = 252,
) -> pd.DataFrame:
    """Calculate causal rolling volatility, Sharpe, drawdown, and beta."""
    if window < 2:
        raise ValueError("window must be at least 2")
    if periods <= 0:
        raise ValueError("periods must be positive")
    series = _clean_returns(returns)
    if series.empty:
        return pd.DataFrame(columns=["date", "volatility", "sharpe", "drawdown", "beta"])
    frame = pd.DataFrame({"return": series})
    frame["volatility"] = frame["return"].rolling(window, min_periods=window).std(ddof=1) * np.sqrt(periods)
    frame["sharpe"] = (
        frame["return"].rolling(window, min_periods=window).mean()
        / frame["return"].rolling(window, min_periods=window).std(ddof=1)
        * np.sqrt(periods)
    )
    equity = (1 + frame["return"]).cumprod()
    frame["drawdown"] = equity / equity.cummax() - 1
    frame["beta"] = 0.0
    if benchmark is not None:
        aligned = pd.concat({"portfolio": series, "benchmark": _clean_returns(benchmark)}, axis=1)
        covariance = aligned["portfolio"].rolling(window, min_periods=window).cov(aligned["benchmark"])
        variance = aligned["benchmark"].rolling(window, min_periods=window).var(ddof=1)
        frame["beta"] = covariance.div(variance.replace(0, np.nan)).reindex(frame.index)
    result = frame.drop(columns="return").reset_index(names="date")
    result["volatility"] = result["volatility"].replace([np.inf, -np.inf], np.nan)
    result["sharpe"] = result["sharpe"].replace([np.inf, -np.inf], np.nan)
    result["beta"] = result["beta"].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return result
