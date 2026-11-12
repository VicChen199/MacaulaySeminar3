"""Turn counts into neighborhood exposure."""

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.aggregate import (
    AggregateError,
    complete_hours,
    location_years,
    neighborhood_periods,
    segment_years,
)

_TRAFFIC = Path("outputs/data/traffic_with_uhf42.parquet")
_SELECTION = Path("outputs/audit/source_selection.json")
_LOCATIONS = Path("outputs/data/traffic_location_year.parquet")
_SEGMENTS = Path("outputs/data/traffic_segment_year.parquet")
_NEIGHBORHOODS = Path("outputs/data/traffic_uhf42_year.csv")
_COVERAGE = Path("outputs/quality/traffic_coverage_by_uhf42.csv")
_EXCLUSIONS = Path("outputs/audit/exclusions_aggregate.csv")
_LOG = Path("outputs/cleaning_log.md")
_COVERAGE_COLUMNS = (
    "period",
    "uhf42_id",
    "n_eligible_segments",
    "n_locations",
    "n_complete_hours",
    "adequate",
    "source",
)
_EXCLUSION_COLUMNS = ("stage", "key", "reason", "source")


def _year_key(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value).strip()
    return number


def _year_frames(frame):
    if frame is None or len(frame.index) == 0 or "year" not in frame.columns:
        return [frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()]
    order = []
    groups = defaultdict(list)
    for index, value in enumerate(frame["year"].tolist()):
        key = _year_key(value)
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(index)
    return [frame.iloc[groups[key]].reset_index(drop=True) for key in order]


def _complete_by_year(frame):
    parts = []
    for year_frame in _year_frames(frame):
        parts.append(complete_hours(year_frame))
    if not parts:
        return complete_hours(pd.DataFrame())
    return pd.concat(parts, ignore_index=True)


def _included_periods(periods):
    chosen = []
    for item in periods:
        if not isinstance(item, dict):
            continue
        flag = item.get("included")
        included = flag is True or (
            isinstance(flag, str) and flag.strip().casefold() == "true"
        )
        if not included:
            continue
        label = item.get("period")
        chosen.append("" if label is None else str(label).strip())
    return chosen


