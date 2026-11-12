"""Contract tests for cleaning asthma rates and traffic counts.

The clean modules and script are absent in this worktree. These tests lock
the suppressed-rate rule, the pooled period, and the traffic hour rules.
"""

import copy
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
FIXTURES = ROOT / "tests" / "fixtures" / "clean"
SCHEMA_PATH = FIXTURES / "schema_snapshot.json"
SCRIPT_NAME = "scripts/04_clean.py"
ASTHMA_COLUMNS = (
    "uhf42_id",
    "uhf42_name",
    "borough",
    "period",
    "period_kind",
    "constituent_years",
    "asthma_ed_count",
    "asthma_ed_rate_per_10000",
)
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
)
EXCLUSION_COLUMNS = ("stage", "key", "reason", "source")
WKT = "POINT(-74.01 40.71)"
HIST_WKT = "POINT(-73.95 40.80)"
AADT_WKT = "POINT(-73.90 40.70)"
ENDASH = "\u2013"
SUCCESS_PERIODS = {
    "2019": 1,
    "2015-2017": 1,
    "2015 to 2016": 1,
    "2018": 1,
}
SPIKE_ID = 19
NEGATIVE_ID = 17
ZERO_ID = 16
DUP_ID = 18
COVERAGE_ID = 888881


def _require(reason):
    missing = []
    clean_asthma = clean_traffic = None
    try:
        from lib.clean_asthma import clean_asthma
    except ImportError:
        missing.append("lib/clean_asthma.py clean_asthma")
    try:
        from lib.clean_traffic import clean_traffic
    except ImportError:
        missing.append("lib/clean_traffic.py clean_traffic")
    script = ROOT / SCRIPT_NAME
    if not script.is_file():
        missing.append(
            "scripts/04_clean.py (python scripts/04_clean.py takes no arguments)"
        )
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    assert callable(clean_asthma)
    assert callable(clean_traffic)
    return clean_asthma, clean_traffic, script


def _token(text, token):
    return (
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(str(token))}(?![A-Za-z0-9_])",
            text or "",
        )
        is not None
    )


def _is_null(value):
    if value is None:
        return True
    try:
        if bool(pd.isna(value)):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and value.strip().lower() in {"", "nan", "none", "null", "<na>", "nat"}:
        return True
    return False


def _number(value):
    assert not isinstance(value, bool), value
    assert not isinstance(value, str), f"expected a number, got {value!r}"
    assert not _is_null(value), value
    return float(value)


def _text(value):
    assert not _is_null(value), value
    assert not isinstance(value, bool), value
    if isinstance(value, (int, np.integer, float, np.floating)):
        raise AssertionError(f"expected a string, got {value!r}")
    return str(value).strip()


def _flag(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, np.bool_):
        return bool(value)
    raise AssertionError(f"expected a bool, got {value!r}")


def _identity(value):
    assert not _is_null(value)
    return int(float(value))


def _lines(text):
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def _table(path):
    return pd.read_csv(path, dtype=str)


def _write_table(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _selection(periods):
    return {
        "scenario": "A",
        "mix_used": False,
        "comparability_pass": None,
        "comparability_r": None,
        "comparability_n": None,
        "periods": periods,
    }


def _period(period, source, years, included=True, reason=""):
    return {
        "period": period,
        "source": source,
        "years": [int(year) for year in years],
        "included": bool(included),
        "reason": reason,
    }


def _asthma_row(**overrides):
    row = {
        "TimePeriod": "2019",
        "GeoID": "101",
        "uhf42_name": "Heights",
        "borough": "Manhattan",
        "asthma_ed_count": "1",
        "asthma_ed_rate_per_10000": "1",
    }
    row.update(overrides)
    return row


def _automated_row(
    identity,
    *,
    minute=0,
    vol=1,
    direction="NB",
    boro="Manhattan",
    hour=8,
    day=15,
    month=1,
    year=2019,
    segment="seg-a",
    wkt=WKT,
    request=None,
):
    return {
        ":id": int(identity),
        "yr": str(year),
        "m": str(month),
        "d": str(day),
        "hh": str(hour),
        "mm": str(minute),
        "vol": str(vol),
        "segmentid": segment,
        "wktgeom": wkt,
        "direction": direction,
        "boro": boro,
        "requestid": f"req-{identity}" if request is None else request,
    }


def _historical_row(
    identity,
    volume,
    *,
    direction="NB",
    hour=9,
    borough="Manhattan",
    year=2015,
    month=6,
    day=1,
    minute=None,
    include_minute=False,
):
    row = {
        ":id": int(identity),
        "year": int(year),
        "month": int(month),
        "day": int(day),
        "hour": int(hour),
        "volume": int(volume),
        "segment": "seg-h",
        "wkt": HIST_WKT,
        "direction": direction,
        "borough": borough,
    }
    if include_minute:
        row["minute"] = minute
    return row


def _aadt_row(identity=41, aadt=240, vol=999, *, year=2022):
    return {
        ":id": int(identity),
        "year": str(year),
        "month": "4",
        "day": "5",
        "hour": "12",
        "vol": str(vol),
        "aadt": str(aadt),
        "segmentid": "seg-adt",
        "wktgeom": AADT_WKT,
        "direction": "North",
        "borough": "queens",
        "requestid": "req-adt",
    }


def _frame(result):
    if isinstance(result, tuple) and result:
        result = result[0]
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, list):
        return pd.DataFrame(result)
    raise AssertionError(f"expected cleaned rows, got {type(result).__name__}")


def _rows(result, columns, *, exact=False):
    frame = _frame(result)
    names = [str(column).strip() for column in frame.columns]
    if exact:
        assert tuple(names) == tuple(columns), names
    else:
        missing = [column for column in columns if column not in names]
        assert not missing, f"missing {missing}; columns are {names}"
    copy_frame = frame.copy()
    copy_frame.columns = names
    return copy_frame.to_dict(orient="records")


def _asthma_map(result):
    records = _rows(result, ASTHMA_COLUMNS, exact=True)
    got = {}
    for record in records:
        key = (_text(record["uhf42_id"]), _text(record["period"]))
        assert key not in got, "one asthma row per neighborhood-period"
        got[key] = record
    return got


def _as_float(value, *, typed):
    if not typed and isinstance(value, str):
        return float(value.strip())
    return _number(value)


