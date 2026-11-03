from __future__ import annotations

import pandas as pd
import pytest
from core.backtest import capped_equal_weights, rebalance_dates


def test_capped_weights_redistribute_excess() -> None:
    weights = capped_equal_weights(pd.Index(list("ABCDEFGHIJ")), 1.0, 0.1)
    assert weights.sum() == pytest.approx(1.0)
    assert weights.max() == pytest.approx(0.1)


def test_weekly_rebalance_marks_first_session_of_week() -> None:
    dates = pd.bdate_range("2024-01-02", periods=10)
    flags = rebalance_dates(pd.Index(dates.date), "weekly")
    assert int(flags.sum()) == 3
