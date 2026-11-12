"""Contract tests for neighborhood traffic exposure.

``lib/aggregate.py`` and ``scripts/06_aggregate_traffic.py`` are absent in
this worktree. These tests lock quarter-hour sums, one segment vote, the
neighborhood mean, pooled years, and the terminal line.
"""

import csv
import json
import re
import runpy
import shutil
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lib.config import load_config

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAME = "scripts/06_aggregate_traffic.py"
TRAFFIC_PATH = Path("outputs/data/traffic_with_uhf42.parquet")
SELECTION_PATH = Path("outputs/audit/source_selection.json")
LOCATION_PATH = Path("outputs/data/traffic_location_year.parquet")
SEGMENT_PATH = Path("outputs/data/traffic_segment_year.parquet")
UHF_PATH = Path("outputs/data/traffic_uhf42_year.csv")
COVERAGE_PATH = Path("outputs/quality/traffic_coverage_by_uhf42.csv")
EXCLUSIONS_PATH = Path("outputs/audit/exclusions_aggregate.csv")
LOG_PATH = Path("outputs/cleaning_log.md")
LOG_MARKER = "clean-marker"
QUARTERS = (0, 15, 30, 45)
WKT = "POINT(-73.95 40.75)"
TRAFFIC_COLUMNS = (
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
    "road_segment_id",
    "uhf42_id",
)
HOUR_COLUMNS = (
    "monitoring_location_id",
    "road_segment_id",
    "uhf42_id",
    "source",
    "year",
    "date",
    "hour",
    "direction_normalized",
    "session",
    "vol",
)
LOCATION_COLUMNS = (
    "monitoring_location_id",
    "road_segment_id",
    "uhf42_id",
    "year",
    "mean_hourly",
    "hour_balanced_mean",
    "n_complete_hours",
    "n_days",
    "n_sessions",
    "months",
    "weekday_hours",
    "weekend_hours",
    "eligible",
    "exclusion_reason",
)
SEGMENT_COLUMNS = (
    "road_segment_id",
    "uhf42_id",
    "year",
    "mean_hourly",
    "hour_balanced_mean",
    "n_locations",
    "n_complete_hours",
    "n_days",
    "eligible",
    "own_segment",
)
UHF_COLUMNS = (
    "uhf42_id",
    "period",
    "traffic_mean_vehicles_per_hour",
    "traffic_mean_hour_balanced",
    "traffic_median_vehicles_per_hour",
    "traffic_sd_vehicles_per_hour",
    "n_road_segments",
    "n_monitoring_locations",
    "n_observation_days",
    "n_complete_hours",
    "n_sessions",
    "months_represented",
    "weekday_weekend_coverage",
    "traffic_source",
    "coverage_status",
)
COVERAGE_COLUMNS = (
    "period",
    "uhf42_id",
    "n_eligible_segments",
    "n_locations",
    "n_complete_hours",
    "adequate",
    "source",
)
EXCLUSION_COLUMNS = ("stage", "key", "reason", "source")
PERIOD_LINE = re.compile(r"^(\S+) eligible (\d+) median (\S+)$")


def _require_functions(reason):
    missing = []
    functions = None
    module = ROOT / "lib" / "aggregate.py"
    if not module.is_file():
        missing.append(
            "lib/aggregate.py (complete_hours, location_years, "
            "segment_years, neighborhood_periods)"
        )
    else:
        try:
            from lib.aggregate import (
                complete_hours,
                location_years,
                neighborhood_periods,
                segment_years,
            )
        except ImportError:
            missing.append(
                "lib/aggregate.py (complete_hours, location_years, "
                "segment_years, neighborhood_periods)"
            )
        else:
            functions = {
                "complete_hours": complete_hours,
                "location_years": location_years,
                "segment_years": segment_years,
                "neighborhood_periods": neighborhood_periods,
            }
            for name, func in functions.items():
                if not callable(func):
                    missing.append(f"lib/aggregate.py {name}")
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    return functions


def _require_script(reason):
    missing = []
    functions = None
    module = ROOT / "lib" / "aggregate.py"
    script = ROOT / SCRIPT_NAME
    if not module.is_file():
        missing.append(
            "lib/aggregate.py (complete_hours, location_years, "
            "segment_years, neighborhood_periods)"
        )
    else:
        functions = _require_functions(reason)
    if not script.is_file():
        missing.append(
            "scripts/06_aggregate_traffic.py "
            "(python scripts/06_aggregate_traffic.py takes no arguments)"
        )
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    return functions, script


def _is_null(value):
    if value is None:
        return True
    try:
        if bool(pd.isna(value)):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and value.strip().lower() in {
        "",
        "nan",
        "none",
        "null",
        "<na>",
        "nat",
    }:
        return True
    return False


def _text(value):
    assert not _is_null(value), value
    if isinstance(value, bool):
        raise AssertionError(value)
    return str(value).strip()


def _id_text(value):
    assert not _is_null(value), value
    if isinstance(value, bool):
        raise AssertionError(value)
    try:
        import numbers

        if isinstance(value, numbers.Integral):
            return str(int(value))
        if isinstance(value, numbers.Real):
            number = float(value)
            if number.is_integer():
                return str(int(number))
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0+", text):
        return str(int(float(text)))
    return text


def _number(value):
    assert not isinstance(value, bool), value
    assert not _is_null(value), value
    return float(value)


def _count(value):
    number = _number(value)
    assert number.is_integer(), value
    return int(number)


def _flag(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, np.bool_):
        return bool(value)
    raise AssertionError(f"expected a bool, got {value!r}")


def _as_bool(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return bool(int(value))
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise AssertionError(value)


def _day(value):
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None:
            value = value.tz_convert("America/New_York")
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "year") and hasattr(value, "month") and not isinstance(value, str):
        return f"{int(value.year):04d}-{int(value.month):02d}-{int(value.day):02d}"
    return str(value).strip()[:10]


def _hour(value):
    return int(float(value))


def _lines(text):
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def _close(value, expected):
    assert _number(value) == pytest.approx(float(expected))


def _assert_columns(frame, columns):
    assert tuple(frame.columns) == tuple(columns), list(frame.columns)


def _require_columns(frame, columns):
    missing = [column for column in columns if column not in frame.columns]
    assert not missing, missing


def _existed():
    return {path: path.exists() for path in (ROOT / "outputs", ROOT / "data")}


def _cleanup(existed):
    created = []
    for path, was_there in existed.items():
        if path.exists() and not was_there:
            shutil.rmtree(path)
            created.append(path.name)
    return created


@contextmanager
def _guard_repo():
    existed = _existed()
    try:
        yield
    finally:
        created = _cleanup(existed)
        assert not created, (
            "fixture test must not write the repository outputs/ or data/ tree"
        )


