import os

from .runner import ingest

if __name__ == "__main__":
    ingest(
        os.getenv("UNIVERSE_PATH", "/config/universe.yaml"),
        os.getenv("DATA_ROOT", "/data"),
        sleep_seconds=float(os.getenv("INGEST_SLEEP_SECONDS", "0.05")),
        workers=int(os.getenv("INGEST_WORKERS", "4")),
        retries=int(os.getenv("INGEST_RETRIES", "2")),
    )