def _assert_asthma(record, *, typed=True, **expected):
    assert _text(record["uhf42_name"]) == expected["name"]
    assert _text(record["borough"]) == expected["borough"]
    assert _text(record["period_kind"]) == expected["kind"]
    assert _text(record["constituent_years"]) == expected["years"]
    if expected["count"] is None:
        assert _is_null(record["asthma_ed_count"]), "a suppressed count stays null"
    else:
        assert _as_float(record["asthma_ed_count"], typed=typed) == pytest.approx(
            expected["count"]
        ), "0 stays 0" if expected["count"] == 0 else "the count is parsed as a number"
    if expected["rate"] is None:
        assert _is_null(record["asthma_ed_rate_per_10000"]), "a suppressed rate stays null"
    else:
        assert _as_float(record["asthma_ed_rate_per_10000"], typed=typed) == pytest.approx(
            expected["rate"]
        ), "0 stays 0" if expected["rate"] == 0 else "the rate is parsed as a number"


def _by_id(records):
    found = {}
    for record in records:
        identity = _identity(record[":id"])
        assert identity not in found, identity
        found[identity] = record
    return found


def _record(records, identity, *, source, year):
    matches = [
        record
        for record in records
        if _identity(record[":id"]) == int(identity)
        and _text(record["source"]) == source
        and int(_number(record["year"])) == int(year)
    ]
    assert len(matches) == 1, (source, year, identity)
    return matches[0]


def _assert_local(value, year, month, day, hour, minute):
    assert not _is_null(value), "ts_local is America/New_York civil time"
    stamp = pd.Timestamp(value)
    assert stamp.tzinfo is not None, "ts_local is America/New_York civil time"
    expected = pd.Timestamp(
        year=int(year),
        month=int(month),
        day=int(day),
        hour=int(hour),
        minute=int(minute),
        tz="America/New_York",
    )
    assert stamp.tz_convert("UTC") == expected.tz_convert("UTC")


def _source_vol(record, source):
    if source == "aadt":
        return float(record["aadt"]) / 24.0
    if source == "historical":
        return float(record["volume"])
    return float(record["vol"])


def _source_year(record, source):
    if source == "automated":
        return int(record["yr"])
    return int(record["year"])


def _kept_records(frames):
    kept = []
    for (source, _year), frame in frames.items():
        body = frame.drop_duplicates(keep="first")
        for record in body.to_dict(orient="records"):
            volume = _source_vol(record, source)
            if volume < 0:
                continue
            kept.append(
                {
                    "id": int(record[":id"]),
                    "source": source,
                    "year": _source_year(record, source),
                    "vol": volume,
                }
            )
    return kept


def _expected_flag_ids(frames):
    grouped = {}
    for record in _kept_records(frames):
        grouped.setdefault(record["year"], []).append(record)
    flagged = set()
    for records in grouped.values():
        threshold = float(np.quantile([record["vol"] for record in records], 0.999))
        for record in records:
            if record["vol"] > threshold:
                flagged.add(record["id"])
    return flagged


def _removed_duplicates(frames):
    return int(sum(int(frame.duplicated(keep="first").sum()) for frame in frames.values()))


def _success_frames():
    automated = [
        _automated_row(11, minute=0, vol=1, direction="NB", boro="manhattan", hour=8),
        _automated_row(12, minute=15, vol=1, direction="SB"),
        _automated_row(13, minute=30, vol=1, direction="1"),
        _automated_row(14, minute=45, vol=1, direction="North"),
        _automated_row(15, minute=0, vol=1, direction=" NB "),
        _automated_row(ZERO_ID, minute=0, vol=0, direction="NB"),
        _automated_row(NEGATIVE_ID, minute=0, vol=-3, direction="NB"),
    ]
    duplicate = _automated_row(DUP_ID, minute=0, vol=1, direction="NB")
    automated.extend(copy.deepcopy(duplicate) for _ in range(3))
    automated.append(_automated_row(SPIKE_ID, minute=0, vol=50, direction="NB"))
    for identity in range(100, 200):
        automated.append(_automated_row(identity, minute=0, vol=1, direction="NB"))
    historical = [
        _historical_row(31, 48, direction="NB", borough="bronx", hour=9),
        _historical_row(32, 48, direction="SB", borough="Manhattan", hour=10),
    ]
    historical.extend(
        _historical_row(identity, 200, direction="NB", hour=11)
        for identity in range(33, 53)
    )
    return {
        ("automated", 2019): pd.DataFrame(automated),
        ("historical", 2015): pd.DataFrame(historical),
        ("aadt", 2022): pd.DataFrame([_aadt_row()]),
    }


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
        assert not (FIXTURES / "outputs").exists()
        assert not (FIXTURES / "data").exists()


def _forbid_network(monkeypatch):
    calls = []

    def send(self, prepared, **kwargs):
        calls.append(getattr(prepared, "url", ""))
        raise AssertionError(f"fixture test must not call Socrata ({prepared.url})")

    def urlopen(*args, **kwargs):
        calls.append(args[0] if args else "")
        raise AssertionError("fixture test must not call Socrata")

    monkeypatch.setattr("requests.sessions.Session.send", send)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


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


