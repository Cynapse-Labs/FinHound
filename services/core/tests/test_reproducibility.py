from __future__ import annotations

import pandas as pd
from core.signals import generate_baseline_momentum
from core.store import Store


def test_config_hash_is_stable(tmp_path) -> None:
    store = Store(tmp_path)
    config = {"rebalance_frequency": "weekly", "commission_bps": 1.0}
    first = store.reproducibility_metadata(config)
    second = store.reproducibility_metadata(config)
    assert first["config_hash"] == second["config_hash"]
    assert len(first["config_hash"]) == 64


def test_baseline_signal_uses_contract(tmp_path) -> None:
    store = Store(tmp_path)
    dates = pd.date_range("2020-01-01", periods=280, freq="D")
    frame = pd.DataFrame(
        [
            {"date": day, "symbol": symbol, "close": 100 + i, "volume": 1000}
            for symbol, offset in [("AAA", 0), ("BBB", 2)]
            for i, day in enumerate(dates)
        ]
    )
    store.write_table(frame, "clean_prices")
    result = generate_baseline_momentum(store)
    assert list(result.columns) == ["date", "symbol", "score"]
    assert result["score"].dtype == "float64"
