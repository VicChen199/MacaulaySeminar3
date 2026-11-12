"""Test the correlation between neighborhood traffic and asthma rates."""

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.stats import (
    COMPARISON_COLUMNS,
    PEARSON_COLUMNS,
    SENSITIVITY_COLUMNS,
    SPEARMAN_COLUMNS,
    StatsError,
    compare_periods,
    pearson_by_period,
    sensitivity,
    spearman_by_period,
)

_MERGED = Path("outputs/data/final_merged_dataset.csv")
_LOCATIONS = Path("outputs/data/traffic_location_year.parquet")
_PEARSON = Path("outputs/stats/pearson_results_by_year.csv")
_SPEARMAN = Path("outputs/stats/spearman_results_by_year.csv")
_SENSITIVITY = Path("outputs/stats/sensitivity_analysis_results.csv")
_COMPARISON = Path("outputs/stats/correlation_comparison_results.csv")
_YEAR = re.compile(r"\d{4}")


def _selection_from(merged):
    periods = []
    seen = []
    for value in merged["period"].tolist():
        label = str(value).strip()
        if label and label not in seen:
            seen.append(label)
    for label in seen:
        periods.append(
            {
                "period": label,
                "source": "automated",
                "years": [int(token) for token in _YEAR.findall(label)],
                "included": True,
                "reason": "",
            }
        )
    return {"periods": periods}


def _write(path, frame, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, columns=list(columns), index=False)


def _number(value):
    return format(float(value), ".16g")


def _blank(value):
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _print_result(kind, record):
    period = record["period"]
    if record["test_status"] != "ok" or _blank(record.get("r")):
        print(f"{kind} {period} skipped {record['skip_reason']}")
        return
    print(
        f"{kind} {period} r={_number(record['r'])} "
        f"ci={_number(record['ci_low'])},{_number(record['ci_high'])} "
        f"p={_number(record['p_raw'])}"
    )


def main():
    if not _MERGED.is_file():
        print(_MERGED.as_posix())
        return 2
    if not _LOCATIONS.is_file():
        print(_LOCATIONS.as_posix())
        return 2
    merged = pd.read_csv(_MERGED, dtype=str, keep_default_na=False)
    locations = pd.read_parquet(_LOCATIONS)
    selection = _selection_from(merged)
    try:
        pearson = pearson_by_period(merged, selection)
        spearman = spearman_by_period(merged, selection)
        sensitivity_table = sensitivity(merged, locations, selection)
        comparison = compare_periods(merged, pearson, selection)
    except StatsError as exc:
        print(exc.message)
        return int(exc.exit_code)
    _write(_PEARSON, pearson, PEARSON_COLUMNS)
    _write(_SPEARMAN, spearman, SPEARMAN_COLUMNS)
    _write(_SENSITIVITY, sensitivity_table, SENSITIVITY_COLUMNS)
    _write(_COMPARISON, comparison, COMPARISON_COLUMNS)
    records = pearson.to_dict(orient="records")
    if records and all(record["test_status"] == "skipped" for record in records):
        for record in records:
            print(f"secondary {record['period']} skipped {record['skip_reason']}")
        return 2
    for record in records:
        kind = "primary" if record["role"] == "primary" else "secondary"
        _print_result(kind, record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
