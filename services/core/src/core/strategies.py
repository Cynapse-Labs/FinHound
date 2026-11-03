from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .factors import FACTOR_NAMES
from .store import Store

HORIZONS: dict[str, int] = {"1d": 1, "1w": 5, "1m": 21, "3m": 63}
HORIZON_LABELS = {"1d": "1 day", "1w": "1 week", "1m": "1 month", "3m": "3 months"}
ENGINES = ("auto", "native", "qlib")


class StrategyFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_price: float = Field(1.0, ge=0, le=1_000_000)
    min_dollar_volume: float = Field(1_000_000, ge=0)
    trend: Literal["none", "above_50", "above_200", "below_200"] = "none"
    volatility: Literal["none", "low", "high"] = "none"
    min_history: int = Field(252, ge=20, le=2000)


class StrategyPortfolio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["long_only", "long_short"] = "long_only"
    rebalance_frequency: Literal["daily", "weekly", "monthly"] = "weekly"
    quantile: float = Field(0.2, gt=0, le=1)
    max_position_weight: float = Field(0.1, gt=0, le=1)
    gross_exposure: float = Field(1.0, ge=0.05, le=2.0)
    long_fraction: float = Field(0.5, ge=0.05, le=0.95)
    weighting: Literal["equal"] = "equal"


class StrategyRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_volatility: float | None = Field(None, ge=0.01, le=1.5)
    drawdown_stop: float | None = Field(None, ge=0.01, le=0.95)
    drawdown_resume: float | None = Field(None, ge=0.0, le=0.95)
    max_gross_exposure: float = Field(1.0, ge=0.05, le=2.0)

    @model_validator(mode="after")
    def validate_drawdown(self) -> StrategyRisk:
        if self.drawdown_stop is not None:
            resume = self.drawdown_resume if self.drawdown_resume is not None else round(self.drawdown_stop * 0.5, 4)
            if resume > self.drawdown_stop:
                raise ValueError("drawdown_resume must be less than or equal to drawdown_stop")
            self.drawdown_resume = resume
        return self


class StrategyCosts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commission_bps: float = Field(1.0, ge=0, le=1000)
    slippage_bps: float = Field(2.0, ge=0, le=1000)
    borrow_bps: float = Field(50.0, ge=0, le=10000)


class StrategyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=2, max_length=80)
    description: str = Field("", max_length=600)
    category: str = Field("custom", min_length=2, max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=12)
    engine: Literal["auto", "native", "qlib"] = "auto"
    horizon: Literal["1d", "1w", "1m", "3m"] = "1m"
    qlib_blend: float = Field(0.7, ge=0, le=1)
    factor_weights: dict[str, float] = Field(min_length=1)
    filters: StrategyFilters = Field(default_factory=StrategyFilters)
    portfolio: StrategyPortfolio = Field(default_factory=StrategyPortfolio)
    risk: StrategyRisk = Field(default_factory=StrategyRisk)
    costs: StrategyCosts = Field(default_factory=StrategyCosts)
    builtin: bool = False
    version: int = Field(1, ge=1)
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("factor_weights")
    @classmethod
    def validate_weights(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - set(FACTOR_NAMES))
        if unknown:
            raise ValueError(f"unknown factors: {', '.join(unknown)}")
        if not value:
            raise ValueError("at least one factor weight is required")
        cleaned = {name: float(weight) for name, weight in value.items()}
        if any(not np.isfinite(weight) or abs(weight) > 10 for weight in cleaned.values()):
            raise ValueError("factor weights must be finite and within [-10, 10]")
        if sum(abs(weight) for weight in cleaned.values()) == 0:
            raise ValueError("at least one factor weight must be non-zero")
        return cleaned

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        cleaned = []
        for tag in value:
            tag = re.sub(r"\s+", "-", tag.strip().lower())[:24]
            if tag and tag not in cleaned:
                cleaned.append(tag)
        return cleaned