def _median_text(values):
    if not values:
        return "none"
    ordered = sorted(float(value) for value in values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        number = ordered[middle]
    else:
        number = (ordered[middle - 1] + ordered[middle]) / 2.0
    if number.is_integer():
        return str(int(number))
    return format(number, "f").rstrip("0").rstrip(".")


def _print_periods(neighborhoods, periods):
    labels = neighborhoods["period"].tolist() if len(neighborhoods.index) else []
    status = neighborhoods["coverage_status"].tolist() if len(neighborhoods.index) else []
    segments = neighborhoods["n_road_segments"].tolist() if len(neighborhoods.index) else []
    for label in _included_periods(periods):
        counts = [
            int(segment)
            for period, state, segment in zip(labels, status, segments)
            if period == label and state == "valid"
        ]
        print(f"{label} eligible {len(counts)} median {_median_text(counts)}")


def _coverage_frame(neighborhoods):
    if len(neighborhoods.index) == 0:
        return pd.DataFrame(columns=list(_COVERAGE_COLUMNS))
    return pd.DataFrame(
        {
            "period": neighborhoods["period"].tolist(),
            "uhf42_id": neighborhoods["uhf42_id"].tolist(),
            "n_eligible_segments": [
                int(value) for value in neighborhoods["n_road_segments"].tolist()
            ],
            "n_locations": [
                int(value) for value in neighborhoods["n_monitoring_locations"].tolist()
            ],
            "n_complete_hours": [
                int(value) for value in neighborhoods["n_complete_hours"].tolist()
            ],
            "adequate": [
                state == "valid" for state in neighborhoods["coverage_status"].tolist()
            ],
            "source": neighborhoods["traffic_source"].tolist(),
        },
        columns=list(_COVERAGE_COLUMNS),
    )


def _exclusion_frame(locations):
    rows = []
    if len(locations.index):
        for record in locations.to_dict(orient="records"):
            if record.get("eligible") is True:
                continue
            source = record.get("source")
            rows.append(
                {
                    "stage": "aggregate",
                    "key": "" if record.get("monitoring_location_id") is None else str(record["monitoring_location_id"]),
                    "reason": "below_threshold",
                    "source": "" if source is None else str(source),
                }
            )
    return pd.DataFrame(rows, columns=list(_EXCLUSION_COLUMNS))


def _parse_date(value):
    text = "" if value is None else str(value).strip()
    if len(text) < 10 or text.casefold() in {"nan", "none", "<na>"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _clock(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if 0 <= number <= 23:
        return number
    return None


def _sampling_lines(hours):
    hour_counts = [0] * 24
    month_counts = [0] * 12
    weekday = 0
    weekend = 0
    if len(hours.index):
        for local_date, hour in zip(hours["date"].tolist(), hours["hour"].tolist()):
            clock = _clock(hour)
            if clock is not None:
                hour_counts[clock] += 1
            parsed = _parse_date(local_date)
            if parsed is None:
                continue
            if parsed.weekday() <= 4:
                weekday += 1
            else:
                weekend += 1
            month_counts[parsed.month - 1] += 1
    lines = ["aggregate sampling"]
    for hour, count in enumerate(hour_counts):
        lines.append(f"hour {hour} {count}")
    lines.append(f"weekday {weekday}")
    lines.append(f"weekend {weekend}")
    for month, count in enumerate(month_counts, start=1):
        lines.append(f"month {month} {count}")
    return lines


def _append_log(hours):
    block = "\n".join(_sampling_lines(hours)) + "\n"
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    if _LOG.is_file() and _LOG.stat().st_size > 0:
        existing = _LOG.read_text(encoding="utf-8")
        with _LOG.open("a", encoding="utf-8") as handle:
            if not existing.endswith("\n"):
                handle.write("\n")
            handle.write("\n")
            handle.write(block)
        return
    _LOG.write_text(block, encoding="utf-8")


def _write(locations, segments, neighborhoods, hours):
    _LOCATIONS.parent.mkdir(parents=True, exist_ok=True)
    _COVERAGE.parent.mkdir(parents=True, exist_ok=True)
    _EXCLUSIONS.parent.mkdir(parents=True, exist_ok=True)
    locations.drop(columns=["dates"], errors="ignore").to_parquet(_LOCATIONS, index=False)
    segments.drop(columns=["dates"], errors="ignore").to_parquet(_SEGMENTS, index=False)
    neighborhoods.to_csv(_NEIGHBORHOODS, index=False, na_rep="", lineterminator="\n")
    _coverage_frame(neighborhoods).to_csv(
        _COVERAGE, index=False, na_rep="", lineterminator="\n"
    )
    _exclusion_frame(locations).to_csv(
        _EXCLUSIONS, index=False, na_rep="", lineterminator="\n"
    )
    _append_log(hours)


def _read_inputs():
    if not _TRAFFIC.is_file():
        print(_TRAFFIC.as_posix())
        return None
    if not _SELECTION.is_file():
        print(_SELECTION.as_posix())
        return None
    try:
        frame = pd.read_parquet(_TRAFFIC)
    except Exception:
        print(_TRAFFIC.as_posix())
        return None
    try:
        payload = json.loads(_SELECTION.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(_SELECTION.as_posix())
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("periods"), list):
        print(_SELECTION.as_posix())
        return None
    return frame, payload["periods"]


def main():
    loaded = _read_inputs()
    if loaded is None:
        return 2
    frame, periods = loaded
    try:
        hours = _complete_by_year(frame)
        locations = location_years(hours)
        segments = segment_years(locations)
        neighborhoods = neighborhood_periods(segments, periods)
    except AggregateError as exc:
        print(exc.message)
        return int(exc.exit_code)
    _write(locations, segments, neighborhoods, hours)
    _print_periods(neighborhoods, periods)
    return 0


if __name__ == "__main__":
    sys.exit(main())
