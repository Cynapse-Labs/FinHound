from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = {"strike", "option_type", "gamma", "open_interest"}
OPTION_TYPES = {"call", "put", "c", "p"}


def _normalise_chain(chain: pd.DataFrame, spot: float | None = None) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS.difference(chain.columns))
    if missing:
        raise ValueError(f"option chain requires columns: {', '.join(missing)}")
    frame = chain.copy()
    frame["option_type"] = frame["option_type"].astype(str).str.lower().str.strip()
    invalid_types = sorted(set(frame["option_type"].dropna()) - OPTION_TYPES)
    if invalid_types:
        raise ValueError(f"unsupported option_type values: {', '.join(invalid_types)}")
    for column in ("strike", "gamma", "open_interest"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "contract_size" not in frame:
        frame["contract_size"] = 100.0
    frame["contract_size"] = pd.to_numeric(frame["contract_size"], errors="coerce")
    if "spot" not in frame:
        if spot is None:
            raise ValueError("spot is required when option rows do not include spot")
        frame["spot"] = float(spot)
    else:
        frame["spot"] = pd.to_numeric(frame["spot"], errors="coerce")
        if spot is not None:
            frame["spot"] = frame["spot"].fillna(float(spot))
        elif frame["spot"].isna().any():
            raise ValueError("spot is required for every option row or as a request-level value")
    numeric = ["strike", "gamma", "open_interest", "contract_size", "spot"]
    invalid = (
        frame[numeric].isna().any().any()
        or not np.isfinite(frame[numeric].to_numpy(dtype=float)).all()
        or (frame[numeric] <= 0).any().any()
    )
    if invalid:
        raise ValueError("strike, gamma, open_interest, contract_size, and spot must be positive finite values")
    frame["side"] = np.where(frame["option_type"].isin({"call", "c"}), "call", "put")
    # Dollar gamma is the change in option delta for a 1% move in the
    # underlying, aggregated over open contracts.
    frame["gamma_exposure"] = (
        frame["gamma"]
        * frame["open_interest"]
        * frame["contract_size"]
        * frame["spot"].pow(2)
        * 0.01
        * np.where(frame["side"].eq("call"), 1.0, -1.0)
    )
    frame["abs_gamma_exposure"] = frame["gamma_exposure"].abs()
    return frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["gamma_exposure"])


def gamma_exposure(
    chain: pd.DataFrame,
    *,
    spot: float | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Calculate net dollar gamma exposure and strike-level concentration.

    Positive values indicate dealer gamma that tends to dampen moves under the
    standard long-call/short-put dealer sign convention. The result is a
    research estimate, not a substitute for a vendor's dealer-position feed.
    """
    frame = _normalise_chain(chain, spot)
    if symbol is not None and ("symbol" not in frame or frame["symbol"].isna().all()):
        frame["symbol"] = symbol
    if symbol is not None and "symbol" in frame:
        frame = frame.loc[frame["symbol"].astype(str).str.upper() == symbol.upper()].copy()
    if frame.empty:
        return {
            "symbol": symbol.upper() if symbol else None,
            "spot": spot,
            "net_gamma_exposure": 0.0,
            "call_gamma_exposure": 0.0,
            "put_gamma_exposure": 0.0,
            "total_abs_gamma_exposure": 0.0,
            "gamma_flip": None,
            "strikes": [],
        }
    strikes = (
        frame.groupby("strike", as_index=False)
        .agg(
            net_gamma_exposure=("gamma_exposure", "sum"),
            total_abs_gamma_exposure=("abs_gamma_exposure", "sum"),
            contracts=("open_interest", "sum"),
        )
        .sort_values("strike")
    )
    net = strikes["net_gamma_exposure"]
    flip = None
    signs = np.sign(net.to_numpy(dtype=float))
    for index in range(1, len(strikes)):
        if signs[index] != signs[index - 1] and signs[index] != 0 and signs[index - 1] != 0:
            left = float(strikes.iloc[index - 1]["strike"])
            right = float(strikes.iloc[index]["strike"])
            flip = (left + right) / 2
            break
    calls = frame.loc[frame["side"].eq("call"), "gamma_exposure"].sum()
    puts = frame.loc[frame["side"].eq("put"), "gamma_exposure"].sum()
    return {
        "symbol": symbol.upper() if symbol else (str(frame["symbol"].iloc[0]).upper() if "symbol" in frame else None),
        "spot": float(frame["spot"].median()),
        "net_gamma_exposure": float(net.sum()),
        "call_gamma_exposure": float(calls),
        "put_gamma_exposure": float(puts),
        "total_abs_gamma_exposure": float(strikes["total_abs_gamma_exposure"].sum()),
        "gamma_flip": flip,
        "strikes": strikes.to_dict(orient="records"),
    }
