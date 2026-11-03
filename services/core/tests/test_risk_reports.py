from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from core.analytics import drawdown_episodes, rolling_risk


def test_drawdown_episodes_identifies_recovery_and_open_drawdown() -> None:
    dates = pd.bdate_range("2024-01-02", periods=6)
    returns = pd.Series([0.1, -0.2, 0.3, -0.1, -0.1, -0.1], index=dates)
    episodes = drawdown_episodes(returns)

    assert len(episodes) == 2
    assert episodes["recovered"].tolist() == [False, True]
    assert episodes["depth"].min() < -0.1


def test_rolling_risk_is_causal_and_benchmark_aware() -> None:
    dates = pd.bdate_range("2024-01-02", periods=8)
    portfolio = pd.Series([0.01, 0.02, -0.01, 0.01, 0.02, -0.01, 0.01, 0.02], index=dates)
    benchmark = portfolio / 2
    result = rolling_risk(portfolio, window=3, benchmark=benchmark)

    assert result.iloc[:2]["volatility"].isna().all()
    assert np.isfinite(result.iloc[-1]["volatility"])
    assert result.iloc[-1]["beta"] == pytest.approx(2.0)


def test_rolling_risk_rejects_invalid_window() -> None:
    with pytest.raises(ValueError):
        rolling_risk(pd.Series([0.01, 0.02]), window=1)