def _watch_reads(monkeypatch):
    import pyarrow.parquet as pq

    seen = []
    original_read = pd.read_parquet
    original_table = pq.read_table

    def spy_read(path, *args, **kwargs):
        seen.append(path)
        return original_read(path, *args, **kwargs)

    def spy_table(source, *args, **kwargs):
        if isinstance(source, (str, Path)):
            seen.append(source)
        return original_table(source, *args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", spy_read)
    monkeypatch.setattr(pq, "read_table", spy_table)
    return seen


def _traffic_reads(seen):
    found = []
    for path in seen:
        text = str(path).replace("\\", "/")
        if "data/raw/traffic" in text:
            found.append(text)
    return found


def _write_parquet(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _year_dir(repo, source, year):
    return repo / "data" / "raw" / "traffic" / source / f"year={int(year)}"


def _prepare(tmp_path, script_src, asthma, selection, frames, schema, plant_coverage, omit_records):
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    script = repo / "scripts" / "04_clean.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_src, script)
    audit = repo / "outputs" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "source_selection.json").write_text(
        json.dumps(selection, indent=2) + "\n",
        encoding="utf-8",
    )
    (audit / "schema_snapshot.json").write_text(
        json.dumps(schema, indent=2) + "\n",
        encoding="utf-8",
    )
    export = repo / "data" / "raw" / "asthma" / "export.csv"
    export.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(asthma, Path):
        shutil.copy2(asthma, export)
    else:
        _write_table(export, asthma)
    for (source, year), frame in frames.items():
        if (source, int(year)) in omit_records:
            continue
        _write_parquet(_year_dir(repo, source, year) / "records.parquet", frame)
    for source, year in plant_coverage:
        _write_parquet(
            _year_dir(repo, source, year) / "coverage.parquet",
            pd.DataFrame([_automated_row(COVERAGE_ID, vol=123456, year=year)]),
        )
    return repo, script


def _run_script(
    monkeypatch,
    capsys,
    tmp_path,
    script_src,
    asthma,
    selection,
    frames,
    schema=None,
    plant_coverage=(),
    omit_records=(),
):
    schema = _load_schema() if schema is None else schema
    root_toml = (ROOT / "config" / "pipeline.toml").read_bytes()
    repo, script = _prepare(
        tmp_path,
        script_src,
        asthma,
        selection,
        frames,
        schema,
        plant_coverage,
        {(source, int(year)) for source, year in omit_records},
    )
    project_toml = (repo / "config" / "pipeline.toml").read_bytes()
    saved = _pop_lib_modules()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "path", _copy_path(repo))
    monkeypatch.setattr(sys, "argv", [str(script)])
    calls = _forbid_network(monkeypatch)
    seen = _watch_reads(monkeypatch)
    try:
        assert sys.argv == [str(script)], "python scripts/04_clean.py takes no arguments"
        code = _invoke(script)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert (repo / "config" / "pipeline.toml").read_bytes() == project_toml
        assert (ROOT / "config" / "pipeline.toml").read_bytes() == root_toml
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()
        assert calls == [], "fixture test must not call Socrata"
        return code, text, repo, seen
    finally:
        _restore_modules(saved)


def _assert_exit(code, text, message=""):
    combined = f"{text}{message}"
    assert code == 2, combined
    return combined


def _minimal_frames(source="automated", year=2019, **overrides):
    if source == "historical":
        row = _historical_row(7, 6, year=year, include_minute=False)
    elif source == "aadt":
        row = _aadt_row(7, year=year)
    else:
        row = _automated_row(7, year=year, minute=0, vol=3, direction="NB")
    row.update(overrides)
    return {(source, int(year)): pd.DataFrame([row])}


def _coverage_schema(historical_minute=False):
    schema = _load_schema()
    if historical_minute:
        schema["historical"]["columns"] = list(schema["historical"]["columns"]) + ["minute"]
        schema["historical"]["sample"] = dict(schema["historical"]["sample"])
        schema["historical"]["sample"]["minute"] = 0
    return schema


def _assert_period_lines(text, periods, duplicates, flagged):
    lines = _lines(text)
    for period, count in periods.items():
        assert lines.count(f"{period} {count}") == 1, (
            f"terminal should print {period} {count}"
        )
    assert lines.count(f"duplicates removed {duplicates}") == 1, text
    assert lines.count(f"invalid counts flagged {flagged}") == 1, text
    return lines


def _exclusions(repo):
    path = repo / "outputs" / "audit" / "exclusions_clean.csv"
    assert path.is_file(), "outputs/audit/exclusions_clean.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or []) == EXCLUSION_COLUMNS
        return list(reader)


def _assert_listed(records, identity, source):
    matches = [
        record
        for record in records
        if _token(record["key"], identity) and record["source"].strip() == source
    ]
    assert matches, f"{identity} should be listed in exclusions_clean.csv"
    for record in matches:
        assert record["stage"].strip() == "clean"
        assert record["reason"].strip()
    return matches


def _one_exclusion(records, identity, reason, source="automated"):
    matches = [
        record
        for record in records
        if record["key"].strip() == str(identity)
        and record["reason"].strip() == reason
        and record["source"].strip() == source
    ]
    assert len(matches) == 1, (identity, reason, records)
    assert matches[0]["stage"].strip() == "clean"
    assert matches[0]["key"].strip() == str(identity)
    return matches[0]


def _outputs(repo):
    asthma = repo / "outputs" / "data" / "cleaned_asthma.csv"
    traffic = repo / "outputs" / "data" / "cleaned_traffic.parquet"
    log = repo / "outputs" / "cleaning_log.md"
    exclusions = repo / "outputs" / "audit" / "exclusions_clean.csv"
    for path in (asthma, traffic, log, exclusions):
        assert path.is_file(), path
    return asthma, traffic, log


def _quiet_call(capsys, func, *args):
    monkey_calls = []
    code, message, value = _call(func, *args)
    captured = capsys.readouterr()
    text = f"{captured.out}{captured.err}{message}"
    return code, text, value, monkey_calls


