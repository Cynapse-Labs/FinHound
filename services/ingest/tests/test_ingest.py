from __future__ import annotations

from datetime import date

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


class DateIndexStub:
    def historical(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "open": [1, 1],
                "high": [2, 2],
                "low": [1, 1],
                "close": [1.5, 1.5],
                "volume": [10, 10],
            },
            index=pd.Index([date(2024, 1, 2), date(2024, 1, 3)], name="date"),
        )


class PlaceholderStub:
    def historical(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
                "open": [1, 1, None],
                "high": [2, 2, None],
                "low": [1, 1, None],
                "close": [1.5, 1.5, None],
                "volume": [10, 10, None],
            }
        )


def test_ingestion_is_idempotent(tmp_path) -> None:
    universe = tmp_path / "universe.yaml"
    universe.write_text(yaml.safe_dump({"start_date": "2024-01-01", "symbols": [{"symbol": "AAA"}]}))
    ingest(universe, tmp_path, Stub(), today=pd.Timestamp("2024-01-03").date(), sleep_seconds=0)
    ingest(universe, tmp_path, Stub(), today=pd.Timestamp("2024-01-03").date(), sleep_seconds=0)
    frame = pd.read_parquet(tmp_path / "raw/prices/symbol=AAA/data.parquet")
    assert len(frame) == 2


def test_ingestion_accepts_date_index(tmp_path) -> None:
    universe = tmp_path / "universe.yaml"
    universe.write_text(yaml.safe_dump({"start_date": "2024-01-01", "symbols": [{"symbol": "AAA"}]}))
    ingest(universe, tmp_path, DateIndexStub(), today=pd.Timestamp("2024-01-03").date(), sleep_seconds=0)
    frame = pd.read_parquet(tmp_path / "raw/prices/symbol=AAA/data.parquet")
    assert list(frame["date"]) == [date(2024, 1, 2), date(2024, 1, 3)]


def test_ingestion_drops_null_placeholder_session(tmp_path) -> None:
    universe = tmp_path / "universe.yaml"
    universe.write_text(yaml.safe_dump({"start_date": "2024-01-01", "symbols": [{"symbol": "AAA"}]}))
    ingest(universe, tmp_path, PlaceholderStub(), today=pd.Timestamp("2024-01-04").date(), sleep_seconds=0)
    frame = pd.read_parquet(tmp_path / "raw/prices/symbol=AAA/data.parquet")
    assert list(frame["date"]) == [date(2024, 1, 2), date(2024, 1, 3)]
    assert frame["close"].notna().all()
