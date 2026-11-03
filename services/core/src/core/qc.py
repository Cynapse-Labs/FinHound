from __future__ import annotations

from datetime import date

import pandas as pd
import pandas_market_calendars as mcal

from .store import Store


def run_qc(store: Store, frame: pd.DataFrame | None = None, today: date | None = None) -> pd.DataFrame:
    prices = frame if frame is not None else store.load_raw()
    if prices.empty:
        return prices
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.date
    store.write_table(prices.drop_duplicates(["symbol", "date"]), "raw_prices")
    issues: list[dict[str, object]] = []
    calendar = mcal.get_calendar("NYSE")
    for symbol, group in prices.groupby("symbol"):
        group = group.sort_values("date").reset_index(drop=True)
        dates = pd.DatetimeIndex(group["date"])
        expected = calendar.valid_days(dates.min(), dates.max()).date
        seen = set(group["date"])
        for day in sorted(set(expected) - seen):
            issues.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "check_name": "missing_trading_day",
                    "severity": "warn",
                    "detail": "no row",
                }
            )
        dupes = group[group.duplicated(["symbol", "date"], keep=False)]
        for _, row in dupes.iterrows():
            issues.append(
                {
                    "date": row.date,
                    "symbol": symbol,
                    "check_name": "duplicate_row",
                    "severity": "error",
                    "detail": "duplicate symbol/date",
                }
            )
        for _, row in group.iterrows():
            checks = [
                (
                    row.close <= 0 or row.open <= 0 or row.high <= 0 or row.low <= 0,
                    "non_positive_price",
                    "error",
                ),
                (row.high < row.low, "high_below_low", "error"),
                (
                    row.open < row.low or row.open > row.high or row.close < row.low or row.close > row.high,
                    "ohlc_outside_range",
                    "error",
                ),
            ]
            for failed, name, severity in checks:
                if failed:
                    issues.append(
                        {
                            "date": row.date,
                            "symbol": symbol,
                            "check_name": name,
                            "severity": severity,
                            "detail": "invalid OHLC",
                        }
                    )
        returns = group["close"].pct_change().abs()
        for idx in returns[returns > 0.4].index:
            issues.append(
                {
                    "date": group.loc[idx, "date"],
                    "symbol": symbol,
                    "check_name": "large_return",
                    "severity": "warn",
                    "detail": f"{returns.loc[idx]:.4f}",
                }
            )
        zero = group["volume"].fillna(0).eq(0)
        if zero.rolling(3).sum().max() >= 3:
            for idx in group.index[zero]:
                issues.append(
                    {
                        "date": group.loc[idx, "date"],
                        "symbol": symbol,
                        "check_name": "zero_volume_streak",
                        "severity": "warn",
                        "detail": "three or more consecutive zero volumes",
                    }
                )
        asof = today or date.today()
        recent = calendar.valid_days(pd.Timestamp(asof) - pd.Timedelta(days=30), pd.Timestamp(asof)).date
        if len(recent) and group["date"].max() < recent[-min(5, len(recent))]:
            issues.append(
                {
                    "date": group["date"].max(),
                    "symbol": symbol,
                    "check_name": "stale_series",
                    "severity": "warn",
                    "detail": "last date older than five trading days",
                }
            )
    issue_frame = pd.DataFrame(issues, columns=["date", "symbol", "check_name", "severity", "detail"])
    store.write_table(issue_frame, "qc_issues")
    errors = issue_frame[issue_frame.severity == "error"]
    bad = set(zip(errors.symbol, errors.date, strict=False))
    clean = prices[~prices.apply(lambda r: (r.symbol, r.date) in bad, axis=1)]
    store.write_table(clean, "clean_prices")
    summary = []
    for symbol, group in prices.groupby("symbol"):
        si = issue_frame[issue_frame.symbol == symbol]
        summary.append(
            {
                "symbol": symbol,
                "first_date": group.date.min(),
                "last_date": group.date.max(),
                "expected_days": len(calendar.valid_days(group.date.min(), group.date.max())),
                "missing_days": len(si[si.check_name == "missing_trading_day"]),
                "error_count": len(si[si.severity == "error"]),
                "warn_count": len(si[si.severity == "warn"]),
            }
        )
    summary_frame = pd.DataFrame(summary)
    store.write_table(summary_frame, "qc_summary")
    return issue_frame
