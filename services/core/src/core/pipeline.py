from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .backtest import run_backtest
from .directions import build_directions
from .factors import compute_factors
from .qc import run_qc
from .signals import generate_baseline_momentum, generate_signal
from .store import Store


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "qc"
    store = Store()
    if command == "all":
        started = datetime.now(UTC)
        settings_path = os.getenv("SETTINGS_PATH", "/config/settings.yaml")
        settings = yaml.safe_load(Path(settings_path).read_text())
        signal_name = (settings.get("signal") or {}).get("default", "momentum_reversal_liquidity")
        run_qc(store)
        compute_factors(store.query("SELECT * FROM clean_prices"), store)
        signal_frame = generate_signal(store, settings_path)
        generate_baseline_momentum(store)
        directions_error = None
        try:
            build_directions(store, engine=str((settings.get("directions") or {}).get("engine", "auto")))
        except Exception as exc:  # noqa: BLE001 - Qlib is optional; keep factor analytics alive
            directions_error = str(exc)
        backtest_config = dict(settings.get("backtest") or {})
        backtest_config["signal_name"] = signal_name
        run_id, result = run_backtest(store, signal_frame, backtest_config)
        summary = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "status": "ok",
            "trigger": "cli",
            "run_id": run_id,
            "signal_name": signal_name,
            "metrics": result.get("full", {}),
            "directions_error": directions_error,
        }
        Path(store.root / "pipeline_runs").mkdir(exist_ok=True)
        (store.root / "pipeline_runs" / f"{started.strftime('%Y%m%dT%H%M%S%fZ')}.json").write_text(json.dumps(summary, indent=2))
        store.optimize()
        return
    if command == "qc":
        run_qc(store)
    elif command == "factors":
        compute_factors(store.query("SELECT * FROM clean_prices"), store)
    elif command == "signal":
        generate_signal(store, os.getenv("SETTINGS_PATH", "/config/settings.yaml"))
        generate_baseline_momentum(store)
        settings = yaml.safe_load(Path(os.getenv("SETTINGS_PATH", "/config/settings.yaml")).read_text()) or {}
        build_directions(store, engine=str((settings.get("directions") or {}).get("engine", "auto")))
    elif command == "directions":
        settings = yaml.safe_load(Path(os.getenv("SETTINGS_PATH", "/config/settings.yaml")).read_text()) or {}
        build_directions(store, engine=str((settings.get("directions") or {}).get("engine", "auto")))
    elif command == "optimize":
        store.optimize()
    elif command == "backtest":
        settings_path = os.getenv("SETTINGS_PATH", "/config/settings.yaml")
        settings = yaml.safe_load(Path(settings_path).read_text())
        signal = generate_signal(store, settings_path)
        backtest_config = dict(settings.get("backtest") or {})
        backtest_config["signal_name"] = (settings.get("signal") or {}).get("default", "momentum_reversal_liquidity")
        run_backtest(store, signal, backtest_config)
    else:
        raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
