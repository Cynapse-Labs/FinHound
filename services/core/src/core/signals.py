from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .factors import compute_factors
from .store import Store


def generate_signal(store: Store, settings_path: str | Path = "/config/settings.yaml", name: str | None = None) -> pd.DataFrame:
    settings: dict[str, Any] = yaml.safe_load(Path(settings_path).read_text())
    name = name or settings["signal"]["default"]
    definition = settings["signal"]["definitions"][name]
    factors = store.query("SELECT * FROM factors")
    if factors.empty:
        factors = compute_factors(store.query("SELECT * FROM clean_prices"), store)
    pivot = factors.pivot_table(index=["date", "symbol"], columns="factor", values="standardized").reset_index()
    raw_pivot = factors.pivot_table(index=["date", "symbol"], columns="factor", values="raw_value").reset_index()
    score = pd.Series(0.0, index=pivot.index)
    for factor, weight in definition["weights"].items():
        score = score.add(pivot.get(factor, 0.0) * float(weight), fill_value=0)
    liquidity = raw_pivot.get("dollar_volume", pd.Series(0.0, index=pivot.index))
    out = pd.DataFrame({"date": pd.to_datetime(pivot.date).dt.date, "symbol": pivot.symbol, "score": score.astype("float64")})
    out.loc[liquidity < definition.get("liquidity_min_dollar_volume", 0), "score"] = float("nan")
    path = store.root / "signals"
    path.mkdir(exist_ok=True)
    out.to_parquet(path / f"{name}.parquet", index=False)
    return out
