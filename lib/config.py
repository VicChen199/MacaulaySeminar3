"""Read the pipeline configuration table."""

import tomllib
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pipeline.toml"


def load_config():
    with _CONFIG_PATH.open("rb") as handle:
        return tomllib.load(handle)
