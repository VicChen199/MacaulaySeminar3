"""Choose one traffic source for each asthma period."""

import csv
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.audit import AuditError, score_coverage, select_source
from lib.config import load_config
from lib.geo import BoundaryError, ensure_boundaries
from lib.socrata import SocrataError, paginate

_PERIODS = Path("outputs/audit/asthma_time_periods.csv")
_COUNTS = Path("outputs/audit/traffic_year_counts.csv")
_SCHEMA = Path("outputs/audit/schema_snapshot.json")
_SELECTION = Path("outputs/audit/source_selection.json")
_COVERAGE = Path("outputs/audit/coverage_for_selection.csv")
_COMPARISON = Path("outputs/dataset_source_comparison.csv")
_SOURCE_ORDER = ("automated", "historical", "aadt")
_COVERAGE_COLUMNS = (
    "period",
    "uhf42_id",
    "n_eligible_segments",
    "n_locations",
    "n_complete_hours",
    "adequate",
    "source",
)
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


def _coverage_path(source, year):
    return Path("data/raw/traffic") / source / f"year={int(year)}" / "coverage.parquet"


def _needed_years(periods):
    years = []
    seen = set()
    for record in periods.to_dict(orient="records"):
        raw = record.get("constituent_years")
        text = "" if raw is None else str(raw)
        for part in text.split(","):
            part = part.strip()
            if not part or part.lower() == "nan":
                continue
            year = int(float(part))
            if year not in seen:
                seen.add(year)
                years.append(year)
    return years


def _page_source(config, source, years, schema):
    frames = {}
    for year in years:
        path = _coverage_path(source, year)
        paginate(config, source, year, schema, path)
        frames[int(year)] = pd.read_parquet(path)
    return frames


