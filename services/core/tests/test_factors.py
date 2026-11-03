from __future__ import annotations

import pandas as pd
import pytest
from core.factors import FACTOR_NAMES, compute_factors, factor_catalog


def test_factor_catalog_covers_every_factor_once() -> None:
    catalog = factor_catalog()
    names = [entry["name"] for entry in catalog]

    assert names == list(FACTOR_NAMES)
    assert len(names) == len(set(names))
    assert len(names) >= 50


def test_factor_catalog_entries_are_documented() -> None:
    for entry in factor_catalog():
        assert entry["label"]
        assert entry["group"]
        assert entry["description"]
        assert len(entry["description"]) <= 140


def test_factors_are_persisted_for_the_fixture_universe(factors: pd.DataFrame) -> None:
    assert not factors.empty
    assert set(factors["factor"]) == set(FACTOR_NAMES)
    assert factors["symbol"].nunique() == 6

    coverage = factors.groupby("factor")["symbol"].nunique()
    assert (coverage == 6).all()

    # Every factor is oriented so larger is better; values must be finite after orientation.
    assert factors["standardized"].notna().any()
    assert factors["standardized"].abs().max() <= 25


def test_factors_are_cross_sectionally_standardized(factors: pd.DataFrame) -> None:
    sample = factors[factors["factor"] == "momentum_12_1"].dropna(subset=["standardized"])
    latest = sample["date"].max()
    day = sample[sample["date"] == latest]["standardized"]
    if len(day) > 1:
        assert abs(float(day.mean())) < 1e-6
        assert float(day.std(ddof=0)) == pytest.approx(1.0, abs=1e-6)


def test_new_ohlcv_features_have_observations(factors: pd.DataFrame) -> None:
    expected = {
        "overnight_return",
        "intraday_return",
        "range_position_20",
        "volume_surprise_20",
        "low_market_correlation",
        "residual_momentum_60",
        "volatility_of_volatility",
        "realized_skew_20",
        "momentum_2m",
        "momentum_6m",
        "momentum_12m",
        "momentum_acceleration",
        "trend_acceleration",
        "ema_spread_50_200",
        "price_efficiency_20",
        "autocorrelation_20",
        "realized_variance_20",
        "range_volatility_20",
        "gap_volatility_20",
        "upside_capture_60",
        "downside_capture_60",
        "downside_beta_60",
        "idiosyncratic_vol_60",
        "beta_stability_60",
        "volume_zscore_20",
        "obv_trend_20",
        "money_flow_20",
        "price_volume_corr_20",
        "turnover_trend",
        "max_drawdown_60",
        "drawdown_recovery",
        "breakout_20",
        "breakout_60",
    }
    observed = factors.loc[factors["factor"].isin(expected)].groupby("factor")["raw_value"].count()
    assert set(observed.index) == expected
    assert (observed > 0).all()


def test_factor_inputs_are_validated() -> None:
    with pytest.raises(ValueError, match="requires columns"):
        compute_factors(pd.DataFrame({"date": [], "symbol": []}))