def _forbid_network(monkeypatch):
    calls = []

    def send(self, prepared, **kwargs):
        url = getattr(prepared, "url", "")
        calls.append(url)
        raise AssertionError(f"fixture test must not call Socrata ({url})")

    def get(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        calls.append(url)
        raise AssertionError(f"fixture test must not call Socrata ({url})")

    def urlopen(*args, **kwargs):
        target = args[0] if args else ""
        calls.append(target)
        raise AssertionError("fixture test must not call Socrata")

    monkeypatch.setattr("requests.sessions.Session.send", send)
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


def _assert_no_local_writes(tmp_path):
    assert not (tmp_path / "outputs").exists(), (
        "complete_hours on a fixture must not create outputs/"
    )
    assert not (tmp_path / "data").exists(), (
        "complete_hours on a fixture must not create data/"
    )


def _copy_tree(src, dest):
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        target = dest / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _pop_lib_modules():
    saved = {}
    for name in list(sys.modules):
        if name == "lib" or name.startswith("lib."):
            saved[name] = sys.modules.pop(name)
    return saved


def _restore_modules(saved):
    for name in list(sys.modules):
        if name == "lib" or name.startswith("lib."):
            del sys.modules[name]
    sys.modules.update(saved)


def _copy_path(repo):
    entries = [str(repo)]
    for entry in sys.path:
        if entry == str(repo):
            continue
        if entry in {"", "."}:
            entries.append(entry)
            continue
        try:
            if Path(entry).resolve() == ROOT:
                continue
        except OSError:
            pass
        entries.append(entry)
    return entries


def _invoke(script):
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code
    return 0


def _call(func, *args, **kwargs):
    try:
        value = func(*args, **kwargs)
    except SystemExit as exc:
        code = 0 if exc.code is None else exc.code
        return code, str(exc), None
    except Exception as exc:
        code = getattr(exc, "exit_code", None)
        if code is None:
            raise
        message = getattr(exc, "message", None) or str(exc)
        return code, message, None
    if isinstance(value, dict) and "exit_code" in value:
        return value["exit_code"], str(value.get("message") or ""), value
    return 0, "", value


def _quiet_call(capsys, func, *args, **kwargs):
    code, message, value = _call(func, *args, **kwargs)
    captured = capsys.readouterr()
    text = f"{captured.out}{captured.err}{message}"
    return code, text, value


class _Ids:
    def __init__(self):
        self._n = 0

    def next(self):
        self._n += 1
        return str(self._n)


def _quarter_volumes(total):
    total = int(total)
    base = total // 4
    return (base, base, base, total - 3 * base)


def _local_stamp(year, month, day, hour, minute):
    return pd.Timestamp(
        year=int(year),
        month=int(month),
        day=int(day),
        hour=int(hour),
        minute=int(minute),
        tz="America/New_York",
    )


def _traffic_row(
    ids,
    *,
    loc,
    uhf,
    year,
    month,
    day,
    hour,
    minute,
    vol,
    direction,
    road_segment_id,
    source="automated",
    request="",
    segment=None,
    ts=None,
    hour_complete=False,
):
    if ts is None:
        ts = _local_stamp(year, month, day, hour, 0 if minute is None else minute)
    return {
        ":id": ids.next(),
        "source": source,
        "year": year,
        "month": month,
        "day": day,
        "hour": hour,
        "minute": minute,
        "vol": vol,
        "segment": "raw-decoy" if segment is None else segment,
        "wkt": WKT,
        "direction_normalized": direction,
        "borough": "Manhattan",
        "request": request,
        "ts_local": ts,
        "monitoring_location_id": loc,
        "hour_complete": bool(hour_complete),
        "high_count_flag": False,
        "road_segment_id": road_segment_id,
        "uhf42_id": uhf,
    }


def _traffic_frame(rows):
    frame = pd.DataFrame(rows)
    frame = frame.loc[:, list(TRAFFIC_COLUMNS)].copy()
    frame["road_segment_id"] = pd.Series(
        frame["road_segment_id"].tolist(), dtype="object"
    )
    frame["request"] = pd.Series(
        ["" if _is_null(value) else str(value) for value in frame["request"]],
        dtype="object",
    )
    frame["direction_normalized"] = pd.Series(
        frame["direction_normalized"].tolist(), dtype="object"
    )
    frame["monitoring_location_id"] = pd.Series(
        frame["monitoring_location_id"].tolist(), dtype="object"
    )
    frame["uhf42_id"] = pd.Series(frame["uhf42_id"].tolist(), dtype="object")
    return frame


def _quarter_rows(
    ids,
    *,
    loc,
    uhf,
    year,
    month,
    day,
    hour,
    volumes,
    road_segment_id,
    direction="NB",
    request="",
    segment=None,
    minutes=QUARTERS,
    ts_for_minute=None,
    calendar=None,
):
    rows = []
    for minute, vol in zip(minutes, volumes):
        stamp = None if ts_for_minute is None else ts_for_minute(minute)
        year_value, month_value, day_value, hour_value = (
            (year, month, day, hour) if calendar is None else calendar(minute)
        )
        rows.append(
            _traffic_row(
                ids,
                loc=loc,
                uhf=uhf,
                year=year_value,
                month=month_value,
                day=day_value,
                hour=hour_value,
                minute=minute,
                vol=vol,
                direction=direction,
                request=request,
                road_segment_id=road_segment_id,
                segment=segment,
                ts=stamp,
            )
        )
    return rows


def _hour_block(
    ids,
    *,
    loc,
    uhf,
    year,
    month,
    day,
    hours,
    volume,
    road_segment_id,
    direction="NB",
    request="",
    segment=None,
):
    rows = []
    for hour in hours:
        rows.extend(
            _quarter_rows(
                ids,
                loc=loc,
                uhf=uhf,
                year=year,
                month=month,
                day=day,
                hour=hour,
                volumes=_quarter_volumes(volume),
                direction=direction,
                request=request,
                road_segment_id=road_segment_id,
                segment=segment,
            )
        )
    return rows


def _location_block(ids, *, loc, uhf, road_segment_id, days, direction="NB", request="", segment=None):
    """``days`` is a list of (year, month, day, hours, volume)."""
    rows = []
    for year, month, day, hours, volume in days:
        rows.extend(
            _hour_block(
                ids,
                loc=loc,
                uhf=uhf,
                year=year,
                month=month,
                day=day,
                hours=hours,
                volume=volume,
                direction=direction,
                request=request,
                road_segment_id=road_segment_id,
                segment=segment,
            )
        )
    return rows


def _pattern_days(year, volumes):
    """Monday hours 0-15 at volumes[0], Tuesday hours 0-7 at volumes[1]."""
    monday = pd.Timestamp(year=year, month=1, day=1)
    monday = monday + pd.Timedelta(days=int((0 - monday.day_of_week) % 7))
    tuesday = monday + pd.Timedelta(days=1)
    assert int(monday.day_of_week) == 0, monday
    assert int(tuesday.day_of_week) == 1, tuesday
    return [
        (year, int(monday.month), int(monday.day), list(range(16)), volumes[0]),
        (year, int(tuesday.month), int(tuesday.day), list(range(8)), volumes[1]),
    ]


def _uniform_days(year, volume):
    return _pattern_days(year, (volume, volume))


def _where(frame, column, expected, kind="text"):
    if kind == "text":
        mask = frame[column].map(_text) == str(expected)
    elif kind == "id":
        mask = frame[column].map(_id_text) == str(expected)
    elif kind == "day":
        mask = frame[column].map(_day) == expected
    elif kind == "hour":
        mask = frame[column].map(_hour) == int(expected)
    elif kind == "year":
        mask = frame[column].map(_count) == int(expected)
    else:
        raise AssertionError(kind)
    return frame.loc[mask].copy()


def _one(frame, column, expected, kind="text"):
    matched = _where(frame, column, expected, kind=kind)
    assert len(matched) == 1, (column, expected, frame)
    return matched.iloc[0]


def _frame_from_return(value, required):
    if isinstance(value, pd.DataFrame):
        frames = [value]
    elif isinstance(value, (tuple, list)):
        frames = [item for item in value if isinstance(item, pd.DataFrame)]
    else:
        frames = []
    matches = [frame for frame in frames if required in frame.columns]
    assert len(matches) == 1, (
        f"expected a table with {required}, got "
        f"{[list(frame.columns) for frame in frames]}"
    )
    return matches[0]


def _valid_rows(frame, period):
    matched = _where(frame, "period", period)
    kept = []
    for _, row in matched.iterrows():
        if _text(row["coverage_status"]) == "valid":
            kept.append(row)
    return kept


def _assert_invalid_means_are_null(frame):
    if "coverage_status" not in frame.columns:
        return
    for _, row in frame.iterrows():
        if _text(row["coverage_status"]) == "valid":
            continue
        assert _is_null(row["traffic_mean_vehicles_per_hour"]), row.to_dict()


def _period_lines(text):
    found = []
    for line in _lines(text):
        match = PERIOD_LINE.match(line)
        if match:
            found.append(match.groups())
    return found


def _assert_period_lines(text, expected):
    found = _period_lines(text)
    assert [item[0] for item in found] == [item[0] for item in expected], text
    for (period, count, median), (got_period, got_count, got_median) in zip(expected, found):
        assert got_period == period, text
        assert int(got_count) == int(count), text
        if median is None:
            assert got_median == "none", text
        else:
            assert float(got_median) == pytest.approx(float(median)), text
    return found


def _selection(periods):
    return {
        "scenario": "A",
        "mix_used": False,
        "comparability_pass": None,
        "comparability_r": None,
        "comparability_n": None,
        "periods": periods,
    }


def _period(period, years, included=True, source="automated", reason=""):
    return {
        "period": period,
        "source": source,
        "years": [int(year) for year in years],
        "included": bool(included),
        "reason": reason,
    }


def _segment_row(
    segment,
    uhf,
    year,
    mean_hourly,
    hour_balanced,
    *,
    n_locations=1,
    n_complete_hours=24,
    n_days=2,
    eligible=True,
    own_segment=False,
):
    return {
        "road_segment_id": segment,
        "uhf42_id": uhf,
        "year": int(year),
        "mean_hourly": float(mean_hourly),
        "hour_balanced_mean": float(hour_balanced),
        "n_locations": int(n_locations),
        "n_complete_hours": int(n_complete_hours),
        "n_days": int(n_days),
        "eligible": bool(eligible),
        "own_segment": bool(own_segment),
    }


def _segment_frame(rows):
    frame = pd.DataFrame(rows)
    return frame.loc[:, list(SEGMENT_COLUMNS)].copy()


def _write_parquet(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _prepare(tmp_path, script_src, traffic, periods):
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    script = repo / SCRIPT_NAME
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_src, script)
    _write_parquet(repo / TRAFFIC_PATH, traffic)
    selection = repo / SELECTION_PATH
    selection.parent.mkdir(parents=True, exist_ok=True)
    selection.write_text(
        json.dumps(_selection(periods), indent=2) + "\n",
        encoding="utf-8",
    )
    log = repo / LOG_PATH
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(LOG_MARKER + "\n", encoding="utf-8")
    return repo, script


def _run_script(monkeypatch, capsys, tmp_path, script_src, traffic, periods):
    root_toml = (ROOT / "config" / "pipeline.toml").read_bytes()
    repo, script = _prepare(tmp_path, script_src, traffic, periods)
    project_toml = (repo / "config" / "pipeline.toml").read_bytes()
    saved = _pop_lib_modules()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "path", _copy_path(repo))
    monkeypatch.setattr(sys, "argv", [str(script)])
    calls = _forbid_network(monkeypatch)
    try:
        assert sys.argv == [str(script)], (
            "python scripts/06_aggregate_traffic.py takes no arguments"
        )
        code = _invoke(script)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert (repo / "config" / "pipeline.toml").read_bytes() == project_toml
        assert (ROOT / "config" / "pipeline.toml").read_bytes() == root_toml
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()
        assert calls == [], "fixture test must not call Socrata"
        return code, text, repo
    finally:
        _restore_modules(saved)


