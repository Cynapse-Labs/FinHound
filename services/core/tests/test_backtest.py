from __future__ import annotations

import pandas as pd
import pytest
from core.backtest import (
    build_target_weights,
    capped_equal_weights,
    forward_returns,
    rebalance_dates,
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
