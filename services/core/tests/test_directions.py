from __future__ import annotations

from importlib.util import find_spec

import pytest
from core.directions import (
    HORIZONS,
    build_directions,
    directions_meta,
    load_directions,
    snapshot,
    symbol_history,
)


def test_auto_engine_falls_back_when_qlib_is_unavailable(store) -> None:
    if find_spec("qlib") is not None:
        pytest.skip("Qlib is installed; the auto engine may train a model")
    frame = build_directions(store, engine="auto")
    meta = directions_meta(store)

    assert set(frame["engine"].unique()) == {"native"}
    assert meta["engine"] == "native"
    assert set(meta["native_horizons"]) == set(HORIZONS.values())


def test_build_directions_persists_all_horizons(store) -> None:
    frame = build_directions(store, engine="native")

    assert not frame.empty
    assert set(frame["horizon"].unique()) == set(HORIZONS.values())
    assert set(frame["engine"].unique()) == {"native"}
    assert set(frame["direction"].unique()).issubset({"up", "down", "neutral"})
    assert frame["prob_up"].between(0, 1).all()
    assert frame["confidence"].between(0, 1).all()
    assert frame["date"].notna().all()

    assert (store.root / "directions" / "directions.parquet").exists()
    assert (store.root / "directions" / "meta.json").exists()


def test_build_directions_uses_cache_until_forced(store) -> None:
    first = build_directions(store, engine="native")
    cached = build_directions(store, engine="native")
    forced = build_directions(store, engine="native", force=True)

    assert len(first) == len(cached)
    assert first.reset_index(drop=True).equals(cached.reset_index(drop=True))
    assert len(forced) == len(first)


def test_meta_reports_artifact_coverage(store) -> None:
    build_directions(store, engine="native")
    meta = directions_meta(store)

    assert meta["available"] is True
    assert meta["engine"] == "native"
    assert meta["rows"] > 0
    assert meta["symbols"] == 6
    assert meta["horizons"]
    assert set(meta["engines"]) == {"native"}
    assert meta["first_date"] <= meta["last_date"]


def test_snapshot_ranks_scores_for_a_horizon(store) -> None:
    build_directions(store, engine="native")
    result = snapshot(store, horizon=21, limit=3)

    assert result["available"] is True
    assert result["horizon"] == 21
    assert result["horizon_key"] == "1m"
    assert result["count"] > 0
    assert result["count"] == result["bullish"] + result["bearish"] + result["neutral"]
    assert len(result["up"]) <= 3
    assert len(result["down"]) <= 3
    ranks = [item["rank"] for item in result["items"]]
    assert ranks == sorted(ranks)
    scores = [item["score_z"] for item in result["items"]]
    assert scores == sorted(scores, reverse=True)


def test_snapshot_filters_confidence_and_dates(store) -> None:
    frame = build_directions(store, engine="native")
    latest = str(frame["date"].max())

    high = snapshot(store, horizon=21, min_confidence=0.99)
    historical = snapshot(store, horizon=21, on=latest)

    assert high["count"] <= snapshot(store, horizon=21)["count"]
    assert historical["available"] is True
    assert historical["date"] == latest


def test_snapshot_without_artifacts_is_safe(store) -> None:
    result = snapshot(store, horizon=21)

    assert result["available"] is False
    assert result["items"] == []
    assert result["count"] == 0


def test_symbol_history_is_ordered_and_filterable(store) -> None:
    build_directions(store, engine="native")

    everything = symbol_history(store, "aaa", limit=500)
    one_month = symbol_history(store, "AAA", horizon=21, limit=500)

    assert everything["symbol"] == "AAA"
    assert everything["items"]
    dates = [item["date"] for item in everything["items"]]
    assert dates == sorted(dates)
    assert one_month["items"]
    assert {item["horizon"] for item in one_month["items"]} == {21}


def test_symbol_history_without_artifacts_is_safe(store) -> None:
    assert symbol_history(store, "AAA") == {"symbol": "AAA", "items": []}


def test_load_directions_round_trips_dates(store) -> None:
    build_directions(store, engine="native")
    frame = load_directions(store)

    assert not frame.empty
    assert str(frame["date"].dtype) == "object"