def _csv_table(path):
    assert path.is_file(), path
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or []), list(reader)


def _assert_success_files(repo):
    for path in (
        LOCATION_PATH,
        SEGMENT_PATH,
        UHF_PATH,
        COVERAGE_PATH,
        EXCLUSIONS_PATH,
        LOG_PATH,
    ):
        assert (repo / path).is_file(), path
    locations = pd.read_parquet(repo / LOCATION_PATH)
    segments = pd.read_parquet(repo / SEGMENT_PATH)
    _assert_columns(locations, LOCATION_COLUMNS)
    _assert_columns(segments, SEGMENT_COLUMNS)
    uhf_names, _uhf_rows = _csv_table(repo / UHF_PATH)
    coverage_names, _coverage_rows = _csv_table(repo / COVERAGE_PATH)
    exclusion_names, _exclusion_rows = _csv_table(repo / EXCLUSIONS_PATH)
    assert uhf_names == UHF_COLUMNS
    assert coverage_names == COVERAGE_COLUMNS
    assert exclusion_names == EXCLUSION_COLUMNS
    log = (repo / LOG_PATH).read_text(encoding="utf-8")
    assert LOG_MARKER in log
    lowered = log.lower()
    for word in ("hour", "weekday", "weekend", "month"):
        assert word in lowered, log
    return locations, segments


def _coverage_rows(repo, period, uhf):
    frame = pd.read_csv(repo / COVERAGE_PATH)
    _assert_columns(frame, COVERAGE_COLUMNS)
    matched = frame[
        frame["period"].map(_text).eq(period)
        & frame["uhf42_id"].map(_id_text).eq(str(uhf))
    ]
    assert len(matched) >= 1, frame
    return matched


def _assert_coverage(repo, period, uhf, segments, locations, adequate, source="automated"):
    matched = _coverage_rows(repo, period, uhf)
    for _, row in matched.iterrows():
        assert _count(row["n_eligible_segments"]) == int(segments)
        assert _count(row["n_locations"]) == int(locations)
        assert _as_bool(row["adequate"]) is bool(adequate)
        assert _text(row["source"]) == source


def _edit_toml(repo, old, new):
    path = repo / "config" / "pipeline.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


def _import_from(repo, monkeypatch, name):
    saved = _pop_lib_modules()
    monkeypatch.setattr(sys, "path", _copy_path(repo))
    try:
        from lib import aggregate as aggregate_module

        return saved, getattr(aggregate_module, name)
    except Exception:
        _restore_modules(saved)
        raise


def _eligible_location(frame, loc):
    matched = _where(frame, "monitoring_location_id", loc)
    assert len(matched) == 1, frame
    return matched.iloc[0]


def test_four_quarters_sum_to_one_hour_and_three_quarters_drop(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "Four quarter volumes 1, 2, 3, and 4 become one hour whose volume "
            "is 10. Three quarters produce no hour. Duplicate minute volumes "
            "are summed. The call does not create outputs/ and does not call Socrata."
        )
        ids = _Ids()
        rows = []
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-full",
                uhf="101",
                year=2019,
                month=4,
                day=2,
                hour=8,
                volumes=(1, 2, 3, 4),
                request="REQ-1",
                road_segment_id="seg-full",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-short",
                uhf="101",
                year=2019,
                month=4,
                day=2,
                hour=8,
                volumes=(5, 6, 7),
                minutes=(0, 15, 30),
                request="REQ-1",
                road_segment_id="seg-short",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-dup",
                uhf="101",
                year=2019,
                month=4,
                day=2,
                hour=9,
                volumes=(1, 1, 2, 3, 4),
                minutes=(0, 0, 15, 30, 45),
                request="REQ-DUP",
                road_segment_id="seg-dup",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-repeat",
                uhf="101",
                year=2019,
                month=4,
                day=2,
                hour=10,
                volumes=(1, 1, 2, 3),
                minutes=(0, 0, 15, 30),
                request="REQ-REPEAT",
                road_segment_id="seg-repeat",
            )
        )
        code, text, value = _quiet_call(capsys, functions["complete_hours"], _traffic_frame(rows))
        assert code == 0, text
        hours = value
        assert isinstance(hours, pd.DataFrame), type(hours)
        _require_columns(hours, HOUR_COLUMNS)
        full = _where(hours, "monitoring_location_id", "loc-full")
        assert len(full) == 1, hours
        assert _number(full.iloc[0]["vol"]) == pytest.approx(10)
        assert _text(full.iloc[0]["session"]) == "REQ-1"
        assert _hour(full.iloc[0]["hour"]) == 8
        assert _day(full.iloc[0]["date"]) == "2019-04-02"
        dup = _where(hours, "monitoring_location_id", "loc-dup")
        assert len(dup) == 1, hours
        assert _number(dup.iloc[0]["vol"]) == pytest.approx(11)
        assert _text(dup.iloc[0]["session"]) == "REQ-DUP"
        assert _where(hours, "monitoring_location_id", "loc-short").empty
        assert _where(hours, "monitoring_location_id", "loc-repeat").empty
        assert set(hours["monitoring_location_id"].map(_text)) == {"loc-full", "loc-dup"}
        _assert_no_local_writes(tmp_path)
        assert calls == [], "fixture test must not call Socrata"