def _strategy(
    strategy_id: str,
    name: str,
    description: str,
    category: str,
    weights: dict[str, float],
    *,
    engine: str = "native",
    horizon: str = "1m",
    mode: str = "long_only",
    frequency: str = "weekly",
    quantile: float = 0.2,
    max_weight: float = 0.1,
    target_volatility: float | None = None,
    drawdown_stop: float | None = None,
    filters: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    qlib_blend: float = 0.7,
) -> StrategyDefinition:
    return StrategyDefinition(
        id=strategy_id,
        name=name,
        description=description,
        category=category,
        tags=tags or [],
        engine=engine,
        horizon=horizon,
        qlib_blend=qlib_blend,
        factor_weights=weights,
        filters=StrategyFilters(**(filters or {})),
        portfolio=StrategyPortfolio(
            mode=mode,
            rebalance_frequency=frequency,
            quantile=quantile,
            max_position_weight=max_weight,
            gross_exposure=1.0,
            long_fraction=0.5,
        ),
        risk=StrategyRisk(target_volatility=target_volatility, drawdown_stop=drawdown_stop),
        builtin=True,
    )


BUILTIN_STRATEGIES: tuple[StrategyDefinition, ...] = (
    _strategy(
        "momentum_12_1_classic",
        "Momentum 12-1 Classic",
        "The academic cross-sectional momentum benchmark: 12-month return skipping the latest month.",
        "Momentum",
        {"momentum_12_1": 1.0},
        filters={"min_dollar_volume": 10_000_000},
        tags=["classic", "cross-sectional"],
    ),
    _strategy(
        "multi_horizon_momentum",
        "Multi-Horizon Momentum",
        "Blends 1, 3, 6 and 12-month momentum to reduce single-window turnover and timing risk.",
        "Momentum",
        {"momentum_12_1": 0.35, "momentum_6_1": 0.3, "momentum_3_1": 0.2, "momentum_1m": 0.15},
        filters={"min_dollar_volume": 5_000_000},
        tags=["momentum", "blend"],
    ),
    _strategy(
        "short_term_reversal",
        "Short-Term Reversal",
        "Buys recent losers and shorts recent winners, exploiting short-horizon mean reversion.",
        "Reversal",
        {"reversal": 0.55, "bollinger_z_20": 0.25, "rsi_14": 0.2},
        mode="long_short",
        frequency="weekly",
        quantile=0.2,
        tags=["reversal", "long-short"],
    ),
    _strategy(
        "low_volatility",
        "Low Volatility Defensive",
        "Prefers the calmest names in the universe with a light liquidity screen.",
        "Defensive",
        {"volatility_60": 0.5, "downside_vol_60": 0.25, "atr_14": 0.15, "beta_60": 0.1},
        frequency="monthly",
        quantile=0.25,
        max_weight=0.1,
        target_volatility=0.12,
        tags=["defensive", "low-vol"],
    ),
    _strategy(
        "quality_low_vol",
        "Quality Low-Vol",
        "Combines low volatility, shallow drawdowns and positive skew for a defensive core.",
        "Defensive",
        {"volatility_60": 0.35, "downside_vol_60": 0.2, "drawdown_252": 0.2, "skew_60": 0.15, "trend_200": 0.1},
        frequency="monthly",
        quantile=0.25,
        target_volatility=0.11,
        drawdown_stop=0.15,
        tags=["defensive", "quality"],
    ),
    _strategy(
        "trend_breakout",
        "Trend Breakout",
        "Requires price strength versus the 50-day trend and proximity to 52-week highs.",
        "Trend",
        {"trend_50": 0.35, "distance_high": 0.25, "macd": 0.2, "momentum_1m": 0.2},
        filters={"trend": "above_50", "min_dollar_volume": 5_000_000},
        tags=["trend", "breakout"],
    ),
    _strategy(
        "dual_momentum",
        "Dual Momentum",
        "Absolute trend plus relative momentum; exposure is trimmed when the 200-day trend fails.",
        "Trend",
        {"momentum_6_1": 0.35, "trend_200": 0.3, "momentum_3_1": 0.2, "volatility_60": 0.15},
        filters={"trend": "above_200"},
        frequency="monthly",
        quantile=0.2,
        tags=["trend", "absolute"],
    ),
    _strategy(
        "mean_reversion_rsi",
        "RSI Mean Reversion",
        "Looks for oversold RSI readings inside otherwise liquid, established names.",
        "Reversal",
        {"rsi_14": 0.45, "reversal": 0.3, "bollinger_z_20": 0.25},
        quantile=0.15,
        filters={"min_price": 5, "min_history": 252},
        tags=["reversal", "rsi"],
    ),
    _strategy(
        "bollinger_reversion",
        "Bollinger Band Reversion",
        "Buys names trading below their 20-day Bollinger band and avoids high-volatility traps.",
        "Reversal",
        {"bollinger_z_20": 0.5, "reversal": 0.25, "volatility_20": 0.15, "volume_trend": 0.1},
        quantile=0.2,
        tags=["reversal", "bands"],
    ),
    _strategy(
        "volume_surge_momentum",
        "Volume Surge Momentum",
        "Rewards positive price momentum confirmed by rising relative volume.",
        "Momentum",
        {"momentum_1m": 0.3, "volume_trend": 0.25, "macd": 0.2, "dollar_volume": 0.15, "trend_50": 0.1},
        filters={"min_dollar_volume": 10_000_000},
        tags=["momentum", "volume"],
    ),
    _strategy(
        "liquidity_leaders",
        "Liquidity Leaders",
        "Overweights deep, liquid names with improving volume and low trading friction.",
        "Liquidity",
        {"dollar_volume": 0.45, "amihud_illiquidity": 0.3, "volume_trend": 0.15, "volatility_60": 0.1},
        frequency="monthly",
        quantile=0.25,
        tags=["liquidity", "capacity"],
    ),
    _strategy(
        "risk_adjusted_momentum",
        "Risk-Adjusted Momentum",
        "Momentum divided by risk: strong trends in low-volatility names rank highest.",
        "Momentum",
        {"momentum_6_1": 0.35, "momentum_3_1": 0.2, "volatility_60": 0.25, "drawdown_252": 0.2},
        tags=["momentum", "risk-adjusted"],
    ),
    _strategy(
        "drawdown_recovery",
        "Drawdown Recovery",
        "Targets names recovering from deep drawdowns with improving short-term trend.",
        "Reversal",
        {"distance_low_252": 0.3, "reversal": 0.25, "trend_50": 0.25, "volume_trend": 0.2},
        quantile=0.2,
        tags=["recovery", "reversal"],
    ),
    _strategy(
        "volatility_regime",
        "Volatility Regime Rotation",
        "Rotates toward low-volatility names when dispersion is high and momentum when it is calm.",
        "Adaptive",
        {"volatility_20": 0.3, "momentum_3_1": 0.25, "macd": 0.2, "bollinger_z_20": 0.15, "trend_50": 0.1},
        engine="auto",
        horizon="1m",
        target_volatility=0.14,
        tags=["adaptive", "volatility"],
    ),
    _strategy(
        "beta_rotation",
        "Low-Beta Rotation",
        "Favours market-independent, low-beta compounders with stable trends.",
        "Defensive",
        {"beta_60": 0.4, "volatility_60": 0.25, "trend_200": 0.2, "drawdown_252": 0.15},
        frequency="monthly",
        target_volatility=0.1,
        tags=["beta", "defensive"],
    ),
    _strategy(
        "high_beta_momentum",
        "High-Beta Momentum",
        "Aggressive momentum sleeve that accepts higher beta and volatility for trend exposure.",
        "Momentum",
        {"momentum_3_1": 0.35, "momentum_1m": 0.25, "beta_60": -0.2, "volume_trend": 0.2},
        quantile=0.15,
        target_volatility=0.2,
        tags=["aggressive", "momentum"],
    ),
    _strategy(
        "composite_alpha",
        "Composite Alpha",
        "Broad ensemble across momentum, trend, reversal, risk and liquidity factors.",
        "Ensemble",
        {
            "momentum_12_1": 0.18,
            "momentum_3_1": 0.12,
            "trend_50": 0.12,
            "reversal": 0.1,
            "volatility_60": 0.16,
            "distance_high": 0.1,
            "dollar_volume": 0.12,
            "amihud_illiquidity": 0.1,
        },
        tags=["ensemble", "balanced"],
    ),
    _strategy(
        "defensive_growth",
        "Defensive Growth",
        "Steady 6-month trend and positive skew, with a hard drawdown circuit breaker.",
        "Defensive",
        {"momentum_6_1": 0.3, "trend_200": 0.25, "skew_60": 0.2, "volatility_60": 0.15, "drawdown_252": 0.1},
        frequency="monthly",
        quantile=0.25,
        drawdown_stop=0.12,
        tags=["defensive", "growth"],
    ),
    _strategy(
        "momentum_crash_protection",
        "Momentum Crash Protection",
        "Momentum with low-volatility and shallow-drawdown guards to reduce crash exposure.",
        "Momentum",
        {"momentum_12_1": 0.4, "volatility_60": 0.25, "downside_vol_60": 0.2, "skew_60": 0.15},
        target_volatility=0.13,
        drawdown_stop=0.18,
        tags=["momentum", "risk-management"],
    ),
    _strategy(
        "trend_reversal_blend",
        "Trend + Reversal Blend",
        "A barbell of medium-term trend and short-term reversal, rebalanced weekly.",
        "Ensemble",
        {"momentum_3_1": 0.3, "reversal": 0.25, "bollinger_z_20": 0.2, "trend_50": 0.15, "volatility_20": 0.1},
        tags=["blend", "barbell"],
    ),
    _strategy(
        "qlib_alpha158",
        "Qlib Alpha158 ML",
        "LightGBM forecast over Qlib's Alpha158 feature set, blended with a native factor prior.",
        "Qlib",
        {"momentum_6_1": 0.25, "trend_50": 0.2, "volatility_60": 0.2, "reversal": 0.15, "dollar_volume": 0.2},
        engine="qlib",
        horizon="1m",
        qlib_blend=0.8,
        tags=["qlib", "machine-learning"],
    ),
    _strategy(
        "qlib_multihorizon",
        "Qlib Multi-Horizon Ensemble",
        "Blends one-week and one-month Qlib forecasts with a momentum/risk prior.",
        "Qlib",
        {"momentum_3_1": 0.3, "momentum_6_1": 0.2, "trend_50": 0.2, "volatility_60": 0.15, "reversal": 0.15},
        engine="qlib",
        horizon="1w",
        qlib_blend=0.75,
        frequency="weekly",
        tags=["qlib", "ensemble"],
    ),
    _strategy(
        "qlib_long_horizon",
        "Qlib Long-Horizon",
        "Uses the three-month Qlib forecast for lower-turnover monthly positioning.",
        "Qlib",
        {"momentum_12_1": 0.3, "momentum_6_1": 0.25, "trend_200": 0.2, "volatility_60": 0.15, "drawdown_252": 0.1},
        engine="qlib",
        horizon="3m",
        qlib_blend=0.8,
        frequency="monthly",
        tags=["qlib", "low-turnover"],
    ),
)


