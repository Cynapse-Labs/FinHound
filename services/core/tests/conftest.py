from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from core.factors import compute_factors
from core.store import Store

SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE", "SPY")


def make_prices(sessions: int = 340) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2022-01-03", periods=sessions)
    rows = []
    for index, symbol in enumerate(SYMBOLS):
        base = 40.0 + index * 12.0
        drift = 0.0005 * (index + 1) if symbol != "SPY" else 0.0003
        returns = drift + rng.normal(0, 0.011, sessions)
        close = base * np.exp(np.cumsum(returns))
        open_ = close * (1 + rng.normal(0, 0.002, sessions))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, sessions)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, sessions)))
        volume = rng.integers(500_000, 3_000_000, sessions).astype(float)
        rows.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": symbol,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "provider": "test",
                    "fetched_at": pd.Timestamp("2026-01-01"),
                    "adjusted": True,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


@pytest.fixture()
def prices() -> pd.DataFrame:
    return make_prices()


@pytest.fixture()
def store(tmp_path, monkeypatch) -> Store:
    monkeypatch.setenv("UNIVERSE_PATH", str(tmp_path / "missing-universe.yaml"))
    instance = Store(tmp_path / "data")
    frame = make_prices()
    instance.write_table(frame, "clean_prices")
    universe = pd.DataFrame(
        {
            "symbol": [symbol for symbol in SYMBOLS if symbol != "SPY"],
            "start_date": pd.Timestamp("2022-01-03").date(),
            "end_date": None,
            "index_name": "test",
            "tradable": True,
        }
    )
    instance.write_table(universe, "universe_membership")
    compute_factors(frame, instance)
    return instance


@pytest.fixture()
def factors(store: Store) -> pd.DataFrame:
    return store.query("SELECT * FROM factors")
