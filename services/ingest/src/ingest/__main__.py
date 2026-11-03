import os

from .runner import ingest

if __name__ == "__main__":
    ingest(os.getenv("UNIVERSE_PATH", "/config/universe.yaml"), os.getenv("DATA_ROOT", "/data"))