BUILTIN_BY_ID = {strategy.id: strategy for strategy in BUILTIN_STRATEGIES}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:48]
    return slug or f"strategy_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"


def list_strategies(store: Store) -> list[dict[str, Any]]:
    builtins = []
    for definition in BUILTIN_STRATEGIES:
        item = definition.model_dump()
        item["builtin"] = True
        item["created_at"] = None
        item["updated_at"] = None
        builtins.append(item)
    custom = []
    for item in store.list_custom_strategies():
        try:
            custom.append(StrategyDefinition(**item).model_dump())
        except Exception:  # noqa: BLE001 - stale custom rows should not break the library
            continue
    return builtins + custom


def get_strategy(store: Store, strategy_id: str) -> dict[str, Any] | None:
    if strategy_id in BUILTIN_BY_ID:
        item = BUILTIN_BY_ID[strategy_id].model_dump()
        item["builtin"] = True
        return item
    item = store.get_custom_strategy(strategy_id)
    if not item:
        return None
    try:
        return StrategyDefinition(**item).model_dump()
    except Exception:  # noqa: BLE001
        return None


def save_strategy(store: Store, definition: StrategyDefinition) -> dict[str, Any]:
    if definition.id in BUILTIN_BY_ID:
        raise ValueError("built-in strategies cannot be overwritten; duplicate it first")
    payload = definition.model_dump()
    payload["builtin"] = False
    now = now_iso()
    if not payload.get("created_at"):
        payload["created_at"] = now
    payload["updated_at"] = now
    store.save_custom_strategy(payload)
    return payload