def test_session_is_request_or_location_date_direction(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "The session is the request value when it is non-empty. "
            "Otherwise it is location|date|direction. Quarters from different "
            "sessions do not form one hour."
        )
        ids = _Ids()
        rows = []
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-req",
                uhf="101",
                year=2019,
                month=5,
                day=6,
                hour=5,
                volumes=(1, 2, 3, 4),
                request="R1",
                road_segment_id="seg-req",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-req",
                uhf="101",
                year=2019,
                month=5,
                day=6,
                hour=5,
                volumes=(5, 6, 7, 8),
                request="R2",
                road_segment_id="seg-req",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-split",
                uhf="101",
                year=2019,
                month=5,
                day=6,
                hour=6,
                volumes=(1, 2),
                minutes=(0, 15),
                request="R1",
                road_segment_id="seg-split",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-split",
                uhf="101",
                year=2019,
                month=5,
                day=6,
                hour=6,
                volumes=(3, 4),
                minutes=(30, 45),
                request="R2",
                road_segment_id="seg-split",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-fallback",
                uhf="101",
                year=2019,
                month=5,
                day=6,
                hour=4,
                volumes=(1, 2, 3, 4),
                request="",
                direction="NB",
                road_segment_id="seg-fallback",
            )
        )
        code, text, hours = _quiet_call(
            capsys, functions["complete_hours"], _traffic_frame(rows)
        )
        assert code == 0, text
        _require_columns(hours, HOUR_COLUMNS)
        requested = _where(hours, "monitoring_location_id", "loc-req")
        assert len(requested) == 2, hours
        by_session = {
            _text(row["session"]): _number(row["vol"])
            for _, row in requested.iterrows()
        }
        assert by_session == {"R1": pytest.approx(10), "R2": pytest.approx(26)}
        assert _where(hours, "monitoring_location_id", "loc-split").empty
        fallback = _one(hours, "monitoring_location_id", "loc-fallback")
        assert _text(fallback["session"]) == "loc-fallback|2019-05-06|NB"
        assert _number(fallback["vol"]) == pytest.approx(10)
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_local_date_and_hour_come_from_ts_local(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "The date and hour come from ts_local in America/New_York."
        )
        ids = _Ids()
        rows = []

        def _utc(month, day, hour):
            def stamp(minute):
                return pd.Timestamp(
                    year=2019,
                    month=month,
                    day=day,
                    hour=hour,
                    minute=minute,
                    tz="UTC",
                )

            def calendar(_minute):
                return (2019, month, day, hour)

            return stamp, calendar

        winter_stamp, winter_calendar = _utc(1, 1, 4)
        summer_stamp, summer_calendar = _utc(7, 1, 3)
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-year",
                uhf="101",
                year=2019,
                month=1,
                day=1,
                hour=4,
                volumes=(1, 2, 3, 4),
                request="",
                road_segment_id="seg-year",
                ts_for_minute=winter_stamp,
                calendar=winter_calendar,
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-summer",
                uhf="101",
                year=2019,
                month=7,
                day=1,
                hour=3,
                volumes=(1, 2, 3, 4),
                request="",
                road_segment_id="seg-summer",
                ts_for_minute=summer_stamp,
                calendar=summer_calendar,
            )
        )
        code, text, hours = _quiet_call(
            capsys, functions["complete_hours"], _traffic_frame(rows)
        )
        assert code == 0, text
        winter = _one(hours, "monitoring_location_id", "loc-year")
        assert _day(winter["date"]) == "2018-12-31"
        assert _hour(winter["hour"]) == 23
        assert _count(winter["year"]) == 2018
        assert _text(winter["session"]) == "loc-year|2018-12-31|NB"
        assert _number(winter["vol"]) == pytest.approx(10)
        summer = _one(hours, "monitoring_location_id", "loc-summer")
        assert _day(summer["date"]) == "2019-06-30"
        assert _hour(summer["hour"]) == 23
        assert _count(summer["year"]) == 2019
        assert _text(summer["session"]) == "loc-summer|2019-06-30|NB"
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_two_directions_sum_and_one_direction_keeps_its_hour(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "Directions are counted per monitoring location. One direction "
            "keeps its hour. Two directions keep an hour only when both are "
            "complete for that location, date, hour, and session, and the "
            "volume is the sum. The kept direction is the labels sorted and "
            "joined with |. A missing direction is not stored as zero."
        )
        ids = _Ids()
        rows = []
        # SB is listed before NB so the kept label has to be sorted.
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-two",
                uhf="101",
                year=2019,
                month=4,
                day=3,
                hour=8,
                volumes=(1, 1, 2, 2),
                direction="SB",
                request="R1",
                road_segment_id="seg-two",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-two",
                uhf="101",
                year=2019,
                month=4,
                day=3,
                hour=8,
                volumes=(4, 2, 2, 2),
                direction="NB",
                request="R1",
                road_segment_id="seg-two",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-two",
                uhf="101",
                year=2019,
                month=4,
                day=3,
                hour=8,
                volumes=(25, 25, 25, 25),
                direction="NB",
                request="R2",
                road_segment_id="seg-two",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-two",
                uhf="101",
                year=2019,
                month=4,
                day=3,
                hour=9,
                volumes=(1, 2, 3, 4),
                direction="NB",
                request="R1",
                road_segment_id="seg-two",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-solo",
                uhf="101",
                year=2019,
                month=4,
                day=2,
                hour=6,
                volumes=(2, 2, 2, 2),
                direction="EB",
                request="",
                road_segment_id="seg-solo",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-fallback",
                uhf="102",
                year=2019,
                month=4,
                day=4,
                hour=7,
                volumes=(1, 1, 1, 0),
                direction="SB",
                request="",
                road_segment_id="seg-fallback",
            )
        )
        rows.extend(
            _quarter_rows(
                ids,
                loc="loc-fallback",
                uhf="102",
                year=2019,
                month=4,
                day=4,
                hour=7,
                volumes=(1, 1, 1, 1),
                direction="NB",
                request="",
                road_segment_id="seg-fallback",
            )
        )
        code, text, hours = _quiet_call(
            capsys, functions["complete_hours"], _traffic_frame(rows)
        )
        assert code == 0, text
        combined = _where(hours, "monitoring_location_id", "loc-two")
        assert len(combined) == 1, hours
        assert _number(combined.iloc[0]["vol"]) == pytest.approx(16)
        assert _text(combined.iloc[0]["direction_normalized"]) == "NB|SB"
        assert _text(combined.iloc[0]["session"]) == "R1"
        assert _hour(combined.iloc[0]["hour"]) == 8
        solo = _one(hours, "monitoring_location_id", "loc-solo")
        assert _number(solo["vol"]) == pytest.approx(8)
        assert _text(solo["direction_normalized"]) == "EB"
        assert _text(solo["session"]) == "loc-solo|2019-04-02|EB"
        fallback = _one(hours, "monitoring_location_id", "loc-fallback")
        assert _number(fallback["vol"]) == pytest.approx(7)
        assert _text(fallback["direction_normalized"]) == "NB|SB"
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_three_directions_sum_when_every_direction_is_present(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "A location that shows three directions keeps an hour only when "
            "all three are complete, and the volume is their sum. The kept "
            "direction is the labels sorted and joined with |. An hour missing "
            "one of those directions is dropped. The call exits 0."
        )
        ids = _Ids()
        rows = []
        for direction, volumes in (
            ("NB", (1, 2, 3, 4)),
            ("SB", (1, 1, 1, 1)),
            ("EB", (2, 2, 2, 2)),
        ):
            rows.extend(
                _quarter_rows(
                    ids,
                    loc="loc-three",
                    uhf="101",
                    year=2019,
                    month=4,
                    day=5,
                    hour=8,
                    volumes=volumes,
                    direction=direction,
                    request="R1",
                    road_segment_id="seg-three",
                )
            )
        for direction, volumes in (
            ("NB", (9, 9, 9, 9)),
            ("SB", (9, 9, 9, 9)),
        ):
            rows.extend(
                _quarter_rows(
                    ids,
                    loc="loc-three",
                    uhf="101",
                    year=2019,
                    month=4,
                    day=5,
                    hour=9,
                    volumes=volumes,
                    direction=direction,
                    request="R1",
                    road_segment_id="seg-three",
                )
            )
        code, text, hours = _quiet_call(
            capsys, functions["complete_hours"], _traffic_frame(rows)
        )
        assert code == 0, text
        assert "loc-three" not in text
        combined = _where(hours, "monitoring_location_id", "loc-three")
        assert len(combined) == 1, hours
        assert _number(combined.iloc[0]["vol"]) == pytest.approx(22)
        assert _text(combined.iloc[0]["direction_normalized"]) == "EB|NB|SB"
        assert _text(combined.iloc[0]["session"]) == "R1"
        assert _hour(combined.iloc[0]["hour"]) == 8
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_empty_direction_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "An empty direction exits 2 and the message contains the location id. "
            "It is not stored as zero."
        )
        ids = _Ids()
        rows = _quarter_rows(
            ids,
            loc="loc-empty",
            uhf="101",
            year=2019,
            month=4,
            day=6,
            hour=8,
            volumes=(1, 2, 3, 4),
            direction="",
            request="R1",
            road_segment_id="seg-empty",
        )
        code, text, value = _quiet_call(
            capsys, functions["complete_hours"], _traffic_frame(rows)
        )
        assert code == 2, text
        assert "loc-empty" in text
        if isinstance(value, pd.DataFrame):
            pytest.fail("an empty direction is not stored as zero")
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_minute_outside_the_quarter_set_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "A minute outside {0, 15, 30, 45} exits 2 and the message contains that minute."
        )
        ids = _Ids()
        rows = _quarter_rows(
            ids,
            loc="loc-minute",
            uhf="101",
            year=2019,
            month=3,
            day=6,
            hour=8,
            volumes=(1, 2, 3, 4),
            minutes=(0, 15, 30, 10),
            request="R1",
            road_segment_id="seg-minute",
        )
        code, text, value = _quiet_call(
            capsys, functions["complete_hours"], _traffic_frame(rows)
        )
        assert code == 2, text
        assert re.search(r"(?<!\d)10(?!\d)", text), text
        assert not isinstance(value, pd.DataFrame)
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_historical_hour_is_not_divided_and_aadt_skips_quarters(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "A historical row with hour_complete true is already one hour and "
            "is not divided. An AADT vol is already vehicles per hour and is "
            "one complete hour."
        )
        ids = _Ids()
        historical = _traffic_frame(
            [
                _traffic_row(
                    ids,
                    loc="loc-hist",
                    uhf="101",
                    source="historical",
                    year=2019,
                    month=5,
                    day=1,
                    hour=9,
                    minute=15,
                    vol=40,
                    direction="NB",
                    request="H1",
                    road_segment_id="seg-hist",
                    hour_complete=True,
                )
            ]
        )
        code, text, hours = _quiet_call(capsys, functions["complete_hours"], historical)
        assert code == 0, text
        assert len(hours) == 1, hours
        assert _number(hours.iloc[0]["vol"]) == pytest.approx(40)
        assert _text(hours.iloc[0]["monitoring_location_id"]) == "loc-hist"
        assert _hour(hours.iloc[0]["hour"]) == 9
        aadt = _traffic_frame(
            [
                _traffic_row(
                    ids,
                    loc="loc-aadt",
                    uhf="101",
                    source="aadt",
                    year=2019,
                    month=5,
                    day=2,
                    hour=10,
                    minute=None,
                    vol=96,
                    direction="NB",
                    request="",
                    road_segment_id="seg-aadt",
                    hour_complete=False,
                    ts=_local_stamp(2019, 5, 2, 10, 0),
                )
            ]
        )
        code, text, hours = _quiet_call(capsys, functions["complete_hours"], aadt)
        assert code == 0, text
        assert len(hours) == 1, hours
        assert _number(hours.iloc[0]["vol"]) == pytest.approx(96)
        assert _text(hours.iloc[0]["source"]) == "aadt"
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_location_years_average_days_and_balance_hours(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "location_years averages complete hours to a daily mean, then "
            "averages days. hour_balanced_mean averages available clock-hour "
            "means, then averages the weekday mean and the weekend mean when "
            "both exist. Monday through Friday are weekdays. A location is "
            "eligible only when n_days >= 2 and n_complete_hours >= 1."
        )
        ids = _Ids()
        rows = []
        rows.extend(
            _location_block(
                ids,
                loc="loc-days",
                uhf="101",
                road_segment_id="seg-days",
                days=[
                    (2019, 1, 7, [0], 10),
                    (2019, 1, 8, list(range(23)), 100),
                ],
            )
        )
        rows.extend(
            _location_block(
                ids,
                loc="loc-one-day",
                uhf="101",
                road_segment_id="seg-one-day",
                days=[(2019, 1, 9, list(range(24)), 100)],
            )
        )
        rows.extend(
            _location_block(
                ids,
                loc="loc-short",
                uhf="101",
                road_segment_id="seg-short-hours",
                days=[
                    (2019, 1, 7, list(range(12)), 5),
                    (2019, 1, 8, list(range(11)), 5),
                ],
            )
        )
        rows.extend(
            _location_block(
                ids,
                loc="loc-balance",
                uhf="101",
                road_segment_id="seg-balance",
                days=[
                    (2019, 1, 7, [0, 1], 0),
                    (2019, 1, 8, [0, 1], 0),
                    (2019, 1, 11, [0], 60),
                    (2019, 1, 12, list(range(12)), 40),
                    (2019, 1, 13, list(range(7)), 40),
                ],
            )
        )
        code, text, hours = _quiet_call(
            capsys, functions["complete_hours"], _traffic_frame(rows)
        )
        assert code == 0, text
        code, text, locations = _quiet_call(capsys, functions["location_years"], hours)
        assert code == 0, text
        assert isinstance(locations, pd.DataFrame)
        _assert_columns(locations, LOCATION_COLUMNS)
        days = _eligible_location(locations, "loc-days")
        assert _flag(days["eligible"]) is True
        assert _count(days["n_days"]) == 2
        assert _count(days["n_complete_hours"]) == 24
        assert _count(days["n_sessions"]) == 2
        assert _count(days["weekday_hours"]) == 24
        assert _count(days["weekend_hours"]) == 0
        _close(days["mean_hourly"], 55)
        one_day = _eligible_location(locations, "loc-one-day")
        assert _flag(one_day["eligible"]) is False
        assert _count(one_day["n_days"]) == 1
        assert _count(one_day["n_complete_hours"]) == 24
        short = _eligible_location(locations, "loc-short")
        assert _flag(short["eligible"]) is True
        assert _count(short["n_days"]) == 2
        assert _count(short["n_complete_hours"]) == 23
        balance = _eligible_location(locations, "loc-balance")
        assert _flag(balance["eligible"]) is True
        assert _count(balance["n_days"]) == 5
        assert _count(balance["n_complete_hours"]) == 24
        assert _count(balance["n_sessions"]) == 5
        assert _count(balance["weekday_hours"]) == 5
        assert _count(balance["weekend_hours"]) == 19
        _close(balance["mean_hourly"], 28)
        _close(balance["hour_balanced_mean"], 25)
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_two_locations_on_one_segment_cast_one_vote(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "Two eligible locations on one segment produce one segment row "
            "whose mean is the unweighted mean of the two location means. "
            "An empty road_segment_id uses the monitoring_location_id and "
            "own_segment is true."
        )
        ids = _Ids()
        rows = []
        rows.extend(
            _location_block(
                ids,
                loc="loc-a",
                uhf="101",
                road_segment_id="seg-vote",
                segment="raw-seg-vote",
                days=[
                    (2019, 1, 14, list(range(12)), 10),
                    (2019, 1, 15, list(range(12)), 30),
                ],
            )
        )
        rows.extend(
            _location_block(
                ids,
                loc="loc-b",
                uhf="101",
                road_segment_id="seg-vote",
                segment="raw-seg-vote",
                days=[
                    (2019, 1, 14, list(range(12)), 40),
                    (2019, 1, 15, list(range(12)), 80),
                ],
            )
        )
        rows.extend(
            _location_block(
                ids,
                loc="loc-c",
                uhf="101",
                road_segment_id="seg-vote",
                segment="raw-seg-vote",
                days=[(2019, 1, 16, list(range(24)), 100)],
            )
        )
        rows.extend(
            _location_block(
                ids,
                loc="loc-own",
                uhf="101",
                road_segment_id="",
                segment="raw-decoy",
                days=[
                    (2019, 1, 14, list(range(12)), 7),
                    (2019, 1, 15, list(range(12)), 7),
                ],
            )
        )
        rows.extend(
            _location_block(
                ids,
                loc="loc-blank",
                uhf="101",
                road_segment_id=None,
                segment="raw-decoy",
                days=[
                    (2019, 1, 14, list(range(12)), 9),
                    (2019, 1, 15, list(range(12)), 9),
                ],
            )
        )
        code, text, hours = _quiet_call(
            capsys, functions["complete_hours"], _traffic_frame(rows)
        )
        assert code == 0, text
        code, text, locations = _quiet_call(capsys, functions["location_years"], hours)
        assert code == 0, text
        loc_a = _eligible_location(locations, "loc-a")
        loc_b = _eligible_location(locations, "loc-b")
        loc_c = _eligible_location(locations, "loc-c")
        assert _flag(loc_a["eligible"]) is True
        assert _flag(loc_b["eligible"]) is True
        assert _flag(loc_c["eligible"]) is False
        _close(loc_a["mean_hourly"], 20)
        _close(loc_b["mean_hourly"], 60)
        code, text, segments = _quiet_call(
            capsys, functions["segment_years"], locations
        )
        assert code == 0, text
        assert isinstance(segments, pd.DataFrame)
        _assert_columns(segments, SEGMENT_COLUMNS)
        vote = segments[
            segments["road_segment_id"].map(_text).eq("seg-vote")
            & segments["eligible"].map(_flag)
        ]
        assert len(vote) == 1, segments
        _close(vote.iloc[0]["mean_hourly"], 40)
        _close(vote.iloc[0]["hour_balanced_mean"], 40)
        assert _count(vote.iloc[0]["n_locations"]) == 2
        assert _flag(vote.iloc[0]["own_segment"]) is False
        assert _id_text(vote.iloc[0]["uhf42_id"]) == "101"
        assert _count(vote.iloc[0]["year"]) == 2019
        own = _one(segments, "road_segment_id", "loc-own")
        assert _flag(own["own_segment"]) is True
        assert _flag(own["eligible"]) is True
        _close(own["mean_hourly"], 7)
        assert _count(own["n_locations"]) == 1
        blank = _one(segments, "road_segment_id", "loc-blank")
        assert _flag(blank["own_segment"]) is True
        assert _flag(blank["eligible"]) is True
        _close(blank["mean_hourly"], 9)
        assert "raw-decoy" not in set(segments["road_segment_id"].map(_text))
        assert "raw-seg-vote" not in set(segments["road_segment_id"].map(_text))
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_thresholds_come_from_config(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "Eligibility uses min_observation_dates, min_complete_hours, and "
            "min_segments_per_uhf from the config."
        )
        config = load_config()
        assert int(config["thresholds"]["min_observation_dates"]) == 2
        assert int(config["thresholds"]["min_complete_hours"]) == 1
        assert int(config["thresholds"]["min_segments_per_uhf"]) == 1
        ids = _Ids()
        rows = _location_block(
            ids,
            loc="loc-edge",
            uhf="101",
            road_segment_id="seg-edge",
            days=[
                (2019, 2, 4, list(range(12)), 10),
                (2019, 2, 5, list(range(12)), 10),
            ],
        )
        code, text, hours = _quiet_call(
            capsys, functions["complete_hours"], _traffic_frame(rows)
        )
        assert code == 0, text
        code, text, locations = _quiet_call(capsys, functions["location_years"], hours)
        assert code == 0, text
        edge = _eligible_location(locations, "loc-edge")
        assert _flag(edge["eligible"]) is True
        assert _count(edge["n_complete_hours"]) == 24
        assert _count(edge["n_days"]) == 2

        def _copied_call(edit_old, edit_new, func_name, *args):
            repo = tmp_path / f"cfg-{edit_new.split()[-1]}"
            if repo.exists():
                shutil.rmtree(repo)
            _copy_tree(ROOT / "lib", repo / "lib")
            _copy_tree(ROOT / "config", repo / "config")
            _edit_toml(repo, edit_old, edit_new)
            saved, func = _import_from(repo, monkeypatch, func_name)
            try:
                copied_code, copied_text, copied_value = _quiet_call(capsys, func, *args)
            finally:
                _restore_modules(saved)
            assert copied_code == 0, copied_text
            return copied_value

        stricter_hours = _copied_call(
            "min_complete_hours = 1",
            "min_complete_hours = 25",
            "location_years",
            hours,
        )
        assert _flag(_eligible_location(stricter_hours, "loc-edge")["eligible"]) is False
        stricter_days = _copied_call(
            "min_observation_dates = 2",
            "min_observation_dates = 3",
            "location_years",
            hours,
        )
        assert _flag(_eligible_location(stricter_days, "loc-edge")["eligible"]) is False
        segments = _segment_frame(
            [
                _segment_row(f"seg-{index}", "101", 2019, 10, 10)
                for index in range(5)
            ]
        )
        periods = [_period("2019", [2019])]
        code, text, neighborhoods = _quiet_call(
            capsys, functions["neighborhood_periods"], segments, periods
        )
        assert code == 0, text
        frame = _frame_from_return(neighborhoods, "traffic_mean_vehicles_per_hour")
        _assert_columns(frame, UHF_COLUMNS)
        valid = _valid_rows(frame, "2019")
        assert len(valid) == 1
        _close(valid[0]["traffic_mean_vehicles_per_hour"], 10)
        widened = _copied_call(
            "min_segments_per_uhf = 1",
            "min_segments_per_uhf = 6",
            "neighborhood_periods",
            segments,
            periods,
        )
        widened_frame = _frame_from_return(widened, "traffic_mean_vehicles_per_hour")
        assert _valid_rows(widened_frame, "2019") == []
        _assert_no_local_writes(tmp_path)
        assert calls == []