def test_suppressed_rate_and_count_stay_null(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        clean_asthma, _clean_traffic, _script = _require(
            "A suppressed asthma_ed_rate_per_10000 stays null, "
            "the same token on asthma_ed_count stays null, and 0 stays 0."
        )
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        frame = _table(FIXTURES / "suppressed" / "export.csv")
        code, text, value, _unused = _quiet_call(capsys, clean_asthma, frame)
        assert code == 0, text
        got = _asthma_map(value)
        assert len(got) == 7, "an age or measure column does not drop a row"
        _assert_asthma(
            got[("101", "2019")],
            name="Washington Heights",
            borough="Manhattan",
            kind="year",
            years="2019",
            count=None,
            rate=None,
        )
        _assert_asthma(
            got[("101", "2018")],
            name="Washington Heights",
            borough="Manhattan",
            kind="year",
            years="2018",
            count=0,
            rate=0,
        )
        _assert_asthma(
            got[("102", "2022")],
            name="Harlem",
            borough="Bronx",
            kind="year",
            years="2022",
            count=None,
            rate=None,
        )
        _assert_asthma(
            got[("103", "2023")],
            name="Astoria",
            borough="Queens",
            kind="year",
            years="2023",
            count=None,
            rate=None,
        )
        _assert_asthma(
            got[("104", "2016")],
            name="Bay",
            borough="Brooklyn",
            kind="year",
            years="2016",
            count=None,
            rate=None,
        )
        _assert_asthma(
            got[("105", "2015")],
            name="Shore",
            borough="Staten Island",
            kind="year",
            years="2015",
            count=0,
            rate=None,
        )
        _assert_asthma(
            got[("106", "2017")],
            name="Park",
            borough="Manhattan",
            kind="year",
            years="2017",
            count=None,
            rate=12.5,
        )
        assert calls == [], "fixture test must not call Socrata"
        assert not (tmp_path / "outputs").exists(), (
            "clean_asthma on a fixture must not write outputs/"
        )
        assert not (tmp_path / "data").exists()


def test_pooled_time_period_stays_one_period(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        clean_asthma, _clean_traffic, _script = _require(
            "A pooled TimePeriod stays one period."
        )
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        frame = _table(FIXTURES / "pooled" / "export.csv")
        code, text, value, _unused = _quiet_call(capsys, clean_asthma, frame)
        assert code == 0, text
        got = _asthma_map(value)
        assert len(got) == 5, "a pooled TimePeriod stays one period"
        pooled = {key for key in got if key[1] == "2015-2017"}
        assert pooled == {("201", "2015-2017"), ("202", "2015-2017")}
        _assert_asthma(
            got[("201", "2015-2017")],
            name="Harlem",
            borough="Manhattan",
            kind="pooled",
            years="2015,2016,2017",
            count=8,
            rate=9,
        )
        _assert_asthma(
            got[("202", "2015-2017")],
            name="Harlem East",
            borough="Manhattan",
            kind="pooled",
            years="2015,2016,2017",
            count=1,
            rate=2,
        )
        _assert_asthma(
            got[("301", "2015 to 2016")],
            name="Mott Haven",
            borough="Bronx",
            kind="pooled",
            years="2015,2016",
            count=3,
            rate=4,
        )
        dash = f"2022{ENDASH}2023"
        _assert_asthma(
            got[("302", dash)],
            name="Bedford",
            borough="Brooklyn",
            kind="pooled",
            years="2022,2023",
            count=5,
            rate=6,
        )
        _assert_asthma(
            got[("401", "2019")],
            name="Astoria",
            borough="Queens",
            kind="year",
            years="2019",
            count=7,
            rate=8,
        )
        periods = {period for _uhf, period in got}
        assert periods.isdisjoint(
            {"2015", "2016", "2017", "2020", "2021", "2014", "Winter", "2019-2022", "2015-2016-2017"}
        )
        assert calls == [], "fixture test must not call Socrata"
        assert not (tmp_path / "outputs").exists(), (
            "clean_asthma on a fixture must not write outputs/"
        )
        assert not (tmp_path / "data").exists()


def test_overlapping_periods_exit_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        clean_asthma, _clean_traffic, script = _require(
            "Overlapping periods for one neighborhood exit 2 and name the id."
        )
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        frame = _table(FIXTURES / "overlap" / "export.csv")
        code, text, _value, _unused = _quiet_call(capsys, clean_asthma, frame)
        text = _assert_exit(code, text)
        assert _token(text, 101)
        assert "2015-2017" in text
        assert _token(text, 2016)
        assert not (tmp_path / "outputs").exists()
        code, text, _repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            FIXTURES / "overlap" / "export.csv",
            _selection([_period("2019", "automated", [2019])]),
            _minimal_frames(),
        )
        text = _assert_exit(code, text)
        assert _token(text, 101)
        assert "2015-2017" in text
        assert _token(text, 2016)


def test_duplicate_neighborhood_period_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        clean_asthma, _clean_traffic, script = _require(
            "A duplicate neighborhood-period exits 2 and names the key."
        )
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        frame = _table(FIXTURES / "duplicate" / "export.csv")
        code, text, _value, _unused = _quiet_call(capsys, clean_asthma, frame)
        text = _assert_exit(code, text)
        assert _token(text, 777)
        assert _token(text, 2023)
        code, text, _repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            FIXTURES / "duplicate" / "export.csv",
            _selection([_period("2019", "automated", [2019])]),
            _minimal_frames(),
        )
        text = _assert_exit(code, text)
        assert _token(text, 777)
        assert _token(text, 2023)


def test_unparseable_asthma_value_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        clean_asthma, _clean_traffic, script = _require(
            "An unparseable asthma value exits 2 and the message contains that value."
        )
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        for folder in ("unparseable_rate", "unparseable_count"):
            frame = pd.read_csv(
                FIXTURES / folder / "export.csv",
                dtype=str,
                keep_default_na=False,
            )
            code, text, _value, _unused = _quiet_call(capsys, clean_asthma, frame)
            text = _assert_exit(code, text)
            assert _token(text, "NA"), text
        code, text, _repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            FIXTURES / "unparseable_rate" / "export.csv",
            _selection([_period("2019", "automated", [2019])]),
            _minimal_frames(),
        )
        text = _assert_exit(code, text)
        assert _token(text, "NA"), text


def test_unknown_borough_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        clean_asthma, _clean_traffic, script = _require(
            "An unknown borough exits 2 and the message contains the token."
        )
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        frame = _table(FIXTURES / "unknown_borough" / "export.csv")
        code, text, _value, _unused = _quiet_call(capsys, clean_asthma, frame)
        text = _assert_exit(code, text)
        assert _token(text, "Hudson"), text
        code, text, _repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            FIXTURES / "unknown_borough" / "export.csv",
            _selection([_period("2019", "automated", [2019])]),
            _minimal_frames(),
        )
        text = _assert_exit(code, text)
        assert _token(text, "Hudson"), text