def delete_strategy(store: Store, strategy_id: str) -> bool:
    if strategy_id in BUILTIN_BY_ID:
        return False
    return store.delete_custom_strategy(strategy_id)


def validate_strategy(store: Store, definition: StrategyDefinition) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    coverage: dict[str, Any] = {}
    factors = store.factor_coverage()
    available = {row["factor"]: row for row in factors.to_dict(orient="records")} if not factors.empty else {}
    missing = [name for name in definition.factor_weights if name not in available]
    if missing:
        warnings.append(f"factors not calculated yet: {', '.join(missing)}")
    if not available:
        errors.append("no factor data is available; run the pipeline first")
    else:
        for name in definition.factor_weights:
            if name in available:
                coverage[name] = {"rows": int(available[name]["rows"]), "symbols": int(available[name]["symbols"])}
        covered = [name for name in definition.factor_weights if name in available]
        if not covered:
            errors.append("none of the selected factors are available in the current dataset")
    total_weight = sum(abs(weight) for weight in definition.factor_weights.values())
    if total_weight == 0:
        errors.append("the factor weights sum to zero")
    portfolio = definition.portfolio
    if portfolio.mode == "long_short" and definition.filters.trend != "none":
        warnings.append("trend filters apply to both long and short legs; review the short side after the first run")
    if portfolio.quantile * portfolio.max_position_weight < 0.01:
        warnings.append("the position cap and quantile produce very small positions")
    if portfolio.max_position_weight * max(1, int(np.ceil(1 / max(portfolio.quantile, 0.01)))) < 0.25:
        warnings.append("the max position cap may leave a large cash residual")
    if definition.risk.target_volatility and definition.risk.target_volatility > 0.35:
        warnings.append("target volatility above 35% is aggressive for a long-only equity sleeve")
    if definition.risk.drawdown_stop and definition.risk.drawdown_stop < 0.05:
        warnings.append("a drawdown stop below 5% will likely trigger on normal noise")
    try:
        from .qlib_engine import engine_status

        status = engine_status(store)
        if definition.engine == "qlib" and not status.get("available"):
            warnings.append("Qlib is not installed; the strategy will fall back to the native factor engine")
        if definition.engine == "qlib" and status.get("available") and not status.get("predictions_ready"):
            warnings.append("Qlib is available but forecasts have not been built; the pipeline will build them")
    except Exception:  # noqa: BLE001 - engine status is advisory
        status = {}
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "coverage": coverage,
        "engine": status,
        "estimated_positions": max(1, int(np.ceil(1 / max(portfolio.quantile, 0.01)))),
    }


