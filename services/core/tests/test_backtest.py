from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from core.backtest import (
    apply_risk_overlays,
    apply_terminal_returns,
    build_target_weights,
    capped_equal_weights,
    forward_returns,
    rebalance_dates,
    run_backtest,
)


def test_capped_weights_redistribute_excess() -> None:
    weights = capped_equal_weights(pd.Index(list("ABCDEFGHIJ")), 1.0, 0.1)
    assert weights.sum() == pytest.approx(1.0)
    assert weights.max() == pytest.approx(0.1)


def test_capped_weights_hold_cash_when_capacity_is_too_small() -> None:
    weights = capped_equal_weights(pd.Index(["A", "B"]), 1.0, 0.1)
    assert weights.sum() == pytest.approx(0.2)
    assert (weights <= 0.1).all()


@pytest.mark.parametrize(
    ("symbols", "gross", "cap", "expected"),
    [
        ([], 1.0, 0.25, 0.0),
        (["A"], 1.0, 0.25, 0.25),
        (["A", "B"], 1.0, 0.5, 1.0),
    ],
)
def test_capped_weights_edge_cases(symbols: list[str], gross: float, cap: float, expected: float) -> None:
    assert capped_equal_weights(pd.Index(symbols), gross, cap).sum() == pytest.approx(expected)


def test_empty_nan_and_tied_scores_are_safe() -> None:
    dates = pd.Index(pd.to_datetime(["2024-01-02", "2024-01-03"]).date)
    signal = pd.DataFrame({"A": [1.0, float("nan")], "B": [1.0, float("nan")]}, index=dates)
    weights, _ = build_target_weights(signal, ["A", "B"], 0.5, 1.0, "long_only", "daily")
    assert weights.loc[dates[0]].sum() == pytest.approx(1.0)
    assert weights.loc[dates[1]].sum() == pytest.approx(0.0)


def test_weekly_rebalance_marks_first_session_of_week() -> None:
    dates = pd.bdate_range("2024-01-02", periods=10)
    flags = rebalance_dates(pd.Index(dates.date), "weekly")
    assert int(flags.sum()) == 3


def test_weekly_and_monthly_turnover_are_below_daily() -> None:
    dates = pd.Index(pd.bdate_range("2024-01-02", periods=40).date)
    signal = pd.DataFrame(
        [[float((i + j) % 7) for j in range(10)] for i in range(len(dates))],
        index=dates,
        columns=[f"S{i}" for i in range(10)],
    )
    daily = build_target_weights(signal, list(signal.columns), 0.2, 0.25, "long_only", "daily")[1].sum()
    weekly = build_target_weights(signal, list(signal.columns), 0.2, 0.25, "long_only", "weekly")[1].sum()
    monthly = build_target_weights(signal, list(signal.columns), 0.2, 0.25, "long_only", "monthly")[1].sum()
    assert weekly < daily
    assert monthly < daily


def test_forward_ic_return_starts_after_execution_close() -> None:
    dates = pd.Index(pd.bdate_range("2024-01-02", periods=4).date)
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1]}, index=dates)
    forward = forward_returns(close, 1)
    assert forward.loc[dates[0], "A"] == pytest.approx(0.1)


def test_placeholder_null_row_does_not_trigger_delisting() -> None:
    dates = pd.Index(pd.bdate_range("2024-01-02", periods=4).date)
    close = pd.DataFrame({"A": [100.0, 101.0, 102.0, np.nan]}, index=dates)
    returns = apply_terminal_returns(close.pct_change().shift(-1), close, -1.0)
    assert returns.loc[dates[2], "A"] != -1.0


def test_terminal_return_applies_after_material_gap() -> None:
    dates = pd.Index(pd.bdate_range("2024-01-02", periods=60).date)
    close = pd.DataFrame(
        {
            "A": [100.0] * 10 + [np.nan] * 50,
            "B": [100.0] * 60,
        },
        index=dates,
    )
    returns = apply_terminal_returns(close.pct_change().shift(-1), close, -1.0, grace_days=30)
    assert returns.loc[dates[9], "A"] == -1.0


def test_risk_overlays_cap_gross_exposure() -> None:
    dates = pd.Index(pd.bdate_range("2024-01-02", periods=30).date)
    weights = pd.DataFrame(1.0, index=dates, columns=["A", "B"])
    returns = pd.DataFrame(0.001, index=dates, columns=["A", "B"])

    executed, diagnostics = apply_risk_overlays(weights, returns, {"max_gross_exposure": 1.0})

    assert (executed.abs().sum(axis=1) <= 1.0 + 1e-9).all()
    assert diagnostics["max_gross_exposure_observed"] <= 1.0 + 1e-9
    assert diagnostics["drawdown_halts"] == 0


def test_risk_overlays_cut_exposure_on_drawdown() -> None:
    dates = pd.Index(pd.bdate_range("2024-01-02", periods=40).date)
    weights = pd.DataFrame(1.0, index=dates, columns=["A"])
    returns = pd.Series(-0.05, index=dates).to_frame("A")

    executed, diagnostics = apply_risk_overlays(
        weights,
        returns,
        {"drawdown_stop": 0.1, "drawdown_resume": 0.05},
    )

    assert diagnostics["drawdown_halts"] >= 1
    assert executed["A"].iloc[-1] == 0.0


def test_risk_overlays_volatility_target_is_causal() -> None:
    dates = pd.Index(pd.bdate_range("2024-01-02", periods=120).date)
    weights = pd.DataFrame(1.0, index=dates, columns=["A"])
    returns = pd.Series(0.01, index=dates).to_frame("A")
    returns.iloc[::7] = -0.01

    executed, diagnostics = apply_risk_overlays(
        weights,
        returns,
        {"target_volatility": 0.1, "max_gross_exposure": 1.0},
    )

    assert diagnostics["volatility_scale_min"] >= 0.1
    assert diagnostics["volatility_scale_max"] <= 2.0
    assert (executed.abs().sum(axis=1) <= 1.0 + 1e-9).all()
    # Weights cannot react on the first session because no trailing data exists yet.
    assert executed["A"].iloc[0] == pytest.approx(0.0)


def test_risk_overlays_empty_weights() -> None:
    empty = pd.DataFrame()
    executed, diagnostics = apply_risk_overlays(empty, empty, {})

    assert executed.empty
    assert diagnostics["drawdown_halts"] == 0


def test_backtest_accepts_signal_missing_universe_symbols(store) -> None:
    prices = store.query("SELECT date, symbol FROM clean_prices WHERE symbol = 'AAA' ORDER BY date")
    signal = pd.DataFrame(
        {
            "date": pd.to_datetime(prices["date"]).dt.date,
            "symbol": "AAA",
            "score": 1.0,
        }
    )

    run_id, result = run_backtest(
        store,
        signal,
        {
            "quantile": 0.5,
            "max_position_weight": 0.5,
            "mode": "long_only",
            "rebalance_frequency": "weekly",
            "commission_bps": 1.0,
            "slippage_bps": 2.0,
        },
    )

    assert run_id
    assert "CAGR" in result["full"]