def test_fixture_clean_prints_period_counts(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, _clean_traffic, script = _require(
            "A fixture clean prints the period counts and exits 0, "
            "with one asthma row per neighborhood-period and a null suppressed rate."
        )
        frames = _success_frames()
        removed = _removed_duplicates(frames)
        flagged = _expected_flag_ids(frames)
        assert removed == 2
        assert flagged == {SPIKE_ID}
        code, text, repo, seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            FIXTURES / "success" / "export.csv",
            json.loads((FIXTURES / "success" / "source_selection.json").read_text(encoding="utf-8")),
            frames,
            plant_coverage=(("automated", 2019),),
        )
        assert code == 0, text
        lines = _assert_period_lines(text, SUCCESS_PERIODS, removed, len(flagged))
        for dropped in ("2015 1", "2016 1", "2017 1", "2020 1", "2014 1", "Winter 1", "2019-2022 1"):
            assert dropped not in lines
        script_text = script.read_text(encoding="utf-8")
        assert "clean_asthma" in script_text
        assert "clean_traffic" in script_text
        asthma_path, traffic_path, log_path = _outputs(repo)
        log = log_path.read_text(encoding="utf-8")
        for period, count in SUCCESS_PERIODS.items():
            assert f"{period} {count}" in log
        assert f"duplicates removed {removed}" in log
        assert f"invalid counts flagged {len(flagged)}" in log
        got = _asthma_map(_table(asthma_path))
        assert len(got) == 4, "one asthma row per neighborhood-period"
        _assert_asthma(
            got[("101", "2019")],
            typed=False,
            name="Washington Heights",
            borough="Manhattan",
            kind="year",
            years="2019",
            count=10,
            rate=None,
        )
        _assert_asthma(
            got[("102", "2015-2017")],
            typed=False,
            name="Central Harlem",
            borough="Bronx",
            kind="pooled",
            years="2015,2016,2017",
            count=None,
            rate=0,
        )
        _assert_asthma(
            got[("103", "2015 to 2016")],
            typed=False,
            name="East Harlem",
            borough="Queens",
            kind="pooled",
            years="2015,2016",
            count=0,
            rate=3.5,
        )
        _assert_asthma(
            got[("104", "2018")],
            typed=False,
            name="Highbridge",
            borough="Bronx",
            kind="year",
            years="2018",
            count=2,
            rate=4,
        )
        traffic = _rows(pd.read_parquet(traffic_path), TRAFFIC_COLUMNS)
        assert len(traffic) == len(_kept_records(frames))
        identities = [_identity(record[":id"]) for record in traffic]
        assert identities.count(DUP_ID) == 1, "exact duplicate rows keep one copy"
        assert identities.count(NEGATIVE_ID) == 0, "a negative volume is excluded"
        assert identities.count(ZERO_ID) == 1, "a zero volume remains"
        assert identities.count(41) == 2, (
            "a historical row and an AADT row may share an :id"
        )
        assert COVERAGE_ID not in identities, "clean does not read coverage.parquet"
        by_id = {}
        for source, year, identity in (
            ("automated", 2019, ZERO_ID),
            ("automated", 2019, 11),
            ("automated", 2019, 12),
            ("automated", 2019, 13),
            ("automated", 2019, 14),
            ("automated", 2019, 15),
            ("historical", 2015, 31),
            ("historical", 2015, 32),
            ("historical", 2015, 33),
        ):
            by_id[identity] = _record(traffic, identity, source=source, year=year)
        assert _number(by_id[ZERO_ID]["vol"]) == pytest.approx(0), "a zero volume remains"
        assert _flag(by_id[ZERO_ID]["high_count_flag"]) is False
        assert _text(by_id[11]["direction_normalized"]) == "NB"
        assert _text(by_id[12]["direction_normalized"]) == "SB"
        assert _text(by_id[13]["direction_normalized"]) == "1"
        assert _text(by_id[14]["direction_normalized"]) == "North"
        assert _text(by_id[15]["direction_normalized"]) == "NB"
        assert _number(by_id[14]["vol"]) == pytest.approx(1), "compass direction does not drop volume"
        for identity, minute in ((11, 0), (12, 15), (13, 30), (14, 45)):
            assert _number(by_id[identity]["minute"]) == minute
            assert _flag(by_id[identity]["hour_complete"]) is False
        assert _text(by_id[11]["borough"]) == "Manhattan"
        assert _text(by_id[11]["segment"]) == "seg-a"
        assert _text(by_id[11]["wkt"]) == WKT
        assert _text(by_id[11]["request"]) == "req-11"
        assert _text(by_id[11]["source"]) == "automated"
        assert _number(by_id[11]["year"]) == 2019
        assert _number(by_id[11]["month"]) == 1
        assert _number(by_id[11]["day"]) == 15
        assert _number(by_id[11]["hour"]) == 8
        _assert_local(by_id[11]["ts_local"], 2019, 1, 15, 8, 0)
        assert _number(by_id[31]["vol"]) == pytest.approx(48), (
            "a historical hourly volume is not divided"
        )
        assert _number(by_id[32]["vol"]) == pytest.approx(48)
        assert _number(by_id[33]["vol"]) == pytest.approx(200)
        assert _text(by_id[31]["direction_normalized"]) == "NB"
        assert _text(by_id[32]["direction_normalized"]) == "SB"
        assert _text(by_id[31]["borough"]) == "Bronx"
        assert _text(by_id[31]["source"]) == "historical"
        assert _flag(by_id[31]["hour_complete"]) is True
        assert _is_null(by_id[31]["minute"]) or _number(by_id[31]["minute"]) == 0
        _assert_local(by_id[31]["ts_local"], 2015, 6, 1, 9, 0)
        historical_41 = _record(traffic, 41, source="historical", year=2015)
        assert _identity(historical_41[":id"]) == 41
        assert _text(historical_41["source"]) == "historical"
        assert _number(historical_41["vol"]) == pytest.approx(200), (
            "the historical row 41 stays in the file"
        )
        aadt = _record(traffic, 41, source="aadt", year=2022)
        assert _number(aadt["vol"]) == pytest.approx(10), (
            "an AADT value of 240 becomes vol 10"
        )
        assert _number(aadt["vol"]) != pytest.approx(999)
        assert _is_null(aadt["hour"]), "AADT leaves the hour null"
        assert _flag(aadt["hour_complete"]) is False
        assert _text(aadt["direction_normalized"]) == "North"
        assert _text(aadt["borough"]) == "Queens"
        assert _text(aadt["source"]) == "aadt"
        assert _identity(aadt[":id"]) == 41
        got_flags = set()
        for record in traffic:
            assert record["monitoring_location_id"] == "", (
                "monitoring_location_id is an empty string"
            )
            assert _number(record["vol"]) >= 0
            if _flag(record["high_count_flag"]):
                got_flags.add(_identity(record[":id"]))
            else:
                assert _flag(record["high_count_flag"]) is False
        assert got_flags == flagged, (
            "counts above the within-year 99.9th percentile are flagged and kept"
        )
        exclusions = _exclusions(repo)
        assert all(record["stage"].strip() == "clean" for record in exclusions)
        negative = _assert_listed(exclusions, NEGATIVE_ID, "automated")
        assert {record["reason"].strip() for record in negative} == {"negative_volume"}
        assert {record["key"].strip() for record in negative} == {str(NEGATIVE_ID)}
        duplicates = _assert_listed(exclusions, DUP_ID, "automated")
        assert {record["reason"].strip() for record in duplicates} == {"duplicate_row"}
        assert {record["key"].strip() for record in duplicates} == {str(DUP_ID)}
        assert len(duplicates) == removed
        reads = _traffic_reads(seen)
        assert reads, "one year parquet is read at a time"
        for path in reads:
            assert path.rstrip("/").endswith("records.parquet"), (
                "one year parquet is read at a time"
            )
            assert "coverage.parquet" not in path
            assert "year=2014" not in path
        for source, year in (("automated", 2019), ("historical", 2015), ("aadt", 2022)):
            suffix = f"data/raw/traffic/{source}/year={year}/records.parquet"
            assert any(suffix in path for path in reads), suffix
        assert not (repo / "data" / "raw" / "traffic" / "historical" / "year=2014").exists()