def latest_factor_date(store: Store) -> date | None:
    """Return the most recent factor date without scanning the factor table."""
    coverage = store.factor_coverage()
    if coverage.empty or "last_date" not in coverage:
        return None
    latest = pd.to_datetime(coverage["last_date"]).max()
    return latest.date() if pd.notna(latest) else None


def _composite_score(store: Store, definition: StrategyDefinition, since: date | None = None) -> pd.DataFrame:
    available = set(store.factor_coverage()["factor"])
    weights = {name: float(weight) for name, weight in definition.factor_weights.items() if name in available and float(weight) != 0}
    if not weights:
        return pd.DataFrame(columns=["date", "symbol", "score"])
    terms = " + ".join("SUM(CASE WHEN factor = ? THEN standardized * ? ELSE 0 END)" for _ in weights)
    params: list[Any] = []
    for name, weight in weights.items():
        params.extend([name, weight])
    params.append(sum(abs(weight) for weight in weights.values()))
    placeholders = ", ".join("?" for _ in weights)
    params.extend(weights.keys())
    sql = f"SELECT date, symbol, ({terms}) / ? AS score FROM factors WHERE factor IN ({placeholders})"
    if since is not None:
        sql += " AND date >= ?"
        params.append(since)
    sql += " GROUP BY date, symbol"
    return store.query(sql, params)


