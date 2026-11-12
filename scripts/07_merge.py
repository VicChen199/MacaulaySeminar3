"""Join neighborhood traffic means to asthma rates."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.merge import MergeError, _MERGED_COLUMNS, merge_uhf_period

_ASTHMA = Path("outputs/data/cleaned_asthma.csv")
_TRAFFIC = Path("outputs/data/traffic_uhf42_year.csv")
_COMPARISON = Path("outputs/dataset_source_comparison.csv")
_MERGED = Path("outputs/data/final_merged_dataset.csv")
_MISSING = Path("outputs/quality/missing_data_report.csv")
_EXCLUSIONS = Path("outputs/audit/exclusions_merge.csv")
_COMPARISON_COLUMNS = (
    "period",
    "asthma_available",
    "automated_available",
    "historical_available",
    "aadt_available",
    "raw_record_count",
    "distinct_segments",
    "n_uhf_with_traffic",
    "n_uhf_with_asthma",
    "n_matched",
    "n_usable",
    "recommended_source",
)


def _read_table(path):
    if not path.is_file():
        print(path.as_posix())
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _cell(column, value):
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, "f").rstrip("0").rstrip(".")
    return str(value)


def _write_frame(path, frame, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(columns)]
    for record in frame.to_dict(orient="records"):
        lines.append(",".join(_cell(column, record.get(column)) for column in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _usable_lines(comparison, merged):
    lines = []
    seen = set()
    counts = {}
    for record in merged.to_dict(orient="records"):
        period = str(record["period"])
        counts.setdefault(period, 0)
        if record["coverage_status"] == "valid":
            counts[period] += 1
    for period in comparison["period"].tolist():
        label = str(period).strip()
        seen.add(label)
        lines.append(f"{label} usable {int(counts.get(label, 0))}")
    for period, count in counts.items():
        if period not in seen:
            lines.append(f"{period} usable {int(count)}")
    return lines


def main():
    asthma = _read_table(_ASTHMA)
    if asthma is None:
        return 2
    traffic = _read_table(_TRAFFIC)
    if traffic is None:
        return 2
    comparison = _read_table(_COMPARISON)
    if comparison is None:
        return 2
    try:
        merged, updated, missing, exclusions = merge_uhf_period(
            asthma, traffic, comparison
        )
    except MergeError as exc:
        print(exc.message)
        return int(exc.exit_code)
    _write_frame(_MERGED, merged, _MERGED_COLUMNS)
    _write_frame(_COMPARISON, updated, _COMPARISON_COLUMNS)
    _write_frame(
        _MISSING,
        missing,
        ("period", "uhf42_id", "missing_rate", "missing_traffic", "exclusion_reason"),
    )
    _write_frame(_EXCLUSIONS, exclusions, ("stage", "key", "reason", "source"))
    for line in _usable_lines(updated, merged):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
