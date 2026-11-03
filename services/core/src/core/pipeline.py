from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .backtest import run_backtest
from .factors import compute_factors
from .qc import run_qc
from .signals import generate_baseline_momentum, generate_signal
from .store import Store


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "qc"
    store = Store()
    if command == "all":
        started = datetime.now(UTC)
        run_qc(store)
        compute_factors(store.query("SELECT * FROM clean_prices"), store)
        generate_signal(store, os.getenv("SETTINGS_PATH", "/config/settings.yaml"))
        generate_baseline_momentum(store)
        summary = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "status": "ok",
        }
        Path(store.root / "pipeline_runs").mkdir(exist_ok=True)
        (store.root / "pipeline_runs" / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json").write_text(yaml.safe_dump(summary))
        return
    if command == "qc":
        run_qc(store)
    elif command == "factors":
        compute_factors(store.query("SELECT * FROM clean_prices"), store)
    elif command == "signal":
        generate_signal(store, os.getenv("SETTINGS_PATH", "/config/settings.yaml"))
        generate_baseline_momentum(store)
    elif command == "backtest":
        signal = generate_signal(store, os.getenv("SETTINGS_PATH", "/config/settings.yaml"))
        settings = yaml.safe_load(open(os.getenv("SETTINGS_PATH", "/config/settings.yaml")))
        run_backtest(store, signal, settings.get("backtest", {}))
    else:
        raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
