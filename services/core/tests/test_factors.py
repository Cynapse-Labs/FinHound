from __future__ import annotations

import pandas as pd
import pytest
from core.factors import FACTOR_NAMES, factor_catalog


def test_factor_catalog_covers_every_factor_once() -> None:
    catalog = factor_catalog()
    names = [entry["name"] for entry in catalog]

    assert names == list(FACTOR_NAMES)
    assert len(names) == len(set(names))
    assert len(names) >= 20


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
