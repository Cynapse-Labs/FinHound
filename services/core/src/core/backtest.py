from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import structlog

from .store import Store

log = structlog.get_logger()


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


def capped_equal_weights(
    symbols: pd.Index,
    gross_exposure: float,
    cap: float,
    sign: float = 1.0,
) -> pd.Series:
    """Allocate equal weights up to the cap and explicitly leave residual cash."""
    if cap <= 0:
        raise ValueError("cap must be positive")
    if gross_exposure < 0:
        raise ValueError("gross_exposure cannot be negative")
    if len(symbols) == 0 or gross_exposure == 0:
        return pd.Series(0.0, index=symbols, dtype=float)
    capacity = len(symbols) * cap
    if capacity < gross_exposure:
        log.warning(
            "portfolio_capacity_below_target",
            requested_gross_exposure=gross_exposure,
            available_capacity=capacity,
            symbol_count=len(symbols),
            max_position_weight=cap,
        )
    weight = min(gross_exposure / len(symbols), cap)
    return pd.Series(sign * weight, index=symbols, dtype=float)


def rebalance_dates(index: pd.Index, frequency: str) -> pd.Series:
    dates = pd.Series(pd.to_datetime(index), index=index)
    if frequency == "daily":
        return pd.Series(True, index=index)
    if frequency not in {"weekly", "monthly"}:
        raise ValueError("frequency must be daily, weekly, or monthly")
    periods = dates.dt.to_period("W-FRI" if frequency == "weekly" else "M")
    return periods.ne(periods.shift(1))


def build_target_weights(
    signal: pd.DataFrame,
    tradable: list[str],
    quantile: float,
    cap: float,
    mode: str,
    frequency: str,
) -> tuple[pd.DataFrame, pd.Series]:
    signal_p = signal.reindex(columns=tradable)
    weights = pd.DataFrame(np.nan, index=signal.index, columns=tradable, dtype=float)
    rebalance = rebalance_dates(signal.index, frequency)
    for day in signal.index[rebalance]:
        valid = signal_p.loc[day].dropna()
        if valid.empty:
            log.warning("empty_signal_day", date=str(day))
            weights.loc[day] = 0.0
            continue
        n = max(1, int(np.ceil(len(valid) * quantile)))
        if mode == "long_short":
            winners = valid.nlargest(n).index
            losers = valid.nsmallest(n).index
            weights.loc[day, winners] = capped_equal_weights(winners, 0.5, cap)
            weights.loc[day, losers] = capped_equal_weights(losers, 0.5, cap, -1.0)
        else:
            winners = valid.nlargest(n).index
            weights.loc[day, winners] = capped_equal_weights(winners, 1.0, cap)
            weights.loc[day, weights.columns.difference(winners)] = 0.0
    weights = weights.ffill().fillna(0.0)
    turnover = weights.diff().abs().sum(axis=1)
    turnover.iloc[0] = weights.iloc[0].abs().sum()
    return weights, turnover