def test_unexpected_minute_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, clean_traffic, script = _require(
            "An automated minute of 10 is dropped and listed with reason "
            "minute_not_quarter, and a year that is otherwise valid exits 0."
        )
        kept = _automated_row(9, minute=0, vol=3, hour=4, day=2, month=6, year=2022)
        dropped = _automated_row(7, minute=10, vol=4, hour=5, day=2, month=6, year=2022)
        frame = pd.DataFrame([kept, dropped])
        code, text, repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            [_asthma_row(TimePeriod="2022", GeoID="7", asthma_ed_count="3", asthma_ed_rate_per_10000="4")],
            _selection([_period("2022", "automated", [2022])]),
            {("automated", 2022): frame},
        )
        assert code == 0, (
            "a fixture clean that includes automated minute 10 exits 0; "
            f"got exit {code}: {text}"
        )
        lines = _lines(text)
        assert lines.count("2022 1") == 1, text
        _asthma, traffic_path, _log = _outputs(repo)
        stored = _by_id(_rows(pd.read_parquet(traffic_path), TRAFFIC_COLUMNS))
        assert 7 not in stored, "minute 10 is absent from cleaned_traffic"
        assert _number(stored[9]["minute"]) == 0
        assert _number(stored[9]["vol"]) == pytest.approx(3)
        assert _flag(stored[9]["hour_complete"]) is False
        _one_exclusion(_exclusions(repo), 7, "minute_not_quarter")
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        code, text, value, _unused = _quiet_call(capsys, clean_traffic, frame, "automated")
        assert code == 0, (
            "clean_traffic drops automated minute 10 and exits 0; "
            f"got exit {code}: {text}"
        )
        by_id = _by_id(_rows(value, TRAFFIC_COLUMNS))
        assert 7 not in by_id, "minute 10 is absent from cleaned rows"
        assert _number(by_id[9]["minute"]) == 0
        assert _number(by_id[9]["vol"]) == pytest.approx(3)
        assert not (tmp_path / "outputs").exists()


def test_non_quarter_automated_minute_is_dropped_and_listed(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, clean_traffic, script = _require(
            "An automated integer minute outside {0, 15, 30, 45} is dropped "
            "and listed with minute_not_quarter. An unparseable automated minute "
            "still exits 2 and does not write cleaned_traffic."
        )
        unparsed = _automated_row(77, minute=0, vol=3, year=2019)
        unparsed["mm"] = "not-a-minute"
        code, text, repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            [_asthma_row()],
            _selection([_period("2019", "automated", [2019])]),
            {("automated", 2019): pd.DataFrame([unparsed])},
        )
        text = _assert_exit(code, text)
        assert _token(text, "not-a-minute"), text
        assert not (repo / "outputs" / "data" / "cleaned_traffic.parquet").is_file(), (
            "cleaned_traffic is not written when an automated minute does not parse"
        )
        duplicate = _automated_row(18, minute=0, vol=1)
        rows = [
            _automated_row(11, minute=0, vol=1),
            _automated_row(12, minute=15, vol=2),
            _automated_row(13, minute=30, vol=3),
            _automated_row(14, minute=45, vol=4),
            _automated_row(16, minute=0, vol=0),
            _automated_row(17, minute=15, vol=-4),
            _automated_row(20, minute=20, vol=6),
            _automated_row(50, minute=50, vol=7),
            duplicate,
            copy.deepcopy(duplicate),
        ]
        frame = pd.DataFrame(rows)
        kept_volumes = [1.0, 2.0, 3.0, 4.0, 0.0, 1.0]
        threshold = float(np.quantile(kept_volumes, 0.999))
        expected_flags = {
            identity
            for identity, volume in (
                (11, 1.0),
                (12, 2.0),
                (13, 3.0),
                (14, 4.0),
                (16, 0.0),
                (18, 1.0),
            )
            if volume > threshold
        }
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        code, text, value, _unused = _quiet_call(capsys, clean_traffic, frame, "automated")
        assert code == 0, (
            "automated minutes 20 and 50 are dropped and the year exits 0; "
            f"got exit {code}: {text}"
        )
        by_id = _by_id(_rows(value, TRAFFIC_COLUMNS))
        assert 20 not in by_id, "minute 20 is absent from cleaned rows"
        assert 50 not in by_id, "minute 50 is absent from cleaned rows"
        assert 17 not in by_id, "a negative volume is excluded"
        for identity, minute, volume in (
            (11, 0, 1),
            (12, 15, 2),
            (13, 30, 3),
            (14, 45, 4),
        ):
            assert _number(by_id[identity]["minute"]) == minute
            assert _number(by_id[identity]["vol"]) == pytest.approx(volume)
            assert _flag(by_id[identity]["hour_complete"]) is False
        assert _number(by_id[16]["vol"]) == pytest.approx(0), "a zero volume remains"
        assert _number(by_id[18]["vol"]) == pytest.approx(1)
        code, text, repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            [_asthma_row()],
            _selection([_period("2019", "automated", [2019])]),
            {("automated", 2019): frame},
        )
        assert code == 0, (
            "a fixture clean that includes automated minutes 20 and 50 exits 0; "
            f"got exit {code}: {text}"
        )
        _assert_period_lines(text, {"2019": 1}, 1, len(expected_flags))
        _asthma, traffic_path, log_path = _outputs(repo)
        assert "duplicates removed 1" in log_path.read_text(encoding="utf-8")
        stored = _by_id(_rows(pd.read_parquet(traffic_path), TRAFFIC_COLUMNS))
        assert set(stored) == {11, 12, 13, 14, 16, 18}
        assert _number(stored[16]["vol"]) == pytest.approx(0), "a zero volume remains"
        for identity, minute in ((11, 0), (12, 15), (13, 30), (14, 45)):
            assert _number(stored[identity]["minute"]) == minute
        exclusions = _exclusions(repo)
        _one_exclusion(exclusions, 20, "minute_not_quarter")
        _one_exclusion(exclusions, 50, "minute_not_quarter")
        _one_exclusion(exclusions, 17, "negative_volume")
        _one_exclusion(exclusions, 18, "duplicate_row")
        assert not any(record["key"].strip() == "16" for record in exclusions), (
            "a zero volume stays out of exclusions_clean"
        )
        quarter_ids = {"11", "12", "13", "14"}
        assert not any(
            record["reason"].strip() == "minute_not_quarter"
            and record["key"].strip() in quarter_ids
            for record in exclusions
        )
        got_flags = {
            identity
            for identity, record in stored.items()
            if _flag(record["high_count_flag"])
        }
        assert got_flags == expected_flags


