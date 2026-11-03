from __future__ import annotations

from typing import Protocol

import pandas as pd


class PaidHistoricalProvider(Protocol):
    """Adapter seam for a licensed vendor.

    Implementations must return one row per symbol/session with date, open,
    high, low, close, volume, adjusted, and optional corporate-action fields.
    """

    def historical(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame: ...
