from __future__ import annotations

import os
import sys

import yaml

from .backtest import run_backtest
from .factors import compute_factors
from .qc import run_qc
from .signals import generate_signal
from .store import Store


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "qc"
    store = Store()
    if command == "qc":
        run_qc(store)
    elif command == "factors":
        compute_factors(store.query("SELECT * FROM clean_prices"), store)
    elif command == "signal":
        generate_signal(store, os.getenv("SETTINGS_PATH", "/config/settings.yaml"))
    elif command == "backtest":
        signal = generate_signal(store, os.getenv("SETTINGS_PATH", "/config/settings.yaml"))
        settings = yaml.safe_load(open(os.getenv("SETTINGS_PATH", "/config/settings.yaml")))
        run_backtest(store, signal, settings.get("backtest", {}))
    else:
        raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
