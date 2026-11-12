"""Write the workbook, result figures, and both narratives."""

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.config import load_config
from lib.plots import result_figures
from lib.report import compile_exclusions, narratives, workbook

_MERGED = Path("outputs/data/final_merged_dataset.csv")
_PEARSON = Path("outputs/stats/pearson_results_by_year.csv")
_SPEARMAN = Path("outputs/stats/spearman_results_by_year.csv")
_SENSITIVITY = Path("outputs/stats/sensitivity_analysis_results.csv")
_COMPARISON = Path("outputs/stats/correlation_comparison_results.csv")
_DESCRIPTIVE = Path("outputs/stats/descriptive_statistics_by_year.csv")
_BOROUGH = Path("outputs/stats/descriptive_statistics_by_borough.csv")
_SOURCE_COMPARISON = Path("outputs/dataset_source_comparison.csv")
_SELECTION = Path("outputs/audit/source_selection.json")
_EXCLUSIONS = Path("outputs/quality/excluded_observations.csv")
_WORKBOOK = Path("outputs/research_results.xlsx")
_NOTES = Path("outputs")
_FIGURES = Path("outputs/figures/results")


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


def _exclusion_frames():
    folder = Path("outputs/audit")
    frames = []
    if not folder.is_dir():
        return frames
    for path in sorted(folder.glob("exclusions_*.csv")):
        frames.append(pd.read_csv(path, dtype=str, keep_default_na=False))
    return frames


def main():
    load_config()
    tables = {}
    for key, path in (
        ("merged", _MERGED),
        ("pearson", _PEARSON),
        ("spearman", _SPEARMAN),
        ("sensitivity", _SENSITIVITY),
        ("comparison", _COMPARISON),
        ("descriptive", _DESCRIPTIVE),
        ("borough", _BOROUGH),
        ("source_comparison", _SOURCE_COMPARISON),
    ):
        frame = _read_table(path)
        if frame is None:
            return 2
        tables[key] = frame
    selection = _read_json(_SELECTION)
    if selection is None:
        return 2
    tables["selection"] = selection
    tables["exclusions"] = _exclusion_frames()
    compile_exclusions(tables["exclusions"], _EXCLUSIONS)
    workbook(tables, _WORKBOOK)
    narratives(tables, _NOTES)
    paths = result_figures(
        tables["merged"],
        tables["pearson"],
        tables["spearman"],
        tables["selection"],
        _FIGURES,
    )
    ok = tables["pearson"]["test_status"].map(lambda value: str(value).strip().lower() == "ok")
    if not bool(ok.any()):
        return 2
    print(_WORKBOOK.as_posix())
    for path in paths:
        print(Path(path).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
