from __future__ import annotations

import pandas as pd
import yaml
from ingest.runner import ingest


class Stub:
    def historical(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "open": [1, 1],
                "high": [2, 2],
                "low": [1, 1],
                "close": [1.5, 1.5],
                "volume": [10, 10],
            }
        )


def test_ingestion_is_idempotent(tmp_path) -> None:
    universe = tmp_path / "universe.yaml"
    universe.write_text(yaml.safe_dump({"start_date": "2024-01-01", "symbols": [{"symbol": "AAA"}]}))
    ingest(universe, tmp_path, Stub(), today=pd.Timestamp("2024-01-03").date(), sleep_seconds=0)
    ingest(universe, tmp_path, Stub(), today=pd.Timestamp("2024-01-03").date(), sleep_seconds=0)
    frame = pd.read_parquet(tmp_path / "raw/prices/symbol=AAA/data.parquet")
    assert len(frame) == 2
