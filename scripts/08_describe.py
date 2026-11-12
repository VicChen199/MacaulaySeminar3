"""Describe the merged sample and write figures S1 through S7."""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.describe import BOROUGH_COLUMNS, YEAR_COLUMNS, descriptive_tables, primary_period
from lib.plots import sample_figures

_MERGED = Path("outputs/data/final_merged_dataset.csv")
_TRAFFIC = Path("outputs/data/traffic_uhf42_year.csv")
_LOCATIONS = Path("outputs/data/traffic_monitoring_locations.geojson")
_BASEMAP = Path("data/raw/geography/UHF42.geo.json")
_SELECTION = Path("outputs/audit/source_selection.json")
_YEAR = Path("outputs/stats/descriptive_statistics_by_year.csv")
_BOROUGH = Path("outputs/stats/descriptive_statistics_by_borough.csv")
_FIGURES = Path("outputs/figures/sample")


def _read_table(path):
    if not path.is_file():
        print(path.as_posix())
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _read_json(path):
    if not path.is_file():
        print(path.as_posix())
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_frame(path, frame, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, columns=list(columns), index=False)


def main():
    merged = _read_table(_MERGED)
    if merged is None:
        return 2
    traffic = _read_table(_TRAFFIC)
    if traffic is None:
        return 2
    if not _LOCATIONS.is_file():
        print(_LOCATIONS.as_posix())
        return 2
    if not _BASEMAP.is_file():
        print(_BASEMAP.as_posix())
        return 2
    selection = _read_json(_SELECTION)
    if selection is None:
        return 2
    if traffic.shape[1] == 0:
        print(_TRAFFIC.as_posix())
        return 2
    chosen = primary_period(merged, selection)
    by_year, by_borough = descriptive_tables(merged)
    _write_frame(_YEAR, by_year, YEAR_COLUMNS)
    _write_frame(_BOROUGH, by_borough, BOROUGH_COLUMNS)
    paths = sample_figures(merged, _LOCATIONS, _BASEMAP, _FIGURES, chosen, selection)
    for path in paths:
        print(Path(path).as_posix())
    print(f"primary period {chosen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
