from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

from .store import Store

ISSUE_COLUMNS = ["date", "symbol", "check_name", "severity", "detail"]


def _issue_frame(prices: pd.DataFrame, mask: pd.Series, check_name: str, severity: str, detail: str) -> pd.DataFrame:
    selected = prices.loc[mask, ["date", "symbol"]].copy()
    if selected.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    selected["check_name"] = check_name
    selected["severity"] = severity
    selected["detail"] = detail
    return selected[ISSUE_COLUMNS]


def _missing_trading_days(prices: pd.DataFrame, calendar) -> pd.DataFrame:  # noqa: ANN001 - mcal calendar type is vendor-specific
    if prices.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    valid_days = pd.DatetimeIndex(calendar.valid_days(prices["date"].min(), prices["date"].max()))
    if valid_days.tz is not None:
        valid_days = valid_days.tz_localize(None)
    records: list[dict[str, object]] = []
    for symbol, group in prices.groupby("symbol", sort=False):
        first = pd.Timestamp(group["date"].min())
        last = pd.Timestamp(group["date"].max())
        expected = valid_days[(valid_days >= first) & (valid_days <= last)].date
        missing = np.setdiff1d(expected, group["date"].to_numpy(), assume_unique=False)
        for day in missing:
            records.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "check_name": "missing_trading_day",
                    "severity": "warn",
                    "detail": "no row",
                }
            )
    return pd.DataFrame(records, columns=ISSUE_COLUMNS)


def run_qc(store: Store, frame: pd.DataFrame | None = None, today: date | None = None) -> pd.DataFrame:
    prices = frame if frame is not None else store.load_raw()
    if prices.empty:
        return prices
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.date
    issues: list[pd.DataFrame] = []

    if "close" in prices.columns:
        blank = prices["close"].isna()
        if blank.any():
            issues.append(
                _issue_frame(
                    prices,
                    blank,
                    "missing_close",
                    "info",
                    "provider placeholder row with no close",
                )
            )
            prices = prices.loc[~blank].reset_index(drop=True)

    store.write_table(prices.drop_duplicates(["symbol", "date"]), "raw_prices")
    calendar = mcal.get_calendar("NYSE")

    if {"open", "high", "low", "close"}.issubset(prices.columns):
        ohlc = prices[["open", "high", "low", "close"]]
        issues.append(_issue_frame(prices, (ohlc <= 0).any(axis=1), "non_positive_price", "error", "invalid OHLC"))
        issues.append(_issue_frame(prices, prices["high"] < prices["low"], "high_below_low", "error", "invalid OHLC"))
        issues.append(
            _issue_frame(
                prices,
                (prices["open"] < prices["low"])
                | (prices["open"] > prices["high"])
                | (prices["close"] < prices["low"])
                | (prices["close"] > prices["high"]),
                "ohlc_outside_range",
                "error",
                "invalid OHLC",
            )
        )

    ordered = prices.sort_values(["symbol", "date"], kind="stable")
    returns = ordered.groupby("symbol", sort=False)["close"].pct_change().abs()
    large = returns > 0.4
    if large.any():
        frame = ordered.loc[large, ["date", "symbol"]].copy()
        frame["check_name"] = "large_return"
        frame["severity"] = "warn"
        frame["detail"] = returns[large].map(lambda value: f"{value:.4f}")
        issues.append(frame[ISSUE_COLUMNS])

    zero = ordered["volume"].fillna(0).eq(0)
    if zero.any():
        streak = zero.astype(int).groupby(ordered["symbol"], sort=False).transform(lambda values: values.rolling(3, min_periods=3).sum())
        flagged = ordered.loc[streak >= 3, "symbol"].unique()
        issues.append(
            _issue_frame(
                ordered,
                zero & ordered["symbol"].isin(flagged),
                "zero_volume_streak",
                "warn",
                "three or more consecutive zero volumes",
            )
        )

    asof = today or date.today()
    recent = calendar.valid_days(pd.Timestamp(asof) - pd.Timedelta(days=30), pd.Timestamp(asof)).date
    if len(recent):
        cutoff = recent[-min(5, len(recent))]
        last_dates = prices.groupby("symbol")["date"].max()
        stale = last_dates[last_dates < cutoff]
        if not stale.empty:
            frame = pd.DataFrame(
                {
                    "date": stale.to_numpy(),
                    "symbol": stale.index,
                    "check_name": "stale_series",
                    "severity": "warn",
                    "detail": "last date older than five trading days",
                }
            )
            issues.append(frame[ISSUE_COLUMNS])

    missing = _missing_trading_days(prices, calendar)
    if not missing.empty:
        issues.append(missing)

    issue_frame = pd.concat([frame for frame in issues if not frame.empty], ignore_index=True)
    if issue_frame.empty:
        issue_frame = pd.DataFrame(columns=ISSUE_COLUMNS)
    store.write_table(issue_frame, "qc_issues")

    errors = issue_frame[issue_frame["severity"] == "error"]
    if errors.empty:
        clean = prices
    else:
        bad_keys = pd.MultiIndex.from_frame(errors[["symbol", "date"]])
        price_keys = pd.MultiIndex.from_frame(prices[["symbol", "date"]])
        clean = prices.loc[~price_keys.isin(bad_keys)]
    store.write_table(clean.drop_duplicates(["symbol", "date"]), "clean_prices")

    grouped = prices.groupby("symbol")
    summary = grouped.agg(first_date=("date", "min"), last_date=("date", "max")).reset_index()
    calendar_days = np.array(calendar.valid_days(summary["first_date"].min(), summary["last_date"].max()).date)
    summary["expected_days"] = (
        np.searchsorted(calendar_days, summary["last_date"].to_numpy(), side="right")
        - np.searchsorted(calendar_days, summary["first_date"].to_numpy(), side="left")
    ).astype(int)
    if issue_frame.empty:
        summary["missing_days"] = 0
        summary["error_count"] = 0
        summary["warn_count"] = 0
    else:
        counts = issue_frame.groupby(["symbol", "check_name"]).size().unstack(fill_value=0)
        if "missing_trading_day" in counts.columns:
            summary["missing_days"] = summary["symbol"].map(counts["missing_trading_day"]).fillna(0).astype(int)
        else:
            summary["missing_days"] = 0
        severity = issue_frame.groupby(["symbol", "severity"]).size().unstack(fill_value=0)
        if "error" in severity.columns:
            summary["error_count"] = summary["symbol"].map(severity["error"]).fillna(0).astype(int)
        else:
            summary["error_count"] = 0
        if "warn" in severity.columns:
            summary["warn_count"] = summary["symbol"].map(severity["warn"]).fillna(0).astype(int)
        else:
            summary["warn_count"] = 0
    store.write_table(
        summary[["symbol", "first_date", "last_date", "expected_days", "missing_days", "error_count", "warn_count"]],
        "qc_summary",
    )
    return issue_frame