def forward_returns(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Return from the first close after signal date through horizon sessions."""
    if horizon < 1:
        raise ValueError("horizon must be positive")
    return close.shift(-(horizon + 1)).div(close.shift(-1)).sub(1)


def signal_diagnostics(
    signal: pd.DataFrame,
    close: pd.DataFrame,
    horizons: tuple[int, ...] = (1, 5, 21),
    min_names_per_decile: int = 2,
) -> tuple[dict[str, Any], pd.DataFrame]:
    records: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"ic": {}, "rank_ic": {}, "ic_ir": {}, "t_stat": {}, "hit_rate": {}, "decile_spread": {}}
    for horizon in horizons:
        forward = forward_returns(close, horizon)
        panel = pd.concat([signal.stack().rename("score"), forward.stack().rename("forward")], axis=1).dropna()
        daily_ic = panel.groupby(level=0).apply(lambda x: x["score"].corr(x["forward"]))
        daily_rank_ic = panel.groupby(level=0).apply(lambda x: x["score"].rank(method="average").corr(x["forward"].rank(method="average")))
        mean_ic = float(daily_ic.mean()) if not daily_ic.empty else 0.0
        std_ic = float(daily_ic.std(ddof=1)) if len(daily_ic) > 1 else 0.0
        summary["ic"][str(horizon)] = mean_ic
        summary["rank_ic"][str(horizon)] = float(daily_rank_ic.mean()) if not daily_rank_ic.empty else 0.0
        summary["ic_ir"][str(horizon)] = mean_ic / std_ic if std_ic else 0.0
        summary["t_stat"][str(horizon)] = mean_ic / std_ic * np.sqrt(len(daily_ic)) if std_ic else 0.0
        summary["hit_rate"][str(horizon)] = float((daily_ic > 0).mean()) if not daily_ic.empty else 0.0
        for day, group in panel.groupby(level=0):
            names = len(group)
            valid = names >= 10 * min_names_per_decile
            if not valid:
                records.append({"date": day, "horizon": horizon, "kind": "decile_spread", "value": np.nan, "names": names, "valid": False})
                continue
            decile = pd.qcut(group["score"].rank(method="first"), 10, labels=False)
            means = group.assign(decile=decile).groupby("decile", observed=True)["forward"].mean()
            spread = float(means.iloc[-1] - means.iloc[0])
            records.append({"date": day, "horizon": horizon, "kind": "decile_spread", "value": spread, "names": names, "valid": True})
        records.extend(
            {"date": day, "horizon": horizon, "kind": "ic", "value": float(value), "names": len(panel.loc[day]), "valid": True}
            for day, value in daily_ic.items()
        )
        records.extend(
            {"date": day, "horizon": horizon, "kind": "rank_ic", "value": float(value), "names": len(panel.loc[day]), "valid": True}
            for day, value in daily_rank_ic.items()
        )
        valid_spreads = [r["value"] for r in records if r["horizon"] == horizon and r["kind"] == "decile_spread" and r["valid"]]
        summary["decile_spread"][str(horizon)] = float(np.mean(valid_spreads)) if valid_spreads else None
    return summary, pd.DataFrame(records)


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
    weights, turnover = build_target_weights(
        signal_p[tradable],
        tradable,
        float(config.get("quantile", 0.1)),
        float(config.get("max_position_weight", 0.25)),
        config.get("mode", "long_only"),
        config.get("rebalance_frequency", "weekly"),
    )
    executed = weights.shift(1).fillna(0.0)
    returns = close.pct_change().shift(-1).fillna(0.0)
    gross = (executed * returns).sum(axis=1)
    traded_turnover = turnover.shift(1).fillna(0.0)
    short = executed.clip(upper=0).abs().sum(axis=1)
    net = apply_costs(
        traded_turnover,
        gross,
        float(config.get("commission_bps", 1)),
        float(config.get("slippage_bps", 2)),
        float(config.get("borrow_bps", 50)),
        short,
    )
    gross_exposure = executed.abs().sum(axis=1)
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
            "gross_exposure": gross_exposure,
            "turnover": traded_turnover,
        }
    )
    diagnostics, research = signal_diagnostics(signal_p[tradable], close[tradable])
    run_id = store.new_run_id()
    split = config.get("split_date")
    in_sample = net.loc[net.index < pd.Timestamp(split).date()] if split else net
    out_sample = net.loc[net.index >= pd.Timestamp(split).date()] if split else net.iloc[0:0]
    result = {
        "full": metrics(net),
        "in_sample": metrics(in_sample),
        "out_of_sample": metrics(out_sample),
        "average_holdings": float((executed != 0).sum(axis=1).mean()),
        "average_gross_exposure": float(gross_exposure.mean()),
        "annual_turnover": float(traded_turnover.mean() * 252),
        **diagnostics,
    }
    store.save_backtest(
        run_id,
        config,
        result,
        equity,
        executed.reset_index().melt("date", var_name="symbol", value_name="weight"),
        research,
    )
    return run_id, result