def _is_true(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() == "true"


def _adequate_count(coverage, period):
    if coverage is None or len(coverage.index) == 0:
        return 0
    count = 0
    for label, flag in zip(coverage["period"], coverage["adequate"]):
        if str(label).strip() != str(period).strip():
            continue
        if _is_true(flag):
            count += 1
    return count


def _segment_token(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, float):
        if value != value:
            return None
        if value.is_integer():
            return str(int(value))
        return str(value).strip()
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    return text


def _resolve_segment(columns, aliases):
    present = {str(column) for column in columns}
    for alias in aliases:
        if alias in present:
            return alias
    return None


def _raw_stats(frames, years, aliases):
    count = 0
    segments = set()
    for year in years:
        frame = frames.get(int(year))
        if frame is None:
            continue
        count += len(frame.index)
        column = _resolve_segment(frame.columns, aliases)
        if column is None:
            continue
        for value in frame[column]:
            token = _segment_token(value)
            if token is not None:
                segments.add(token)
    return count, len(segments)


def _uhf_with_traffic(coverage, period):
    if coverage is None or len(coverage.index) == 0:
        return 0
    count = 0
    for label, segments in zip(coverage["period"], coverage["n_eligible_segments"]):
        if str(label).strip() != str(period).strip():
            continue
        try:
            if int(segments) > 0:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


def _bool_text(value):
    return "true" if value else "false"


def _write_coverage(coverage_by_source):
    _COVERAGE.parent.mkdir(parents=True, exist_ok=True)
    with _COVERAGE.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(_COVERAGE_COLUMNS)
        for source in _SOURCE_ORDER:
            frame = coverage_by_source.get(source)
            if frame is None or len(frame.index) == 0:
                continue
            for record in frame.to_dict(orient="records"):
                writer.writerow(
                    [
                        record["period"],
                        record["uhf42_id"],
                        int(record["n_eligible_segments"]),
                        int(record["n_locations"]),
                        int(record["n_complete_hours"]),
                        _bool_text(_is_true(record["adequate"])),
                        record["source"],
                    ]
                )


def _write_comparison(periods, selection, coverage_by_source, raw_frames, config):
    minimum = int(config["thresholds"]["min_uhf_per_period"])
    audit_rows = periods.to_dict(orient="records")
    _COMPARISON.parent.mkdir(parents=True, exist_ok=True)
    with _COMPARISON.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(_COMPARISON_COLUMNS)
        for audit_row, chosen in zip(audit_rows, selection["periods"]):
            period = chosen["period"]
            years = chosen["years"]
            recommended = chosen["source"] if chosen["included"] else ""
            if recommended:
                raw_count, distinct = _raw_stats(
                    raw_frames.get(recommended, {}),
                    years,
                    config["fields"][recommended]["segment"],
                )
                n_traffic = _uhf_with_traffic(
                    coverage_by_source.get(recommended), period
                )
            else:
                raw_count = 0
                distinct = 0
                n_traffic = 0
            n_asthma = int(float(audit_row["n_rows"]))
            available = {
                source: _adequate_count(coverage_by_source.get(source), period)
                >= minimum
                for source in _SOURCE_ORDER
            }
            writer.writerow(
                [
                    period,
                    _bool_text(True),
                    _bool_text(available["automated"]),
                    _bool_text(available["historical"]),
                    _bool_text(available["aadt"]),
                    raw_count,
                    distinct,
                    n_traffic,
                    n_asthma,
                    "",
                    "",
                    recommended,
                ]
            )


def _write_selection(selection):
    _SELECTION.parent.mkdir(parents=True, exist_ok=True)
    _SELECTION.write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")


def _print_selection(selection):
    print(f"scenario {selection['scenario']}")
    for item in selection["periods"]:
        if item["included"]:
            print(f"{item['period']} {item['source']}")
        else:
            print(f"{item['period']} excluded {item['reason']}")


def _read_audits():
    missing = [path for path in (_PERIODS, _COUNTS, _SCHEMA) if not path.is_file()]
    if missing:
        print(missing[0].as_posix())
        return None
    try:
        periods = pd.read_csv(_PERIODS, dtype=str)
    except Exception:
        print(_PERIODS.as_posix())
        return None
    try:
        pd.read_csv(_COUNTS, dtype=str)
    except Exception:
        print(_COUNTS.as_posix())
        return None
    try:
        schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    except Exception:
        print(_SCHEMA.as_posix())
        return None
    if not isinstance(schema, dict):
        print(_SCHEMA.as_posix())
        return None
    return periods, schema


def main():
    try:
        config = load_config()
        loaded = _read_audits()
        if loaded is None:
            return 2
        periods, schema = loaded
        shapefile = ensure_boundaries(config)
        years = _needed_years(periods)
        raw_frames = {}
        coverage_by_source = {}
        means_by_source = {}
        raw_frames["automated"] = _page_source(config, "automated", years, schema)
        coverage_by_source["automated"], means_by_source["automated"] = score_coverage(
            config,
            "automated",
            periods,
            raw_frames["automated"],
            shapefile,
        )
        minimum = int(config["thresholds"]["min_uhf_per_period"])
        period_names = [str(value).strip() for value in periods["period"]]
        auto_cover = {
            period: _adequate_count(coverage_by_source["automated"], period) >= minimum
            for period in period_names
        }
        automated_all = bool(period_names) and all(auto_cover.values())
        if period_names and not automated_all:
            raw_frames["historical"] = _page_source(
                config, "historical", years, schema
            )
            (
                coverage_by_source["historical"],
                means_by_source["historical"],
            ) = score_coverage(
                config,
                "historical",
                periods,
                raw_frames["historical"],
                shapefile,
            )
            hist_cover = {
                period: _adequate_count(coverage_by_source["historical"], period)
                >= minimum
                for period in period_names
            }
            if not any(auto_cover.values()) and not any(hist_cover.values()):
                raw_frames["aadt"] = _page_source(config, "aadt", years, schema)
                coverage_by_source["aadt"], means_by_source["aadt"] = score_coverage(
                    config,
                    "aadt",
                    periods,
                    raw_frames["aadt"],
                    shapefile,
                )
        selection = select_source(
            config, periods, coverage_by_source, means_by_source
        )
    except (AuditError, SocrataError, BoundaryError) as exc:
        print(exc.message)
        return exc.exit_code

    _write_selection(selection)
    _write_coverage(coverage_by_source)
    _write_comparison(periods, selection, coverage_by_source, raw_frames, config)
    _print_selection(selection)
    if any(item["included"] for item in selection["periods"]):
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
