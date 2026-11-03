from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from core.analytics import performance_metrics
from core.backtest import (
    apply_costs,
    build_target_weights,
    validate_backtest_config,
)


def test_constant_returns_are_finite() -> None:
    result = performance_metrics(pd.Series([0.01]))
    assert all(np.isfinite(value) for value in result.values())
    assert result["annualized_volatility"] == 0.0


def test_downside_deviation_ignores_positive_returns() -> None:
    result = performance_metrics(pd.Series([0.1, -0.02]), periods=1)
    assert result["Sortino"] == pytest.approx(2.828427, rel=1e-5)


def test_benchmark_relative_metrics_are_reported() -> None:
    portfolio = pd.Series([0.02, -0.01, 0.03, 0.0])
    benchmark = pd.Series([0.01, -0.01, 0.02, 0.0])
    result = performance_metrics(portfolio, benchmark=benchmark)
    assert result["benchmark_beta"] > 0
    assert result["benchmark_correlation"] > 0.9
    assert np.isfinite(result["information_ratio"])


def test_long_short_selection_is_disjoint() -> None:
    signal = pd.DataFrame([[4.0, 3.0, 2.0]], index=pd.Index([pd.Timestamp("2024-01-02").date()]), columns=["A", "B", "C"])
    weights, _ = build_target_weights(signal, list(signal.columns), 0.34, 1.0, "long_short", "daily")
    assert weights.loc[weights.index[0], "A"] > 0
    assert weights.loc[weights.index[0], "B"] > 0
    assert weights.loc[weights.index[0], "C"] < 0


def test_costs_reject_negative_rates() -> None:
    with pytest.raises(ValueError):
        apply_costs(pd.Series([1.0]), pd.Series([0.0]), -1, 0, 0, pd.Series([0.0]))


def test_long_only_rejects_long_fraction() -> None:
    with pytest.raises(ValueError):
        validate_backtest_config({"mode": "long_only", "long_fraction": 0.25})


def test_drawdown_resume_must_not_exceed_stop() -> None:
    with pytest.raises(ValueError):
        validate_backtest_config({"drawdown_stop": 0.1, "drawdown_resume": 0.2})