def test_neighborhood_mean_is_unweighted_and_pool_weights_years(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        functions = _require_functions(
            "traffic_mean_vehicles_per_hour is the unweighted mean of eligible "
            "segment means. A pooled value is the unweighted mean of the year "
            "means, and a neighborhood is eligible only when every constituent "
            "year is eligible. traffic_mean_hour_balanced is not that primary mean."
        )
        rows = [
            _segment_row("seg-10", "101", 2019, 10, 1),
            _segment_row("seg-20", "101", 2019, 20, 3),
            _segment_row("seg-30", "101", 2019, 30, 5),
            _segment_row("seg-40", "101", 2019, 40, 7),
            _segment_row("seg-50", "101", 2019, 50, 9),
            _segment_row("seg-out", "101", 2019, 999, 999, eligible=False),
            _segment_row("seg-old", "101", 2014, 1000, 1000),
        ]
        rows.extend(_segment_row(f"seg-low-{index}", "102", 2019, 8, 8) for index in range(4))
        rows.extend(_segment_row(f"seg-2015-{index}", "201", 2015, 10, 2) for index in range(5))
        rows.extend(_segment_row(f"seg-2016-{index}", "201", 2016, 40, 8) for index in range(9))
        rows.extend(_segment_row(f"seg-miss-{index}", "202", 2015, 99, 99) for index in range(5))
        periods = [
            _period("SKIP", [2014], included=False, reason="excluded"),
            _period("2019", [2019]),
            _period("2015-2016", [2015, 2016]),
        ]
        code, text, value = _quiet_call(
            capsys,
            functions["neighborhood_periods"],
            _segment_frame(rows),
            periods,
        )
        assert code == 0, text
        frame = _frame_from_return(value, "traffic_mean_vehicles_per_hour")
        _assert_columns(frame, UHF_COLUMNS)
        _assert_invalid_means_are_null(frame)
        year_rows = _valid_rows(frame, "2019")
        by_uhf = {_id_text(row["uhf42_id"]): row for row in year_rows}
        assert set(by_uhf) == {"101", "102"}
        _close(by_uhf["101"]["traffic_mean_vehicles_per_hour"], 30)
        _close(by_uhf["101"]["traffic_mean_hour_balanced"], 5)
        _close(by_uhf["101"]["traffic_median_vehicles_per_hour"], 30)
        assert _count(by_uhf["101"]["n_road_segments"]) == 5
        assert _text(by_uhf["101"]["traffic_source"]) == "automated"
        _close(by_uhf["102"]["traffic_mean_vehicles_per_hour"], 8)
        _close(by_uhf["102"]["traffic_mean_hour_balanced"], 8)
        assert _count(by_uhf["102"]["n_road_segments"]) == 4
        assert _valid_rows(frame, "SKIP") == []
        pooled = _valid_rows(frame, "2015-2016")
        assert [(_id_text(row["uhf42_id"])) for row in pooled] == ["201"]
        _close(pooled[0]["traffic_mean_vehicles_per_hour"], 25)
        _close(pooled[0]["traffic_mean_hour_balanced"], 5)
        assert _text(pooled[0]["traffic_source"]) == "automated"
        _assert_no_local_writes(tmp_path)
        assert calls == []


def _script_success_traffic():
    ids = _Ids()
    rows = []
    for index in range(5):
        rows.extend(
            _location_block(
                ids,
                loc=f"loc-101-{index}",
                uhf="101",
                road_segment_id=f"seg-101-{index}",
                segment=f"seg-101-{index}",
                days=_pattern_days(2019, (10, 100)),
            )
        )
    volumes = [1, 1, 1, 1, 1, 1, 22]
    for index, volume in enumerate(volumes):
        rows.extend(
            _location_block(
                ids,
                loc=f"loc-102-{index}",
                uhf="102",
                road_segment_id=f"seg-102-{index}",
                segment=f"seg-102-{index}",
                days=_uniform_days(2019, volume),
            )
        )
    for index in range(4):
        rows.extend(
            _location_block(
                ids,
                loc=f"loc-401-{index}",
                uhf="401",
                road_segment_id=f"seg-401-{index}",
                segment=f"seg-401-{index}",
                days=_uniform_days(2018, 5),
            )
        )
    return _traffic_frame(rows)


def _script_pool_traffic():
    ids = _Ids()
    rows = []
    for index in range(5):
        rows.extend(
            _location_block(
                ids,
                loc=f"loc-301-{index}",
                uhf="301",
                road_segment_id=f"seg-301-{index}",
                segment=f"seg-301-{index}",
                days=_pattern_days(2015, (10, 100)),
            )
        )
    for index in range(9):
        rows.extend(
            _location_block(
                ids,
                loc=f"loc-301-{index}",
                uhf="301",
                road_segment_id=f"seg-301-{index}",
                segment=f"seg-301-{index}",
                days=_uniform_days(2016, 40),
            )
        )
    for index in range(5):
        rows.extend(
            _location_block(
                ids,
                loc=f"loc-302-{index}",
                uhf="302",
                road_segment_id=f"seg-302-{index}",
                segment=f"seg-302-{index}",
                days=_uniform_days(2015, 99),
            )
        )
    return _traffic_frame(rows)


def _script_zero_traffic():
    ids = _Ids()
    rows = []
    for index in range(4):
        rows.extend(
            _location_block(
                ids,
                loc=f"loc-401-{index}",
                uhf="401",
                road_segment_id=f"seg-401-{index}",
                segment=f"seg-401-{index}",
                days=[(2018, 1, 8, list(range(24)), 5)],
            )
        )
    return _traffic_frame(rows)


def _read_uhf(repo):
    frame = pd.read_csv(repo / UHF_PATH)
    _assert_columns(frame, UHF_COLUMNS)
    return frame


def test_script_prints_eligible_count_and_median(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _functions, script = _require_script(
            "The script prints '{period} eligible {n} median {m}' and exits 0. "
            "The neighborhood mean equals the unweighted mean of the eligible "
            "segment means."
        )
        periods = [
            _period("2019", [2019]),
            _period("2018", [2018]),
            _period("1999", [1999], included=False, reason="dropped"),
        ]
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            _script_success_traffic(),
            periods,
        )
        assert code == 0, text
        _assert_period_lines(
            text,
            [("2019", 2, 6), ("2018", 1, 4)],
        )
        assert "1999" not in {item[0] for item in _period_lines(text)}
        locations, segments = _assert_success_files(repo)
        loc = _eligible_location(locations, "loc-101-0")
        assert _flag(loc["eligible"]) is True
        assert _count(loc["n_days"]) == 2
        assert _count(loc["n_complete_hours"]) == 24
        assert _count(loc["weekday_hours"]) == 24
        assert _count(loc["weekend_hours"]) == 0
        _close(loc["mean_hourly"], 55)
        _close(loc["hour_balanced_mean"], 32.5)
        vote = segments[
            segments["road_segment_id"].map(_text).eq("seg-101-0")
            & segments["year"].map(_count).eq(2019)
        ]
        assert len(vote) == 1, segments
        _close(vote.iloc[0]["mean_hourly"], 55)
        _close(vote.iloc[0]["hour_balanced_mean"], 32.5)
        assert _flag(vote.iloc[0]["own_segment"]) is False
        assert _count(vote.iloc[0]["n_locations"]) == 1
        uhf = _read_uhf(repo)
        _assert_invalid_means_are_null(uhf)
        valid = _valid_rows(uhf, "2019")
        assert len(valid) == 2
        by_uhf = {_id_text(row["uhf42_id"]): row for row in valid}
        _close(by_uhf["101"]["traffic_mean_vehicles_per_hour"], 55)
        _close(by_uhf["101"]["traffic_mean_hour_balanced"], 32.5)
        _close(by_uhf["101"]["traffic_median_vehicles_per_hour"], 55)
        assert _count(by_uhf["101"]["n_road_segments"]) == 5
        assert _text(by_uhf["101"]["traffic_source"]) == "automated"
        _close(by_uhf["102"]["traffic_mean_vehicles_per_hour"], 4)
        _close(by_uhf["102"]["traffic_mean_hour_balanced"], 4)
        _close(by_uhf["102"]["traffic_median_vehicles_per_hour"], 1)
        assert _count(by_uhf["102"]["n_road_segments"]) == 7
        valid_2018 = _valid_rows(uhf, "2018")
        assert len(valid_2018) == 1
        assert _id_text(valid_2018[0]["uhf42_id"]) == "401"
        assert _count(valid_2018[0]["n_road_segments"]) == 4
        _assert_coverage(repo, "2019", "101", 5, 5, True)
        _assert_coverage(repo, "2019", "102", 7, 7, True)
        _assert_coverage(repo, "2018", "401", 4, 4, True)


def test_pooled_script_value_is_the_unweighted_mean_of_year_means(
    monkeypatch, capsys, tmp_path
):
    with _guard_repo():
        _functions, script = _require_script(
            "A pooled value equals the unweighted mean of the year means."
        )
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            _script_pool_traffic(),
            [_period("2015-2016", [2015, 2016])],
        )
        assert code == 0, text
        locations, _segments = _assert_success_files(repo)
        first_year = locations[
            locations["monitoring_location_id"].map(_text).eq("loc-301-0")
            & locations["year"].map(_count).eq(2015)
        ]
        second_year = locations[
            locations["monitoring_location_id"].map(_text).eq("loc-301-0")
            & locations["year"].map(_count).eq(2016)
        ]
        assert len(first_year) == 1, locations
        assert len(second_year) == 1, locations
        _close(first_year.iloc[0]["mean_hourly"], 55)
        _close(first_year.iloc[0]["hour_balanced_mean"], 32.5)
        _close(second_year.iloc[0]["mean_hourly"], 40)
        _close(second_year.iloc[0]["hour_balanced_mean"], 40)
        uhf = _read_uhf(repo)
        _assert_invalid_means_are_null(uhf)
        valid = _valid_rows(uhf, "2015-2016")
        assert len(valid) == 1
        assert _id_text(valid[0]["uhf42_id"]) == "301"
        _close(valid[0]["traffic_mean_vehicles_per_hour"], 47.5)
        _close(valid[0]["traffic_mean_hour_balanced"], 36.25)
        assert _text(valid[0]["traffic_source"]) == "automated"
        found = _period_lines(text)
        assert [item[0] for item in found] == ["2015-2016"], text
        assert int(found[0][1]) == 1
        assert found[0][2] != "none"
        assert float(found[0][2]) == pytest.approx(_number(valid[0]["n_road_segments"]))
        matched = _coverage_rows(repo, "2015-2016", "301")
        for _, row in matched.iterrows():
            assert _as_bool(row["adequate"]) is True
            assert _text(row["source"]) == "automated"
            assert _count(row["n_eligible_segments"]) == _count(
                valid[0]["n_road_segments"]
            )
        inadequate = _coverage_rows(repo, "2015-2016", "302")
        for _, row in inadequate.iterrows():
            assert _as_bool(row["adequate"]) is False


