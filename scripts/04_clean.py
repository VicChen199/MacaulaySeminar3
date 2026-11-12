"""Write cleaned asthma rates and cleaned traffic counts."""

import csv
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.clean_asthma import CleanError, clean_asthma
from lib.clean_traffic import clean_traffic
from lib.config import load_config

_SELECTION = Path("outputs/audit/source_selection.json")
_ASTHMA = Path("outputs/data/cleaned_asthma.csv")
_TRAFFIC = Path("outputs/data/cleaned_traffic.parquet")
_LOG = Path("outputs/cleaning_log.md")
_EXCLUSIONS = Path("outputs/audit/exclusions_clean.csv")
_EXPORT_SUFFIXES = {".csv", ".xlsx"}
_EXCLUSION_COLUMNS = ["stage", "key", "reason", "source"]
_TRAFFIC_COLUMNS = [
    ":id",
    "source",
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "vol",
    "segment",
    "wkt",
    "direction_normalized",
    "borough",
    "request",
    "ts_local",
    "monitoring_location_id",
    "hour_complete",
    "high_count_flag",
]


def _read_export(config):
    directory = Path(config["asthma"]["raw_dir"])
    if not directory.is_dir():
        print(directory.as_posix())
        return None
    files = sorted(
        item
        for item in directory.iterdir()
        if item.is_file() and item.suffix.lower() in _EXPORT_SUFFIXES
    )
    if len(files) != 1:
        print(directory.as_posix())
        return None
    path = files[0]
    try:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path, dtype=str)
        return pd.read_excel(path, dtype=str, engine="openpyxl")
    except Exception:
        print(path.as_posix())
        return None


def _read_selection():
    if not _SELECTION.is_file():
        print(_SELECTION.as_posix())
        return None
    try:
        payload = json.loads(_SELECTION.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(_SELECTION.as_posix())
        return None
    if not isinstance(payload, dict):
        print(_SELECTION.as_posix())
        return None
    return payload


def _selected_years(selection):
    periods = selection.get("periods")
    if not isinstance(periods, list):
        return None
    chosen = []
    seen = set()
    for item in periods:
        if not isinstance(item, dict) or not item.get("included"):
            continue
        source = item.get("source")
        years = item.get("years")
        if not isinstance(source, str) or source == "" or not isinstance(years, list):
            return None
        for year in years:
            try:
                year_number = int(year)
            except (TypeError, ValueError):
                return None
            key = (source, year_number)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(key)
    return chosen


def _empty_traffic():
    frame = pd.DataFrame(columns=_TRAFFIC_COLUMNS)
    for column in ("year", "month", "day", "hour", "minute"):
        frame[column] = pd.Series(dtype="Int64")
    frame["vol"] = pd.Series(dtype="float64")
    frame["hour_complete"] = pd.Series(dtype=bool)
    frame["high_count_flag"] = pd.Series(dtype=bool)
    frame["ts_local"] = pd.Series(dtype="datetime64[us]").dt.tz_localize(
        "America/New_York"
    )
    return frame[_TRAFFIC_COLUMNS]


def _clean_years(config, selection):
    selected = _selected_years(selection)
    if selected is None:
        print(_SELECTION.as_posix())
        return None
    frames = []
    exclusions = []
    removed = 0
    flagged = 0
    for source, year in selected:
        path = Path("data/raw/traffic") / source / f"year={year}" / "records.parquet"
        if not path.is_file():
            print(path.as_posix())
            return None
        raw = pd.read_parquet(path)
        cleaned, year_removed, year_flagged = clean_traffic(raw, config, source)
        removed += int(year_removed)
        flagged += int(year_flagged)
        exclusions.extend(list(cleaned.attrs.get("exclusions", [])))
        frames.append(cleaned)
        del raw
    if frames:
        traffic = pd.concat(frames, ignore_index=True)
    else:
        traffic = _empty_traffic()
    return traffic, exclusions, removed, flagged


def _summary_lines(asthma, removed, flagged):
    counts = {}
    order = []
    for period in asthma["period"]:
        label = str(period)
        if label not in counts:
            counts[label] = 0
            order.append(label)
        counts[label] += 1
    lines = [f"{label} {counts[label]}" for label in order]
    lines.append(f"duplicates removed {int(removed)}")
    lines.append(f"invalid counts flagged {int(flagged)}")
    return lines


def _write_outputs(asthma, traffic, exclusions, lines):
    _ASTHMA.parent.mkdir(parents=True, exist_ok=True)
    _EXCLUSIONS.parent.mkdir(parents=True, exist_ok=True)
    asthma.to_csv(_ASTHMA, index=False, na_rep="", lineterminator="\n")
    traffic.to_parquet(_TRAFFIC, index=False)
    _LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with _EXCLUSIONS.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(_EXCLUSION_COLUMNS)
        for row in exclusions:
            writer.writerow(
                [row["stage"], row["key"], row["reason"], row["source"]]
            )


def main():
    try:
        config = load_config()
        export = _read_export(config)
        if export is None:
            return 2
        asthma = clean_asthma(export, config)
        selection = _read_selection()
        if selection is None:
            return 2
        cleaned = _clean_years(config, selection)
        if cleaned is None:
            return 2
        traffic, exclusions, removed, flagged = cleaned
    except CleanError as exc:
        print(exc.message)
        return int(exc.exit_code)

    lines = _summary_lines(asthma, removed, flagged)
    _write_outputs(asthma, traffic, exclusions, lines)
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
