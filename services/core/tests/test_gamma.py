from __future__ import annotations

import pandas as pd
import pytest
from core.gamma import gamma_exposure


def test_gamma_exposure_aggregates_calls_puts_and_flip() -> None:
    chain = pd.DataFrame(
        [
            {"strike": 95, "option_type": "call", "gamma": 0.02, "open_interest": 100, "spot": 100},
            {"strike": 100, "option_type": "call", "gamma": 0.01, "open_interest": 200, "spot": 100},
            {"strike": 105, "option_type": "put", "gamma": 0.02, "open_interest": 100, "spot": 100},
        ]
    )
    result = gamma_exposure(chain)

    assert result["call_gamma_exposure"] == pytest.approx(40000.0)
    assert result["put_gamma_exposure"] == pytest.approx(-20000.0)
    assert result["net_gamma_exposure"] == pytest.approx(20000.0)
    assert result["gamma_flip"] == 102.5
    assert len(result["strikes"]) == 3


def test_gamma_exposure_requires_spot_when_missing() -> None:
    chain = pd.DataFrame([{"strike": 100, "option_type": "call", "gamma": 0.1, "open_interest": 1}])
    with pytest.raises(ValueError, match="spot is required"):
        gamma_exposure(chain)