def _apply_overlays(store: Store, frame: pd.DataFrame, definition: StrategyDefinition, since: date | None = None) -> pd.DataFrame:
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    if frame.empty:
        return frame[["date", "symbol", "score"]]
    start = since or frame["date"].min()
    if hasattr(start, "date") and not isinstance(start, date):
        start = start.date()
    # 200-session SMA plus a safety margin measured in calendar days.
    lookback_start = start - timedelta(days=400)
    end = frame["date"].max()
    prices = store.query(
        "SELECT date, symbol, close FROM clean_prices WHERE date >= ? AND date <= ?",
        [lookback_start, end],
    )
    if prices.empty:
        return frame[["date", "symbol", "score"]]
    prices["date"] = pd.to_datetime(prices["date"]).dt.date
    close = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    price_lookup = close.stack().rename("price")
    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    returns = close.pct_change()
    volatility = returns.rolling(20, min_periods=20).std() * np.sqrt(252)
    # History counts must include sessions before the loaded window so the
    # min_history filter still behaves like a full-history run.
    history = close.notna().cumsum()
    base = store.query(
        "SELECT symbol, count(close) AS sessions FROM clean_prices WHERE date < ? GROUP BY symbol",
        [lookback_start],
    )
    if not base.empty:
        base_map = pd.Series(base["sessions"].to_numpy(), index=base["symbol"].to_numpy())
        history = history.add(base_map.reindex(close.columns).fillna(0), axis=1)
    keys = pd.MultiIndex.from_frame(frame[["date", "symbol"]])
    frame["price"] = price_lookup.reindex(keys).to_numpy()
    frame["history"] = history.stack().reindex(keys).to_numpy()
    frame["sma50"] = sma50.stack().reindex(keys).to_numpy()
    frame["sma200"] = sma200.stack().reindex(keys).to_numpy()
    frame["volatility"] = volatility.stack().reindex(keys).to_numpy()
    filters = definition.filters
    valid = frame["price"].ge(filters.min_price) & frame["history"].ge(filters.min_history)
    if filters.trend == "above_50":
        valid &= frame["price"].gt(frame["sma50"])
    elif filters.trend == "above_200":
        valid &= frame["price"].gt(frame["sma200"])
    elif filters.trend == "below_200":
        valid &= frame["price"].lt(frame["sma200"])
    if filters.volatility != "none":
        cross_vol = frame.groupby("date")["volatility"].transform("median")
        if filters.volatility == "low":
            valid &= frame["volatility"].le(cross_vol)
        else:
            valid &= frame["volatility"].ge(cross_vol)
    frame.loc[~valid, "score"] = np.nan
    if filters.min_dollar_volume > 0:
        raw = store.query(
            "SELECT date, symbol, raw_value FROM factors WHERE factor = 'dollar_volume' AND date >= ? AND date <= ?",
            [start, end],
        )
        if not raw.empty:
            raw["date"] = pd.to_datetime(raw["date"]).dt.date
            liquidity = raw.set_index(["date", "symbol"])["raw_value"].reindex(keys).to_numpy()
            frame.loc[~(liquidity >= filters.min_dollar_volume), "score"] = np.nan
    return frame[["date", "symbol", "score"]]


def generate_strategy_signal(
    store: Store,
    definition: StrategyDefinition | dict[str, Any],
    *,
    persist: bool = True,
    since: date | None = None,
) -> pd.DataFrame:
    definition = definition if isinstance(definition, StrategyDefinition) else StrategyDefinition(**definition)
    native = _composite_score(store, definition, since=since)
    if native.empty:
        return native
    native = _apply_overlays(store, native, definition, since=since)
    engine = definition.engine
    if engine in {"auto", "qlib"}:
        try:
            from .directions import forecast_scores

            horizon = HORIZONS[definition.horizon]
            qlib = forecast_scores(store, horizon, since=since)
            if not qlib.empty:
                qlib = qlib.rename(columns={"score": "model_score"})
                native = native.merge(qlib, on=["date", "symbol"], how="left")
                blend = definition.qlib_blend
                native["score"] = np.where(
                    native["model_score"].notna(),
                    blend * native["model_score"] + (1 - blend) * native["score"],
                    native["score"],
                )
        except Exception:  # noqa: BLE001 - native scores remain a safe fallback
            pass
    native["score"] = native["score"].replace([np.inf, -np.inf], np.nan)
    native = native.dropna(subset=["score"])
    native["score"] = native["score"].astype("float64")
    if persist:
        path = store.root / "signals"
        path.mkdir(parents=True, exist_ok=True)
        native.to_parquet(path / f"strategy_{definition.id}.parquet", index=False)
    return native


def strategy_backtest_config(definition: StrategyDefinition | dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    definition = definition if isinstance(definition, StrategyDefinition) else StrategyDefinition(**definition)
    portfolio = definition.portfolio
    costs = definition.costs
    config: dict[str, Any] = {
        "signal_name": f"strategy_{definition.id}",
        "strategy_id": definition.id,
        "strategy_name": definition.name,
        "mode": portfolio.mode,
        "rebalance_frequency": portfolio.rebalance_frequency,
        "quantile": portfolio.quantile,
        "max_position_weight": portfolio.max_position_weight,
        "gross_exposure": portfolio.gross_exposure,
        "long_fraction": portfolio.long_fraction,
        "commission_bps": costs.commission_bps,
        "slippage_bps": costs.slippage_bps,
        "borrow_bps": costs.borrow_bps,
        "target_volatility": definition.risk.target_volatility,
        "drawdown_stop": definition.risk.drawdown_stop,
        "drawdown_resume": definition.risk.drawdown_resume,
        "max_gross_exposure": definition.risk.max_gross_exposure,
        "engine": definition.engine,
        "horizon": definition.horizon,
        "factor_weights": definition.factor_weights,
        "start_date": None,
        "end_date": None,
        "split_date": None,
    }
    for key, value in (overrides or {}).items():
        if value is not None and key in config:
            config[key] = value
    return config
