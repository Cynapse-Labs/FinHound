from __future__ import annotations

import pandas as pd
import pytest
from core.backtest import apply_costs, metrics
from core.factors import compute_factors


def test_metrics_hand_computed() -> None:
    result = metrics(pd.Series([0.1, -0.05]))
    assert result["hit_rate"] == pytest.approx(0.5)
    assert result["max_drawdown"] == pytest.approx(-0.05)


def test_costs_reduce_returns() -> None:
    gross = pd.Series([0.01])
    no_cost = apply_costs(pd.Series([0.0]), gross, 0, 0, 0, pd.Series([0.0]))
    cost = apply_costs(pd.Series([1.0]), gross, 10, 10, 0, pd.Series([0.0]))
    assert cost.iloc[0] < no_cost.iloc[0]


def test_factor_truncation_does_not_change_prior_values() -> None:
    dates = pd.date_range("2020-01-01", periods=300, freq="D")
    frame = pd.DataFrame(
        {
            "date": dates,
            "symbol": "AAA",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": range(10, 310),
            "volume": 100000,
        }
    )
    full = compute_factors(frame)
    truncated = compute_factors(frame[frame.date <= dates[250]])
    left = full[full.date <= dates[250].date()].reset_index(drop=True)
    right = truncated.reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)