def test_historical_minute_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, clean_traffic, script = _require(
            "A historical minute other than null or 0 exits 2 and names the minute, source, and year."
        )
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        frame = pd.DataFrame(
            [_historical_row(4, 6, year=2015, minute=10, include_minute=True)]
        )
        code, text, _value, _unused = _quiet_call(capsys, clean_traffic, frame, "historical")
        text = _assert_exit(code, text)
        assert _token(text, 10), text
        assert "historical" in text
        assert _token(text, 2015), text
        code, text, _repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            [_asthma_row()],
            _selection([_period("2015", "historical", [2015])]),
            {("historical", 2015): frame},
            schema=_coverage_schema(historical_minute=True),
        )
        text = _assert_exit(code, text)
        assert _token(text, 10), text
        assert "historical" in text
        assert _token(text, 2015), text


def test_directions_are_kept_and_empty_direction_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, clean_traffic, script = _require(
            "NB and SB are both kept. An empty direction exits 2 and names the :id."
        )
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        frame = pd.DataFrame(
            [
                _automated_row(1, direction="NB", vol=4, minute=0),
                _automated_row(2, direction="SB", vol=5, minute=15),
                _automated_row(3, direction="1", vol=6, minute=30),
                _automated_row(4, direction="North", vol=7, minute=45),
                _automated_row(5, direction=" NB ", vol=8, minute=0),
            ]
        )
        code, text, value, _unused = _quiet_call(capsys, clean_traffic, frame, "automated")
        assert code == 0, text
        by_id = _by_id(_rows(value, TRAFFIC_COLUMNS))
        assert _text(by_id[1]["direction_normalized"]) == "NB"
        assert _text(by_id[2]["direction_normalized"]) == "SB"
        assert _text(by_id[3]["direction_normalized"]) == "1"
        assert _text(by_id[4]["direction_normalized"]) == "North"
        assert _text(by_id[5]["direction_normalized"]) == "NB"
        assert _number(by_id[1]["vol"]) == pytest.approx(4)
        assert _number(by_id[2]["vol"]) == pytest.approx(5)
        assert _number(by_id[4]["vol"]) == pytest.approx(7)
        assert _flag(by_id[1]["hour_complete"]) is False
        assert by_id[1]["monitoring_location_id"] == ""
        assert _text(by_id[1]["borough"]) == "Manhattan"
        _assert_local(by_id[1]["ts_local"], 2019, 1, 15, 8, 0)
        for direction in ("", "   "):
            empty = pd.DataFrame([_automated_row(4242, direction=direction, vol=3)])
            code, text, _value, _unused = _quiet_call(capsys, clean_traffic, empty, "automated")
            text = _assert_exit(code, text)
            assert _token(text, 4242), text
        assert calls == [], "fixture test must not call Socrata"
        assert not (tmp_path / "outputs").exists()
        blank = pd.DataFrame([_automated_row(4242, direction="   ", vol=3, year=2019)])
        code, text, _repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            [_asthma_row()],
            _selection([_period("2019", "automated", [2019])]),
            {("automated", 2019): blank},
        )
        text = _assert_exit(code, text)
        assert _token(text, 4242), text


def test_negative_volume_is_excluded_and_zero_remains(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, clean_traffic, script = _require(
            "A negative volume is absent from the parquet and listed in the exclusions. "
            "A zero volume remains."
        )
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        frame = pd.DataFrame(
            [
                _automated_row(55, vol=-4, minute=0),
                _automated_row(56, vol=0, minute=15),
                _automated_row(57, vol=2, minute=30),
            ]
        )
        code, text, value, _unused = _quiet_call(capsys, clean_traffic, frame, "automated")
        assert code == 0, text
        by_id = _by_id(_rows(value, TRAFFIC_COLUMNS))
        assert 55 not in by_id, "a negative volume is excluded"
        assert _number(by_id[56]["vol"]) == pytest.approx(0), "a zero volume remains"
        assert _number(by_id[57]["vol"]) == pytest.approx(2)
        kept = [0.0, 2.0]
        threshold = float(np.quantile(kept, 0.999))
        expected = {57} if 2.0 > threshold else set()
        if 0.0 > threshold:
            expected.add(56)
        got_flags = {identity for identity, record in by_id.items() if _flag(record["high_count_flag"])}
        assert got_flags == expected
        code, text, repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            [_asthma_row()],
            _selection([_period("2019", "automated", [2019])]),
            {("automated", 2019): frame},
        )
        assert code == 0, text
        _assert_period_lines(text, {"2019": 1}, 0, len(expected))
        _asthma, traffic_path, log_path = _outputs(repo)
        assert "duplicates removed 0" in log_path.read_text(encoding="utf-8")
        assert f"invalid counts flagged {len(expected)}" in log_path.read_text(encoding="utf-8")
        stored = _by_id(_rows(pd.read_parquet(traffic_path), TRAFFIC_COLUMNS))
        assert 55 not in stored, "a negative volume is absent from the parquet"
        assert _number(stored[56]["vol"]) == pytest.approx(0), "a zero volume remains"
        exclusions = _exclusions(repo)
        negative = _assert_listed(exclusions, 55, "automated")
        assert [record["reason"].strip() for record in negative] == ["negative_volume"]
        assert [record["key"].strip() for record in negative] == ["55"]
        assert not any(record["key"].strip() == "56" for record in exclusions), (
            "a zero volume stays out of exclusions_clean"
        )