def test_zero_eligible_neighborhoods_exit_0_and_write_coverage(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _functions, script = _require_script(
            "A period with zero eligible neighborhoods exits 0 and still writes coverage."
        )
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            _script_zero_traffic(),
            [_period("2018", [2018])],
        )
        assert code == 0, text
        _assert_period_lines(text, [("2018", 0, None)])
        _assert_success_files(repo)
        uhf = _read_uhf(repo)
        assert _valid_rows(uhf, "2018") == []
        _assert_invalid_means_are_null(uhf)
        _assert_coverage(repo, "2018", "401", 0, 0, False)


def test_script_three_directions_sum_and_exit_0(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _functions, script = _require_script(
            "A location with three directions does not stop aggregation. "
            "An hour is kept only when all three directions are complete, "
            "and the location mean is the sum of those directions."
        )
        ids = _Ids()
        rows = []
        for day in (3, 4):
            for direction, volumes in (
                ("NB", (1, 2, 3, 4)),
                ("SB", (1, 1, 1, 1)),
                ("EB", (2, 2, 2, 2)),
            ):
                rows.extend(
                    _quarter_rows(
                        ids,
                        loc="loc-three",
                        uhf="306",
                        year=2023,
                        month=10,
                        day=day,
                        hour=8,
                        volumes=volumes,
                        direction=direction,
                        request="36597",
                        road_segment_id="33662",
                    )
                )
        for direction, volumes in (
            ("NB", (9, 9, 9, 9)),
            ("SB", (9, 9, 9, 9)),
        ):
            rows.extend(
                _quarter_rows(
                    ids,
                    loc="loc-three",
                    uhf="306",
                    year=2023,
                    month=10,
                    day=3,
                    hour=9,
                    volumes=volumes,
                    direction=direction,
                    request="36597",
                    road_segment_id="33662",
                )
            )
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            _traffic_frame(rows),
            [_period("2023", [2023])],
        )
        assert code == 0, text
        assert "loc-three" not in text
        _assert_period_lines(text, [("2023", 1, 1)])
        locations, segments = _assert_success_files(repo)
        location = _one(locations, "monitoring_location_id", "loc-three")
        assert _flag(location["eligible"]) is True
        _close(location["mean_hourly"], 22)
        assert _count(location["n_complete_hours"]) == 2
        assert _count(location["n_days"]) == 2
        segment = _one(segments, "road_segment_id", "33662")
        _close(segment["mean_hourly"], 22)
        assert _flag(segment["eligible"]) is True
        uhf = _read_uhf(repo)
        valid = _valid_rows(uhf, "2023")
        assert len(valid) == 1
        assert _id_text(valid[0]["uhf42_id"]) == "306"
        _close(valid[0]["traffic_mean_vehicles_per_hour"], 22)


def test_script_empty_direction_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _functions, script = _require_script(
            "An empty direction exits 2 and the message contains the location id."
        )
        ids = _Ids()
        rows = _quarter_rows(
            ids,
            loc="loc-empty",
            uhf="101",
            year=2019,
            month=4,
            day=6,
            hour=8,
            volumes=(1, 2, 3, 4),
            direction="",
            request="R1",
            road_segment_id="seg-empty",
        )
        code, text, _repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            _traffic_frame(rows),
            [_period("2019", [2019])],
        )
        assert code == 2, text
        assert "loc-empty" in text


def test_script_bad_minute_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _functions, script = _require_script(
            "A failure to build an hour from the audited quarters exits 2 "
            "and the message contains that minute."
        )
        ids = _Ids()
        rows = _quarter_rows(
            ids,
            loc="loc-minute",
            uhf="101",
            year=2019,
            month=3,
            day=6,
            hour=8,
            volumes=(1, 2, 3, 4),
            minutes=(0, 15, 30, 10),
            request="R1",
            road_segment_id="seg-minute",
        )
        code, text, _repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            _traffic_frame(rows),
            [_period("2019", [2019])],
        )
        assert code == 2, text
        assert re.search(r"(?<!\d)10(?!\d)", text), text
