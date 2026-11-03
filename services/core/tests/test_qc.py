from __future__ import annotations

import pandas as pd
from core.qc import apply_latest_session_eligibility, run_qc
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


def test_latest_session_eligibility_removes_low_price_and_volume_names() -> None:
    frame = pd.DataFrame(
        [
            {"date": "2024-01-02", "symbol": "GOOD", "close": 10, "volume": 5000},
            {"date": "2024-01-02", "symbol": "CHEAP", "close": 0.99, "volume": 10000},
            {"date": "2024-01-02", "symbol": "THIN", "close": 10, "volume": 4999},
            {"date": "2024-01-01", "symbol": "GOOD", "close": 10, "volume": 5000},
        ]
    )
    eligible, issues = apply_latest_session_eligibility(frame)
    assert eligible["symbol"].unique().tolist() == ["GOOD"]
    assert set(issues["symbol"]) == {"CHEAP", "THIN"}
