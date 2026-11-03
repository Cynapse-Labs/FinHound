from __future__ import annotations

import pandas as pd
from core.qc import run_qc
from core.store import Store


def make_store(tmp_path):
    return Store(tmp_path)


def test_qc_detects_invalid_ohlc(tmp_path) -> None:
    frame = pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": "AAA", "open": 10, "high": 5, "low": 6, "close": 7, "volume": 1},
        ]
    )
    store = make_store(tmp_path)
    run_qc(store, frame, today=pd.Timestamp("2024-01-03").date())
    issues = store.query("select check_name, severity from qc_issues")
    assert "high_below_low" in issues.check_name.tolist()
    assert store.query("select count(*) n from clean_prices").iloc[0].n == 0
