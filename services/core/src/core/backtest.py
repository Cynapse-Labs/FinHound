from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .store import Store


def apply_costs(
    turnover: pd.Series,
    gross: pd.Series,
    commission_bps: float,
    slippage_bps: float,
    borrow_bps: float,
    short_exposure: pd.Series,
) -> pd.Series:
    cost = turnover * (commission_bps + slippage_bps) / 10000
    borrow = short_exposure.abs() * borrow_bps / 10000 / 252
    return gross - cost - borrow


def metrics(returns: pd.Series, periods: int = 252) -> dict[str, float]:
    returns = returns.fillna(0.0)
    if returns.empty:
        return {
            "CAGR": 0.0,
            "annualized_volatility": 0.0,
            "Sharpe": 0.0,
            "Sortino": 0.0,
            "max_drawdown": 0.0,
            "Calmar": 0.0,
            "hit_rate": 0.0,
        }
    equity = (1 + returns).cumprod()
    years = len(returns) / periods
    cagr = equity.iloc[-1] ** (1 / years) - 1 if years and equity.iloc[-1] > 0 else 0.0
    vol = returns.std(ddof=1) * np.sqrt(periods)
    sharpe = returns.mean() / returns.std(ddof=1) * np.sqrt(periods) if vol else 0.0
    downside = returns.where(returns < 0, 0).std(ddof=1) * np.sqrt(periods)
    drawdown = equity / equity.cummax() - 1
    max_dd = float(drawdown.min())
    return {
        "CAGR": float(cagr),
        "annualized_volatility": float(vol),
        "Sharpe": float(sharpe),
        "Sortino": float(returns.mean() * periods / downside) if downside else 0.0,
        "max_drawdown": max_dd,
        "Calmar": float(cagr / abs(max_dd)) if max_dd else 0.0,
        "hit_rate": float((returns > 0).mean()),
    }


def run_backtest(store: Store, signal: pd.DataFrame, config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    prices = store.query("SELECT * FROM clean_prices")
    prices["date"] = pd.to_datetime(prices.date).dt.date
    signal = signal.copy()
    signal["date"] = pd.to_datetime(signal.date).dt.date
    close = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    if config.get("start_date"):
        close = close.loc[close.index >= pd.Timestamp(config["start_date"]).date()]
    if config.get("end_date"):
        close = close.loc[close.index <= pd.Timestamp(config["end_date"]).date()]
    signal_p = signal.pivot(index="date", columns="symbol", values="score").reindex(close.index)
    tradable = [c for c in close.columns if c != "SPY"]
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    quantile = float(config.get("quantile", 0.1))
    for day, row in signal_p.iterrows():
        valid = row[tradable].dropna()
        n = max(1, int(len(valid) * quantile))
        if config.get("mode", "long_only") == "long_short":
            winners, losers = valid.nlargest(n).index, valid.nsmallest(n).index
            weights.loc[day, winners] = 0.5 / n
            weights.loc[day, losers] = -0.5 / n
        else:
            winners = valid.nlargest(n).index
            weights.loc[day, winners] = 1 / n
    cap = float(config.get("max_position_weight", 1.0))
    weights = weights.clip(-cap, cap)
    executed = weights.shift(1).fillna(0.0)
    returns = close.pct_change().fillna(0.0)
    gross = (executed * returns).sum(axis=1)
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    short = executed.clip(upper=0).sum(axis=1)
    net = apply_costs(
        turnover,
        gross,
        float(config.get("commission_bps", 1)),
        float(config.get("slippage_bps", 2)),
        float(config.get("borrow_bps", 50)),
        short,
    )
    universe_returns = returns[tradable].mean(axis=1)
    spy_returns = returns["SPY"] if "SPY" in returns else pd.Series(0.0, index=returns.index)
    curve = (1 + net).cumprod()
    equity = pd.DataFrame(
        {
            "date": close.index,
            "strategy": curve,
            "SPY": (1 + spy_returns).cumprod(),
            "equal_weight": (1 + universe_returns).cumprod(),
            "daily_return": net,
            "drawdown": curve / curve.cummax() - 1,
        }
    )
    run_id = store.new_run_id()
    split = config.get("split_date")
    in_sample = net.loc[net.index < pd.Timestamp(split).date()] if split else net
    out_sample = net.loc[net.index >= pd.Timestamp(split).date()] if split else net.iloc[0:0]
    signal_quality: dict[str, float] = {}
    aligned_signal = signal_p[tradable]
    for horizon in (1, 5, 21):
        forward = close[tradable].pct_change(horizon).shift(-horizon)
        pairs = pd.concat([aligned_signal.stack(), forward.stack()], axis=1).dropna()
        signal_quality[f"ic_{horizon}d"] = float(pairs.iloc[:, 0].corr(pairs.iloc[:, 1])) if len(pairs) > 1 else 0.0
        signal_quality[f"rank_ic_{horizon}d"] = float(pairs.iloc[:, 0].rank().corr(pairs.iloc[:, 1].rank())) if len(pairs) > 1 else 0.0
    result = {
        "full": metrics(net),
        "in_sample": metrics(in_sample),
        "out_of_sample": metrics(out_sample),
        "average_holdings": float((executed != 0).sum(axis=1).mean()),
        "annual_turnover": float(turnover.mean() * 252),
        **signal_quality,
    }
    store.save_backtest(
        run_id,
        config,
        result,
        equity,
        executed.reset_index().melt("date", var_name="symbol", value_name="weight"),
    )
    return run_id, result
