from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "build_universe.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_universe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_sources(directory: Path) -> None:
    nasdaq = pd.DataFrame(
        [
            {"Symbol": "AAPL", "Security Name": "Apple Inc. - Common Stock", "Test Issue": "N", "ETF": "N"},
            {"Symbol": "TRUE", "Security Name": "TrueCar, Inc. - Common Stock", "Test Issue": "N", "ETF": "N"},
            {"Symbol": "QQQ", "Security Name": "Invesco QQQ Trust", "Test Issue": "N", "ETF": "Y"},
            {"Symbol": "TESTX", "Security Name": "Test Corp - Common Stock", "Test Issue": "Y", "ETF": "N"},
            {"Symbol": "WARR", "Security Name": "Warrant Corp - Warrant", "Test Issue": "N", "ETF": "N"},
            {"Symbol": "PREF", "Security Name": "Preferred Corp - Preferred Stock", "Test Issue": "N", "ETF": "N"},
        ]
    )
    other = pd.DataFrame(
        [
            {"ACT Symbol": "BRK.B", "Security Name": "Berkshire Hathaway Inc. - Class B", "Test Issue": "N", "ETF": "N"},
            {"ACT Symbol": "SPY", "Security Name": "SPDR S&P 500 ETF Trust", "Test Issue": "N", "ETF": "Y"},
            {"ACT Symbol": "FUND", "Security Name": "Closed-End Fund Inc.", "Test Issue": "N", "ETF": "N"},
        ]
    )
    nasdaq.to_csv(directory / "nasdaqlisted.txt", sep="|", index=False)
    other.to_csv(directory / "otherlisted.txt", sep="|", index=False)


def test_builder_keeps_common_equities_and_drops_funds(tmp_path) -> None:
    builder = load_builder()
    write_sources(tmp_path)

    entries = builder.build(tmp_path, offline=True)
    symbols = {symbol for symbol, _name in entries}

    assert {"AAPL", "BRK-B", "SPY"} <= symbols
    assert "TRUE" in symbols
    assert symbols.isdisjoint({"QQQ", "TESTX", "WARR", "PREF", "FUND"})


def test_builder_normalises_symbols_and_adds_spy(tmp_path) -> None:
    builder = load_builder()
    write_sources(tmp_path)

    entries = dict(builder.build(tmp_path, offline=True))

    assert "BRK-B" in entries
    assert "BRK.B" not in entries
    assert entries["SPY"] == "SPDR S&P 500 ETF Trust"

    output = tmp_path / "universe.yaml"
    builder.write_universe(output, list(entries.items()), "2015-01-01")
    text = output.read_text()

    assert 'start_date: "2015-01-01"' in text
    assert '{symbol: "SPY", sector: "ETF", tradable: false}' in text
    assert '{symbol: "AAPL", sector: "Unknown", tradable: true}' in text
    assert '{symbol: "TRUE", sector: "Unknown", tradable: true}' in text
    assert yaml.safe_load(text)["symbols"][0]["symbol"] == "SPY"
    assert all(isinstance(item["symbol"], str) for item in yaml.safe_load(text)["symbols"])
