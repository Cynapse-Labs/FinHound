from __future__ import annotations

import pandas as pd
import yaml
from core.signals import generate_signal
from core.store import Store


def test_signal_handles_missing_raw_factor_rows(tmp_path) -> None:
    store = Store(tmp_path)
    rows = [
        {"date": date, "symbol": symbol, "factor": factor, "raw_value": raw, "standardized": std}
        for date, raw, std in [
            (pd.Timestamp("2024-01-02").date(), float("nan"), 0.0),
            (pd.Timestamp("2024-01-03").date(), 100.0, 1.0),
        ]
        for symbol in ("AAA", "BBB")
        for factor in ("dollar_volume", "momentum_12_1")
    ]
    store.write_table(pd.DataFrame(rows), "factors")
    settings = {
        "signal": {
            "default": "test",
            "definitions": {
                "test": {
                    "weights": {"dollar_volume": 0.5, "momentum_12_1": 0.5},
                    "liquidity_min_dollar_volume": 10,
                }
            },
        }
    }
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(yaml.safe_dump(settings))

    signal = generate_signal(store, settings_path)

    assert len(signal) == 4
    assert signal["score"].notna().all()
    assert (tmp_path / "signals" / "test.parquet").exists()
