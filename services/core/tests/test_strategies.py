from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from core.strategies import (
    BUILTIN_STRATEGIES,
    StrategyDefinition,
    delete_strategy,
    generate_strategy_signal,
    get_strategy,
    latest_factor_date,
    list_strategies,
    save_strategy,
    strategy_backtest_config,
    validate_strategy,
)
from pydantic import ValidationError


def custom_definition(**overrides) -> StrategyDefinition:
    payload = {
        "id": "test_momentum",
        "name": "Test Momentum",
        "description": "A test strategy",
        "category": "Momentum",
        "tags": [" Test ", "test", "core"],
        "engine": "native",
        "horizon": "1m",
        "factor_weights": {"momentum_6_1": 0.6, "volatility_60": 0.4},
    }
    payload.update(overrides)
    return StrategyDefinition(**payload)


def test_builtins_are_plural_unique_and_valid() -> None:
    assert len(BUILTIN_STRATEGIES) >= 20
    ids = [strategy.id for strategy in BUILTIN_STRATEGIES]
    assert len(ids) == len(set(ids))
    assert all(strategy.builtin for strategy in BUILTIN_STRATEGIES)
    assert {strategy.horizon for strategy in BUILTIN_STRATEGIES} == {"1d", "1w", "1m", "3m"} or "1m" in {
        strategy.horizon for strategy in BUILTIN_STRATEGIES
    }
    assert any(strategy.engine == "qlib" for strategy in BUILTIN_STRATEGIES)


def test_tags_are_normalized_and_deduplicated() -> None:
    definition = custom_definition()
    assert definition.tags == ["test", "core"]


def test_unknown_factor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        custom_definition(factor_weights={"not_a_factor": 1.0})


def test_zero_and_non_finite_weights_are_rejected() -> None:
    with pytest.raises(ValidationError):
        custom_definition(factor_weights={"momentum_6_1": 0.0})
    with pytest.raises(ValidationError):
        custom_definition(factor_weights={"momentum_6_1": float("inf")})


def test_invalid_ids_and_drawdown_ordering_are_rejected() -> None:
    with pytest.raises(ValidationError):
        custom_definition(id="Bad ID")
    with pytest.raises(ValidationError):
        StrategyDefinition(
            id="bad_drawdown",
            name="Bad Drawdown",
            factor_weights={"momentum_6_1": 1.0},
            risk={"drawdown_stop": 0.1, "drawdown_resume": 0.2},
        )


def test_custom_strategy_crud(store) -> None:
    definition = custom_definition()
    saved = save_strategy(store, definition)

    assert saved["builtin"] is False
    assert saved["created_at"]

    loaded = get_strategy(store, definition.id)
    assert loaded is not None
    assert loaded["name"] == definition.name
    assert get_strategy(store, "does_not_exist") is None

    library = list_strategies(store)
    assert definition.id in {item["id"] for item in library}
    assert sum(item["builtin"] for item in library) == len(BUILTIN_STRATEGIES)

    assert delete_strategy(store, definition.id) is True
    assert get_strategy(store, definition.id) is None
    assert delete_strategy(store, definition.id) is False


def test_builtin_strategies_are_immutable(store) -> None:
    builtin = BUILTIN_STRATEGIES[0]
    with pytest.raises(ValueError):
        save_strategy(store, builtin)
    assert delete_strategy(store, builtin.id) is False


def test_validation_reports_coverage_and_engine(store) -> None:
    report = validate_strategy(store, custom_definition())

    assert report["valid"] is True
    assert report["errors"] == []
    assert report["estimated_positions"] >= 1
    assert set(report["coverage"]) == {"momentum_6_1", "volatility_60"}
    assert all(item["rows"] > 0 for item in report["coverage"].values())
    assert "available" in report["engine"]


def test_validation_flags_missing_dataset(tmp_path) -> None:
    from core.store import Store

    empty = Store(tmp_path / "empty")
    report = validate_strategy(empty, custom_definition())

    assert report["valid"] is False
    assert any("pipeline" in error for error in report["errors"])


def test_strategy_signal_and_backtest_config(store) -> None:
    definition = custom_definition()
    frame = generate_strategy_signal(store, definition, persist=False)

    assert not frame.empty
    assert set(frame.columns) == {"date", "symbol", "score"}
    assert frame["score"].notna().all()

    config = strategy_backtest_config(definition, {"start_date": "2023-01-01", "end_date": None})
    assert config["strategy_id"] == definition.id
    assert config["signal_name"] == f"strategy_{definition.id}"
    assert config["mode"] == "long_only"
    assert config["start_date"] == "2023-01-01"
    assert config["end_date"] is None
    assert config["commission_bps"] == 1.0


def test_strategy_signal_can_be_persisted(store) -> None:
    definition = custom_definition()
    generate_strategy_signal(store, definition, persist=True)

    assert (store.root / "signals" / f"strategy_{definition.id}.parquet").exists()


def test_preview_window_matches_full_history_on_latest_date(store) -> None:
    definition = custom_definition(filters={"trend": "above_50", "min_dollar_volume": 100_000})
    full = generate_strategy_signal(store, definition, persist=False)
    latest = latest_factor_date(store)
    assert latest is not None
    window = generate_strategy_signal(store, definition, persist=False, since=latest - timedelta(days=7))

    full["date"] = pd.to_datetime(full["date"]).dt.date
    window["date"] = pd.to_datetime(window["date"]).dt.date
    full_latest = full[full["date"] == latest].set_index("symbol")["score"]
    window_latest = window[window["date"] == latest].set_index("symbol")["score"]

    assert not full_latest.empty
    assert set(full_latest.index) == set(window_latest.index)
    assert full_latest.sort_index().equals(window_latest.sort_index())