def test_historical_volume_is_not_divided_and_aadt_becomes_vol(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, clean_traffic, _script = _require(
            "A historical hourly volume is not divided. An AADT value of 240 becomes vol 10."
        )
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        hourly = pd.DataFrame([_historical_row(31, 48, hour=9)])
        code, text, value, _unused = _quiet_call(capsys, clean_traffic, hourly, "historical")
        assert code == 0, text
        by_id = _by_id(_rows(value, TRAFFIC_COLUMNS))
        assert _number(by_id[31]["vol"]) == pytest.approx(48), (
            "a historical hourly volume is not divided"
        )
        assert _flag(by_id[31]["hour_complete"]) is True
        assert _is_null(by_id[31]["minute"]) or _number(by_id[31]["minute"]) == 0
        _assert_local(by_id[31]["ts_local"], 2015, 6, 1, 9, 0)
        mixed = pd.DataFrame(
            [
                _historical_row(1, 24, minute=0, include_minute=True, hour=9),
                _historical_row(2, 36, minute=None, include_minute=True, hour=10),
            ]
        )
        code, text, value, _unused = _quiet_call(capsys, clean_traffic, mixed, "historical")
        assert code == 0, text
        by_id = _by_id(_rows(value, TRAFFIC_COLUMNS))
        assert _number(by_id[1]["vol"]) == pytest.approx(24)
        assert _number(by_id[2]["vol"]) == pytest.approx(36)
        assert _flag(by_id[1]["hour_complete"]) is True
        assert _flag(by_id[2]["hour_complete"]) is True
        aadt = pd.DataFrame([_aadt_row(41, aadt=240, vol=999)])
        code, text, value, _unused = _quiet_call(capsys, clean_traffic, aadt, "aadt")
        assert code == 0, text
        by_id = _by_id(_rows(value, TRAFFIC_COLUMNS))
        assert _number(by_id[41]["vol"]) == pytest.approx(10), (
            "an AADT value of 240 becomes vol 10"
        )
        assert _is_null(by_id[41]["hour"])
        assert _flag(by_id[41]["hour_complete"]) is False
        assert _text(by_id[41]["direction_normalized"]) == "North"
        assert _text(by_id[41]["borough"]) == "Queens"
        assert by_id[41]["monitoring_location_id"] == ""
        assert calls == [], "fixture test must not call Socrata"
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()


def test_missing_records_parquet_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, _clean_traffic, script = _require(
            "A missing records.parquet exits 2 and the message is that path."
        )
        code, text, repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            [_asthma_row()],
            _selection([_period("2019", "automated", [2019])]),
            {},
            plant_coverage=(("automated", 2019),),
            omit_records=(("automated", 2019),),
        )
        text = _assert_exit(code, text)
        expected = "data/raw/traffic/automated/year=2019/records.parquet"
        assert expected in text.replace("\\", "/"), text
        assert not (_year_dir(repo, "automated", 2019) / "records.parquet").exists()
        assert (_year_dir(repo, "automated", 2019) / "coverage.parquet").is_file()


def test_zero_asthma_rows_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, _clean_traffic, script = _require(
            "Zero asthma rows after the period rule exits 2."
        )
        code, text, _repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            FIXTURES / "zero" / "export.csv",
            _selection([_period("2019", "automated", [2019])]),
            _minimal_frames(),
        )
        text = _assert_exit(code, text)
        assert "zero asthma rows" in text.lower(), text


def test_utc_column_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, clean_traffic, script = _require(
            "A source column whose name contains utc exits 2 and names that column."
        )
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        row = _automated_row(7, minute=0, vol=3)
        row["EndUTC"] = "no"
        frame = pd.DataFrame([row])
        code, text, _value, _unused = _quiet_call(capsys, clean_traffic, frame, "automated")
        text = _assert_exit(code, text)
        assert "EndUTC" in text, text
        assert not (tmp_path / "outputs").exists()
        code, text, _repo, _seen = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            [_asthma_row()],
            _selection([_period("2019", "automated", [2019])]),
            {("automated", 2019): frame},
        )
        text = _assert_exit(code, text)
        assert "EndUTC" in text, text


def test_high_counts_are_flagged_within_year(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _clean_asthma, clean_traffic, _script = _require(
            "Counts above the within-year 99.9th percentile are flagged and kept."
        )
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        rows = [_automated_row(1, vol=0, minute=0), _automated_row(2, vol=50, minute=15)]
        rows.extend(_automated_row(identity, vol=1, minute=0) for identity in range(3, 33))
        frame = pd.DataFrame(rows)
        code, text, value, _unused = _quiet_call(capsys, clean_traffic, frame, "automated")
        assert code == 0, text
        by_id = _by_id(_rows(value, TRAFFIC_COLUMNS))
        assert set(by_id) == set(range(1, 33)), "high counts are kept"
        threshold = float(np.quantile([0.0, 50.0] + [1.0] * 30, 0.999))
        for identity, record in by_id.items():
            volume = _number(record["vol"])
            assert _flag(record["high_count_flag"]) is (volume > threshold)
        assert _flag(by_id[2]["high_count_flag"]) is True
        assert _flag(by_id[1]["high_count_flag"]) is False
        assert _flag(by_id[3]["high_count_flag"]) is False
        assert calls == [], "fixture test must not call Socrata"
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()
        assert load_config()["thresholds"]["high_count_percentile"] == pytest.approx(99.9)
