from __future__ import annotations

import os
import tempfile
import time

import pytest

os.environ.setdefault("DATA_ROOT", tempfile.mkdtemp(prefix="finhound-api-import-"))
os.environ.pop("API_READ_ONLY", None)
os.environ.pop("API_KEY", None)

import core.api as api  # noqa: E402
from core.directions import build_directions  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def custom_payload(**overrides) -> dict:
    payload = {
        "id": "api_test_strategy",
        "name": "API Test Strategy",
        "description": "Created from an API contract test",
        "category": "Momentum",
        "tags": ["api", "test"],
        "engine": "native",
        "horizon": "1m",
        "qlib_blend": 0.7,
        "factor_weights": {"momentum_6_1": 0.6, "volatility_60": 0.4},
        "filters": {"min_price": 1, "min_dollar_volume": 1, "trend": "none", "volatility": "none", "min_history": 252},
        "portfolio": {
            "mode": "long_only",
            "rebalance_frequency": "weekly",
            "quantile": 0.2,
            "max_position_weight": 0.25,
            "gross_exposure": 1,
            "long_fraction": 0.5,
            "weighting": "equal",
        },
        "risk": {
            "target_volatility": None,
            "drawdown_stop": None,
            "drawdown_resume": None,
            "max_gross_exposure": 1,
        },
        "costs": {"commission_bps": 1, "slippage_bps": 2, "borrow_bps": 50},
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def client(store, monkeypatch) -> TestClient:
    monkeypatch.delenv("API_READ_ONLY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(api, "store", store)
    with api.job_lock:
        api.jobs.clear()
        api.cancel_requests.clear()
    return TestClient(api.app)


def test_health_metrics_and_engines(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["read_only"] is False
    assert body["data"]["symbols"] == 6
    assert "engine" in body
    assert "directions" in body
    assert health.headers["x-request-id"]

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["tables"]["clean_prices"] == 2040

    engines = client.get("/engines")
    assert engines.status_code == 200
    assert engines.json()["native"]["available"] is True
    assert "directions" in engines.json()


def test_backtest_risk_report(client: TestClient, store) -> None:
    import pandas as pd

    run_id = "risk-report-test"
    path = store.root / "results" / run_id
    path.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range("2024-01-02", periods=8)
    strategy = pd.Series([0.01, 0.02, -0.01, 0.01, 0.02, -0.01, 0.01, 0.02])
    spy = pd.Series([0.005, 0.01, -0.005, 0.005, 0.01, -0.005, 0.005, 0.01])
    pd.DataFrame(
        {
            "date": dates,
            "daily_return": strategy,
            "SPY": (1 + spy).cumprod(),
        }
    ).to_parquet(path / "equity.parquet", index=False)

    response = client.get(f"/backtests/{run_id}/risk?window=3")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["summary"]["benchmark_beta"] == pytest.approx(2.0)
    assert body["rolling"]


def test_factor_catalog_contract(client: TestClient) -> None:
    response = client.get("/factors/catalog")
    assert response.status_code == 200
    catalog = response.json()
    assert len(catalog) >= 20
    assert {"name", "label", "group", "description"} <= set(catalog[0])


def test_gamma_exposure_endpoint(client: TestClient) -> None:
    response = client.post(
        "/options/gamma-exposure",
        json={
            "symbol": "SPY",
            "spot": 100,
            "options": [
                {"strike": 95, "option_type": "call", "gamma": 0.02, "open_interest": 100},
                {"strike": 105, "option_type": "put", "gamma": 0.02, "open_interest": 100},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "SPY"
    assert body["net_gamma_exposure"] == pytest.approx(0)
    assert len(body["strikes"]) == 2


def test_gamma_heatmap_endpoint(client: TestClient) -> None:
    response = client.post(
        "/options/gamma-heatmap",
        json={
            "symbol": "SPY",
            "options": [
                {"expiration": "2024-01-19", "strike": 95, "option_type": "call", "gamma": 0.02, "open_interest": 100, "spot": 100},
                {"expiration": "2024-02-16", "strike": 105, "option_type": "put", "gamma": 0.02, "open_interest": 100, "spot": 100},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["expirations"] == ["2024-01-19", "2024-02-16"]
    assert len(body["cells"]) == 2


def test_signal_snapshot_contract(client: TestClient, store) -> None:
    import pandas as pd

    directory = store.root / "signals"
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02"] * 2 + ["2024-01-03"] * 3),
            "symbol": ["A", "B", "A", "B", "C"],
            "score": [1.0, 2.0, 0.1, 0.2, 0.3],
        }
    ).to_parquet(directory / "smoke.parquet", index=False)

    response = client.get("/signals/smoke/snapshot?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2024-01-03"
    assert body["count"] == 3
    assert [item["symbol"] for item in body["long"]] == ["C", "B"]
    assert [item["symbol"] for item in body["short"]] == ["A", "B"]
    assert body["mean"] == pytest.approx(0.2)
    assert body["positive"] == 3
    assert body["first_date"] == "2024-01-02"
    assert [row["count"] for row in body["history"]] == [2, 3]

    earlier = client.get("/signals/smoke/snapshot?limit=2&date=2024-01-02").json()
    assert earlier["date"] == "2024-01-02"
    assert earlier["count"] == 2
    assert [item["symbol"] for item in earlier["long"]] == ["B", "A"]


def test_strategy_library_and_validation(client: TestClient) -> None:
    library = client.get("/strategies")
    assert library.status_code == 200
    items = library.json()
    assert len(items) >= 20
    assert any(item["builtin"] for item in items)

    valid = client.post("/strategies/validate", json=custom_payload())
    assert valid.status_code == 200
    assert valid.json()["valid"] is True
    assert valid.json()["coverage"]

    invalid = client.post("/strategies/validate", json=custom_payload(factor_weights={"unknown_factor": 1.0}))
    assert invalid.status_code == 422
    assert "unknown factors" in invalid.text


def test_strategy_crud_lifecycle(client: TestClient) -> None:
    created = client.post("/strategies", json=custom_payload())
    assert created.status_code == 201
    payload = created.json()
    assert payload["id"] == "api_test_strategy"
    assert payload["builtin"] is False
    assert payload["created_at"]

    duplicate = client.post("/strategies/api_test_strategy/duplicate", json={})
    assert duplicate.status_code == 201
    duplicate_payload = duplicate.json()
    assert duplicate_payload["id"] != payload["id"]
    assert duplicate_payload["name"] == "API Test Strategy Copy"

    detail = client.get("/strategies/api_test_strategy")
    assert detail.status_code == 200
    assert detail.json()["validation"]["valid"] is True

    update = custom_payload(name="API Test Strategy Updated", factor_weights={"momentum_3_1": 1.0})
    updated = client.put("/strategies/api_test_strategy", json=update)
    assert updated.status_code == 200
    assert updated.json()["name"] == "API Test Strategy Updated"

    mismatch = client.put("/strategies/api_test_strategy", json=custom_payload(id="other_id"))
    assert mismatch.status_code == 422

    builtin_put = custom_payload(id="momentum_12_1_classic")
    assert client.put("/strategies/momentum_12_1_classic", json=builtin_put).status_code == 409
    assert client.delete("/strategies/momentum_12_1_classic").status_code == 409

    assert client.delete("/strategies/api_test_strategy").status_code == 200
    assert client.get("/strategies/api_test_strategy").status_code == 404
    assert client.delete("/strategies/api_test_strategy").status_code == 409
    assert client.delete("/strategies/api_test_strategy_copy").status_code == 200


def test_strategy_preview_endpoints(client: TestClient) -> None:
    created = client.post("/strategies", json=custom_payload())
    assert created.status_code == 201

    draft = client.post("/strategies/preview?limit=3", json=custom_payload(id="draft_api"))
    assert draft.status_code == 200
    preview = draft.json()
    assert preview["available"] is True
    assert preview["count"] > 0
    assert len(preview["long"]) <= 3
    assert preview["validation"]["valid"] is True
    assert preview["config"]["strategy_id"] == "draft_api"

    stored = client.get("/strategies/api_test_strategy/preview?limit=2")
    assert stored.status_code == 200
    assert stored.json()["strategy_id"] == "api_test_strategy"
    assert len(stored.json()["items"]) == stored.json()["count"]

    assert client.get("/strategies/missing_strategy/preview").status_code == 404


def test_strategy_run_job_flow(client: TestClient) -> None:
    created = client.post("/strategies", json=custom_payload())
    assert created.status_code == 201

    queued = client.post("/strategies/api_test_strategy/run", json={"force": False})
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]

    deadline = time.time() + 30
    status = "queued"
    while time.time() < deadline:
        response = client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        status = response.json()["status"]
        if status in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)

    assert status == "completed", client.get(f"/jobs/{job_id}").json()
    finished = client.get(f"/jobs/{job_id}").json()
    assert finished["run_id"]
    assert "Sharpe" in finished["metrics"]

    listed = client.get("/jobs?limit=10")
    assert listed.status_code == 200
    assert any(item["job_id"] == job_id for item in listed.json())

    cancel_finished = client.post(f"/jobs/{job_id}/cancel")
    assert cancel_finished.status_code == 200
    assert cancel_finished.json()["cancelled"] is False

    runs = client.get("/strategies/api_test_strategy/runs")
    assert runs.status_code == 200
    assert any(item["run_id"] == finished["run_id"] for item in runs.json())


def test_direction_endpoints(client: TestClient) -> None:
    build_directions(api.store, engine="native")

    meta = client.get("/signals/directions/meta")
    assert meta.status_code == 200
    assert meta.json()["available"] is True
    assert meta.json()["engine"] == "native"

    snapshot = client.get("/signals/directions?horizon=1m&limit=2")
    assert snapshot.status_code == 200
    assert snapshot.json()["horizon_key"] == "1m"
    assert len(snapshot.json()["up"]) <= 2

    history = client.get("/signals/directions/AAA?horizon=1w&limit=10")
    assert history.status_code == 200
    assert history.json()["symbol"] == "AAA"
    assert {item["horizon"] for item in history.json()["items"]} == {5}

    assert client.get("/signals/directions?horizon=1y").status_code == 422
    assert client.get("/signals/directions/AAA?horizon=1y").status_code == 422


def test_unknown_job_and_read_only_guard(store, monkeypatch) -> None:
    monkeypatch.setenv("API_READ_ONLY", "1")
    monkeypatch.setattr(api, "store", store)
    with TestClient(api.app) as readonly:
        assert readonly.get("/health").json()["read_only"] is True
        assert readonly.post("/strategies", json=custom_payload()).status_code == 409
        assert readonly.post("/strategies/api_test_strategy/run", json={}).status_code == 409
        assert readonly.post("/directions", json={}).status_code == 409
        assert readonly.get("/jobs/does-not-exist").status_code == 404
