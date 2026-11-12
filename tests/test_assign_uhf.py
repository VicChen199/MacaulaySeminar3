"""Contract tests for assigning monitors to one UHF42 neighborhood.

``assign_points``, ``resolve_non_interior``, and ``scripts/05_assign_uhf42.py``
are absent in this worktree. These tests lock the spatial join, the location
id, and the terminal counts.
"""

import csv
import hashlib
import io
import os
import re
import runpy
import shutil
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest

from lib.config import load_config

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAME = "scripts/05_assign_uhf42.py"
SHAPEFILE = (
    Path("data/raw/geography")
    / "UHF_42_DOHMH_2009"
    / "UHF_42_DOHMH_2009_WGS84.shp"
)
TRAFFIC_PATH = Path("outputs/data/cleaned_traffic.parquet")
ASTHMA_PATH = Path("outputs/data/cleaned_asthma.csv")
LOCATIONS_PATH = Path("outputs/data/traffic_monitoring_locations.geojson")
TRAFFIC_OUT = Path("outputs/data/traffic_with_uhf42.parquet")
AUDIT_PATH = Path("outputs/quality/spatial_join_audit.csv")
EXCLUSIONS_PATH = Path("outputs/audit/exclusions_geo.csv")
LION_RELATIVE = Path("data/raw/geography/lion_do_not_read.txt")
CROSSWALK_RELATIVE = Path("data/raw/geography/id_crosswalk.csv")
METERS_PER_US_SURVEY_FOOT = 1200 / 3937
FEET_PER_METER = 3937 / 1200
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
LOCATION_COLUMNS = (
    "monitoring_location_id",
    "road_segment_id",
    "uhf42_id",
    "assignment",
    "source_crs",
    "geometry",
)
AUDIT_COLUMNS = (
    "monitoring_location_id",
    "assignment",
    "uhf42_id",
    "borough_traffic",
    "borough_polygon",
    "rule",
)
EXCLUSION_COLUMNS = ("stage", "key", "reason", "source")
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
INTERIOR_LON = -73.95
INTERIOR_LAT = 40.75
SEGMENT = "seg-101"


def _require_assign(reason):
    missing = []
    assign_points = None
    try:
        from lib.geo import assign_points as imported

        assign_points = imported
    except ImportError:
        missing.append("lib/geo.py assign_points")
    if assign_points is not None and not callable(assign_points):
        missing.append("lib/geo.py assign_points")
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    return assign_points


def _require_resolve(reason):
    missing = []
    resolve_non_interior = None
    try:
        from lib.geo import resolve_non_interior as imported

        resolve_non_interior = imported
    except ImportError:
        missing.append("lib/geo.py resolve_non_interior")
    if resolve_non_interior is not None and not callable(resolve_non_interior):
        missing.append("lib/geo.py resolve_non_interior")
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    return resolve_non_interior


def _require_script(reason):
    missing = []
    assign_points = None
    try:
        from lib.geo import assign_points as imported

        assign_points = imported
    except ImportError:
        missing.append("lib/geo.py assign_points")
    if assign_points is not None and not callable(assign_points):
        missing.append("lib/geo.py assign_points")
    script = ROOT / SCRIPT_NAME
    if not script.is_file():
        missing.append(
            "scripts/05_assign_uhf42.py "
            "(python scripts/05_assign_uhf42.py takes no arguments)"
        )
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    return assign_points, script


def _token_count(text, token):
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(str(token))}(?![A-Za-z0-9_])"
    return len(re.findall(pattern, text or ""))


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


def _lines(text):
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def _step():
    return float(load_config()["geography"]["coordinate_round_meters"])


def _tolerance_feet():
    meters = float(load_config()["geography"]["boundary_tolerance_meters"])
    return meters * FEET_PER_METER


def _rounded_axis(feet, step):
    meters = feet * METERS_PER_US_SURVEY_FOOT
    snapped = round(meters / step) * step
    return int(round(snapped))


def _location_id(segment_id, x_feet, y_feet, step):
    text = f"{segment_id}|{_rounded_axis(x_feet, step)}|{_rounded_axis(y_feet, step)}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _project(x, y, source_crs):
    from pyproj import Transformer

    transformer = Transformer.from_crs(source_crs, "EPSG:2263", always_xy=True)
    return transformer.transform(x, y)


def _feet_for_wkt(text, source_crs):
    from shapely import wkt

    geom = wkt.loads(text)
    return _project(geom.x, geom.y, source_crs)


def _id_for_wkt(text, segment, source_crs):
    x_feet, y_feet = _feet_for_wkt(text, source_crs)
    return _location_id(segment, x_feet, y_feet, _step())


def _paired_interior_wkts():
    """Two different WKT strings that snap to one rounded projected point."""
    from pyproj import Transformer

    forward = Transformer.from_crs("EPSG:4326", "EPSG:2263", always_xy=True)
    backward = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True)
    x_feet, y_feet = forward.transform(INTERIOR_LON, INTERIOR_LAT)
    shifted = x_feet + (0.2 / METERS_PER_US_SURVEY_FOOT)
    lon2, lat2 = backward.transform(shifted, y_feet)
    first = f"POINT({INTERIOR_LON:.10f} {INTERIOR_LAT:.10f})"
    second = f"POINT({lon2:.10f} {lat2:.10f})"
    assert first != second
    assert _id_for_wkt(first, SEGMENT, "EPSG:4326") == _id_for_wkt(
        second, SEGMENT, "EPSG:4326"
    )
    return first, second


def _row(identity, segment, wkt, borough="Manhattan", vol=1.0):
    return {
        ":id": str(identity),
        "source": "automated",
        "year": 2019,
        "month": 6,
        "day": 1,
        "hour": 8,
        "minute": 0,
        "vol": vol,
        "segment": segment,
        "wkt": wkt,
        "direction_normalized": "NB",
        "borough": borough,
        "request": "req-1",
        "ts_local": "2019-06-01T12:00:00Z",
        "monitoring_location_id": "",
        "hour_complete": False,
        "high_count_flag": False,
    }


def _traffic(rows):
    frame = pd.DataFrame(rows)
    for column in ("year", "month", "day", "hour", "minute"):
        frame[column] = pd.to_numeric(frame[column]).astype("Int64")
    frame["vol"] = frame["vol"].astype(float)
    frame["hour_complete"] = frame["hour_complete"].astype(bool)
    frame["high_count_flag"] = frame["high_count_flag"].astype(bool)
    frame["ts_local"] = pd.to_datetime(frame["ts_local"], utc=True).dt.tz_convert(
        "America/New_York"
    )
    return frame[list(TRAFFIC_COLUMNS)]


def _asthma_rows(ids):
    rows = []
    for uhf_id in ids:
        rows.append(
            {
                "uhf42_id": str(uhf_id),
                "uhf42_name": "Neighborhood",
                "borough": "Manhattan",
                "period": "2019",
                "period_kind": "year",
                "constituent_years": "2019",
                "asthma_ed_count": "1",
                "asthma_ed_rate_per_10000": "1.0",
            }
        )
    return rows


def _boundaries(records, crs):
    import geopandas as gpd

    payload = {"UHFCODE": [], "geometry": []}
    boroughs = []
    has_borough = False
    for record in records:
        payload["UHFCODE"].append(record["UHFCODE"])
        payload["geometry"].append(record["geometry"])
        if "BOROUGH" in record:
            has_borough = True
        boroughs.append(record.get("BOROUGH"))
    if has_borough:
        payload["BOROUGH"] = boroughs
    return gpd.GeoDataFrame(payload, geometry="geometry", crs=crs)


def _box_around(lon, lat, pad=0.05):
    from shapely.geometry import box

    return box(lon - pad, lat - pad, lon + pad, lat + pad)


def _interior_box():
    from shapely.geometry import box

    return box(-74.05, 40.65, -73.85, 40.85)


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
        raise AssertionError(
            f"fixture test must not call Socrata or GitHub ({url})"
        )

    def get(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        calls.append(url)
        raise AssertionError(
            f"fixture test must not call Socrata or GitHub ({url})"
        )

    def urlopen(*args, **kwargs):
        target = args[0] if args else ""
        calls.append(target)
        raise AssertionError("fixture test must not call Socrata or GitHub")

    monkeypatch.setattr("requests.sessions.Session.send", send)
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


def _record_path(path, opened):
    if path is None:
        return
    try:
        text = os.fsdecode(os.fspath(path))
    except TypeError:
        text = str(path)
    opened.append(text.replace("\\", "/"))


def _spy_reads(monkeypatch):
    """Record readers that would open the LION path. Real reads still proceed."""
    import pyogrio

    opened = []
    real_open = io.open
    real_read = pyogrio.read_dataframe
    real_info = pyogrio.read_info
    real_csv = pd.read_csv

    def spy_open(file, *args, **kwargs):
        _record_path(file, opened)
        return real_open(file, *args, **kwargs)

    def spy_read(path, *args, **kwargs):
        _record_path(path, opened)
        return real_read(path, *args, **kwargs)

    def spy_info(path, *args, **kwargs):
        _record_path(path, opened)
        return real_info(path, *args, **kwargs)

    def spy_csv(filepath_or_buffer, *args, **kwargs):
        _record_path(filepath_or_buffer, opened)
        return real_csv(filepath_or_buffer, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy_open)
    monkeypatch.setattr("builtins.open", spy_open)
    monkeypatch.setattr(pyogrio, "read_dataframe", spy_read)
    monkeypatch.setattr(pyogrio, "read_info", spy_info)
    monkeypatch.setattr(pd, "read_csv", spy_csv)
    return opened


def _install_sjoin_spy(monkeypatch):
    import geopandas as gpd
    import lib.geo as geo

    calls = []
    original = gpd.sjoin

    def spy(left, right, *args, **kwargs):
        calls.append(
            {
                "args": args,
                "kwargs": dict(kwargs),
                "left": _epsg_code(getattr(left, "crs", None)),
                "right": _epsg_code(getattr(right, "crs", None)),
            }
        )
        return original(left, right, *args, **kwargs)

    monkeypatch.setattr(gpd, "sjoin", spy)
    tools = getattr(gpd, "tools", None)
    if tools is not None and hasattr(tools, "sjoin"):
        monkeypatch.setattr(tools, "sjoin", spy)
    if hasattr(geo, "sjoin"):
        monkeypatch.setattr(geo, "sjoin", spy)
    return calls


def _epsg_code(crs):
    if crs is None:
        return None
    try:
        code = crs.to_epsg()
    except Exception:
        code = None
    if code is not None:
        return int(code)
    text = str(crs).upper()
    if "2263" in text:
        return 2263
    if "4326" in text:
        return 4326
    return text


def _assert_sjoin(calls):
    assert calls, "points and polygons are joined with geopandas.sjoin"
    for call in calls:
        predicate = call["kwargs"].get("predicate")
        if predicate is None and len(call["args"]) >= 2:
            predicate = call["args"][1]
        assert predicate == "within", call
        assert call["left"] == 2263, call
        assert call["right"] == 2263, call


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


def _quiet_call(capsys, func, *args):
    code, message, value = _call(func, *args)
    captured = capsys.readouterr()
    text = f"{captured.out}{captured.err}{message}"
    return code, text, value


def _assert_no_local_outputs(tmp_path):
    assert not (tmp_path / "outputs").exists(), (
        "assign_points on a fixture must not create outputs/"
    )
    assert not (tmp_path / "data").exists()
    assert list(tmp_path.rglob("outputs")) == []


def _frames(value):
    assert isinstance(value, tuple) and len(value) == 4, value
    locations, traffic, audit, exclusions = value
    for frame in (locations, traffic, audit, exclusions):
        assert isinstance(frame, pd.DataFrame), type(frame)
    return locations, traffic, audit, exclusions


def _assert_columns(frame, columns):
    assert tuple(frame.columns) == tuple(columns), list(frame.columns)


def _one(frame, column, expected):
    matches = frame[frame[column].map(_text) == expected]
    assert len(matches) == 1, frame
    return matches.iloc[0]


def _exclusion_keys(exclusions):
    if exclusions.empty:
        return []
    _assert_columns(exclusions, EXCLUSION_COLUMNS)
    keys = []
    for _, row in exclusions.iterrows():
        assert _text(row["stage"]) == "assign-uhf"
        assert _text(row["reason"])
        keys.append(_text(row["key"]))
    return keys


def _column_values(frame, column):
    if frame.empty or column not in frame.columns:
        return set()
    return {_text(value) for value in frame[column] if not _is_null(value)}


def _assert_terminal(text, assigned, unmatched, boundary, mismatched):
    lines = _lines(text)
    expected = [
        f"locations assigned {assigned}",
        f"unmatched {unmatched}",
        f"on a boundary {boundary}",
        f"borough-mismatched {mismatched}",
    ]
    positions = []
    for line in expected:
        assert lines.count(line) == 1, text
        positions.append(lines.index(line))
    assert positions == sorted(positions), text
    return lines


def _write_table(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_parquet(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _prepare(
    tmp_path,
    script_src,
    traffic,
    asthma_ids,
    boundaries,
    edits,
    lion_marker=False,
    crosswalk_rows=None,
):
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    script = repo / "scripts" / "05_assign_uhf42.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_src, script)
    # Cleaned traffic and the asthma id set are the inputs of assign-uhf.
    _write_parquet(repo / TRAFFIC_PATH, traffic)
    _write_table(repo / ASTHMA_PATH, _asthma_rows(asthma_ids), ASTHMA_COLUMNS)
    if boundaries is not None:
        target = repo / SHAPEFILE
        target.parent.mkdir(parents=True, exist_ok=True)
        boundaries.to_file(target)
    if lion_marker:
        marker = repo / LION_RELATIVE
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("not-a-lion-file\n", encoding="utf-8")
    if crosswalk_rows is not None:
        _write_table(
            repo / CROSSWALK_RELATIVE,
            crosswalk_rows,
            ("boundary_id", "uhf42_id"),
        )
    toml_path = repo / "config" / "pipeline.toml"
    text = toml_path.read_text(encoding="utf-8")
    for old, new in edits:
        assert old in text, old
        text = text.replace(old, new, 1)
    toml_path.write_text(text, encoding="utf-8")
    return repo, script


def _run_script(
    monkeypatch,
    capsys,
    tmp_path,
    script_src,
    traffic,
    asthma_ids,
    boundaries,
    edits=(),
    spy_reads=False,
    lion_marker=False,
    crosswalk_rows=None,
):
    root_toml = (ROOT / "config" / "pipeline.toml").read_bytes()
    repo, script = _prepare(
        tmp_path,
        script_src,
        traffic,
        asthma_ids,
        boundaries,
        edits,
        lion_marker=lion_marker,
        crosswalk_rows=crosswalk_rows,
    )
    project_toml = (repo / "config" / "pipeline.toml").read_bytes()
    saved = _pop_lib_modules()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "path", _copy_path(repo))
    monkeypatch.setattr(sys, "argv", [str(script)])
    calls = _forbid_network(monkeypatch)
    opened = _spy_reads(monkeypatch) if spy_reads else []
    try:
        assert sys.argv == [str(script)], (
            "python scripts/05_assign_uhf42.py takes no arguments"
        )
        code = _invoke(script)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert (repo / "config" / "pipeline.toml").read_bytes() == project_toml
        assert (ROOT / "config" / "pipeline.toml").read_bytes() == root_toml
        assert calls == [], "fixture test must not call Socrata or GitHub"
        return code, text, repo, list(opened)
    finally:
        _restore_modules(saved)


def _csv_table(path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or []), list(reader)


def _assign(capsys, assign_points, traffic, boundaries, asthma_ids):
    code, text, value = _quiet_call(
        capsys, assign_points, traffic, boundaries, asthma_ids
    )
    return code, text, value


def test_interior_epsg4326_point_joins_one_polygon(monkeypatch, capsys, tmp_path):
    reason = (
        "An interior EPSG:4326 point whose UHFCODE equals the only asthma id "
        "joins to that id, and the location id is the hex SHA-1 of the segment "
        "and the rounded projected point."
    )
    with _guard_repo():
        assign_points = _require_assign(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        sjoin_calls = _install_sjoin_spy(monkeypatch)
        root_toml = (ROOT / "config" / "pipeline.toml").read_bytes()
        first, second = _paired_interior_wkts()
        expected = _id_for_wkt(first, SEGMENT, "EPSG:4326")
        traffic = _traffic(
            [
                _row("1", SEGMENT, first, vol=1.0),
                _row("2", SEGMENT, second, vol=2.0),
            ]
        )
        boundaries = _boundaries(
            [{"UHFCODE": "101", "geometry": _interior_box()}],
            "EPSG:4326",
        )
        code, text, value = _assign(
            capsys, assign_points, traffic, boundaries, ["101"]
        )
        assert code == 0, text
        locations, joined, audit, exclusions = _frames(value)
        _assert_columns(locations, LOCATION_COLUMNS)
        assert len(locations) == 1
        location = locations.iloc[0]
        assert _text(location["monitoring_location_id"]) == expected
        assert expected == expected.lower()
        assert len(expected) == 40
        assert _text(location["road_segment_id"]) == SEGMENT
        assert _id_text(location["uhf42_id"]) == "101"
        assert _text(location["assignment"]) == "interior"
        assert _text(location["source_crs"]) == "EPSG:4326"
        assert _text(location.geometry.geom_type) == "Point"
        _assert_columns(audit, AUDIT_COLUMNS)
        assert len(audit) == 1
        assert _text(audit.iloc[0]["monitoring_location_id"]) == expected
        assert _text(audit.iloc[0]["assignment"]) == "interior"
        assert _id_text(audit.iloc[0]["uhf42_id"]) == "101"
        assert _exclusion_keys(exclusions) == []
        for column in TRAFFIC_COLUMNS:
            assert column in joined.columns, column
        assert "road_segment_id" in joined.columns
        assert len(joined) == 2
        assert {_text(value) for value in joined[":id"]} == {"1", "2"}
        assert {_text(value) for value in joined["monitoring_location_id"]} == {
            expected
        }
        assert {_text(value) for value in joined["road_segment_id"]} == {SEGMENT}
        if "uhf42_id" in joined.columns:
            assert {_id_text(value) for value in joined["uhf42_id"]} == {"101"}
        _assert_sjoin(sjoin_calls)
        shapefile = tmp_path / "bounds.shp"
        boundaries.to_file(shapefile)
        path_code, path_text, path_value = _assign(
            capsys, assign_points, traffic, str(shapefile), ["101"]
        )
        assert path_code == 0, path_text
        path_locations, path_joined, _path_audit, path_exclusions = _frames(
            path_value
        )
        assert len(path_locations) == 1
        assert _text(path_locations.iloc[0]["monitoring_location_id"]) == expected
        assert _id_text(path_locations.iloc[0]["uhf42_id"]) == "101"
        assert _text(path_locations.iloc[0]["assignment"]) == "interior"
        assert len(path_joined) == 2
        assert _exclusion_keys(path_exclusions) == []
        _assert_sjoin(sjoin_calls)
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"
        assert (ROOT / "config" / "pipeline.toml").read_bytes() == root_toml


def test_interior_script_prints_assigned_count_and_exits_0(
    monkeypatch, capsys, tmp_path
):
    reason = (
        "An interior fixture prints locations assigned 1 and exits 0, "
        "with one neighborhood per location."
    )
    with _guard_repo():
        _assign_points, script = _require_script(reason)
        first, second = _paired_interior_wkts()
        expected = _id_for_wkt(first, SEGMENT, "EPSG:4326")
        traffic = _traffic(
            [
                _row("1", SEGMENT, first, vol=1.0),
                _row("2", SEGMENT, second, vol=2.0),
            ]
        )
        from shapely.geometry import box

        boundaries = _boundaries(
            [
                {"UHFCODE": "0", "geometry": box(-74.2, 40.5, -74.15, 40.55)},
                {"UHFCODE": "101", "geometry": _interior_box()},
            ],
            "EPSG:4326",
        )
        lion = LION_RELATIVE.as_posix()
        code, text, repo, opened = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            traffic,
            ["101"],
            boundaries,
            edits=(('lion_path = ""', f'lion_path = "{lion}"'),),
            spy_reads=True,
            lion_marker=True,
        )
        assert code == 0, text
        _assert_terminal(text, 1, 0, 0, 0)
        assert not any("lion_do_not_read.txt" in path for path in opened), opened
        assert (repo / LION_RELATIVE).read_text(encoding="utf-8") == (
            "not-a-lion-file\n"
        )
        locations = _read_locations(repo / LOCATIONS_PATH)
        assert len(locations) == 1
        assert _text(locations.iloc[0]["monitoring_location_id"]) == expected
        assert _id_text(locations.iloc[0]["uhf42_id"]) == "101"
        assert _text(locations.iloc[0]["assignment"]) == "interior"
        assert _text(locations.iloc[0]["source_crs"]) == "EPSG:4326"
        assert _text(locations.iloc[0]["road_segment_id"]) == SEGMENT
        joined = pd.read_parquet(repo / TRAFFIC_OUT)
        assert len(joined) == 2
        assert {_text(value) for value in joined["monitoring_location_id"]} == {
            expected
        }
        assert {_text(value) for value in joined["road_segment_id"]} == {SEGMENT}
        if "uhf42_id" in joined.columns:
            assert {_id_text(value) for value in joined["uhf42_id"]} == {"101"}
        header, records = _csv_table(repo / AUDIT_PATH)
        assert header == AUDIT_COLUMNS
        assert len(records) == 1
        assert records[0]["monitoring_location_id"].strip() == expected
        assert records[0]["assignment"].strip() == "interior"
        assert _id_text(records[0]["uhf42_id"]) == "101"
        exclusion_header, exclusion_rows = _csv_table(repo / EXCLUSIONS_PATH)
        assert exclusion_header == EXCLUSION_COLUMNS
        assert exclusion_rows == []


def test_outside_point_prints_unmatched_and_exits_2(monkeypatch, capsys, tmp_path):
    reason = (
        "A point outside every polygon, with an empty lion_path, "
        "prints the unmatched count and exits 2."
    )
    with _guard_repo():
        assign_points, script = _require_script(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        wkt = "POINT(-73.70 40.50)"
        traffic = _traffic(
            [
                _row("1", SEGMENT, wkt, vol=1.0),
                _row("2", SEGMENT, wkt, vol=2.0),
            ]
        )
        boundaries = _boundaries(
            [{"UHFCODE": "101", "geometry": _interior_box()}],
            "EPSG:4326",
        )
        code, text, value = _assign(
            capsys, assign_points, traffic, boundaries, ["101"]
        )
        assert code == 0, text
        locations, joined, _audit, exclusions = _frames(value)
        assert len(locations) == 1
        location = locations.iloc[0]
        assert _text(location["assignment"]) == "outside_nyc"
        assert _is_null(location["uhf42_id"])
        assert _text(location["source_crs"]) == "EPSG:4326"
        assert len(joined) == 0
        assert _exclusion_keys(exclusions) == [
            _text(location["monitoring_location_id"])
        ]
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"
        script_code, script_text, _repo, _opened = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            traffic,
            ["101"],
            boundaries,
        )
        assert script_code == 2, script_text
        _assert_terminal(script_text, 0, 1, 0, 0)


def test_origin_point_exits_2_and_message_contains_both_numbers(
    monkeypatch, capsys, tmp_path
):
    reason = (
        "A point whose coordinates are neither lon/lat nor EPSG:2263 "
        "exits 2 and the message contains both numbers."
    )
    with _guard_repo():
        assign_points = _require_assign(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        boundaries = _boundaries(
            [{"UHFCODE": "101", "geometry": _interior_box()}],
            "EPSG:4326",
        )
        origin = _traffic([_row("1", SEGMENT, "POINT(0 0)")])
        code, text, _value = _assign(
            capsys, assign_points, origin, boundaries, ["101"]
        )
        assert code == 2, text
        assert _token_count(text, "0") >= 1, text
        state_plane = _traffic([_row("1", SEGMENT, "POINT(1000 1001)")])
        code, text, _value = _assign(
            capsys, assign_points, state_plane, boundaries, ["101"]
        )
        assert code == 2, text
        assert _token_count(text, "1000") >= 1, text
        assert _token_count(text, "1001") >= 1, text
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"


def test_state_plane_point_is_epsg2263(monkeypatch, capsys, tmp_path):
    reason = (
        "POINT(990000 200000) inside a 2263 polygon has source_crs EPSG:2263."
    )
    with _guard_repo():
        assign_points = _require_assign(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        from shapely.geometry import box

        wkt = "POINT(990000 200000)"
        traffic = _traffic([_row("1", "seg-2263", wkt)])
        boundaries = _boundaries(
            [
                {
                    "UHFCODE": "101",
                    "geometry": box(980000, 190000, 1000000, 210000),
                }
            ],
            "EPSG:2263",
        )
        code, text, value = _assign(
            capsys, assign_points, traffic, boundaries, ["101"]
        )
        assert code == 0, text
        locations, joined, _audit, exclusions = _frames(value)
        assert len(locations) == 1
        location = locations.iloc[0]
        assert _text(location["source_crs"]) == "EPSG:2263"
        assert _text(location["assignment"]) == "interior"
        assert _id_text(location["uhf42_id"]) == "101"
        expected = _id_for_wkt(wkt, "seg-2263", "EPSG:2263")
        assert _text(location["monitoring_location_id"]) == expected
        assert len(joined) == 1
        assert _text(joined.iloc[0]["monitoring_location_id"]) == expected
        assert _exclusion_keys(exclusions) == []
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"


def test_inclusive_coordinate_endpoints(monkeypatch, capsys, tmp_path):
    reason = (
        "Lon/lat endpoints are EPSG:4326 and both coordinates greater than "
        "1000 are EPSG:2263."
    )
    with _guard_repo():
        assign_points = _require_assign(reason)
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        corners = ((-74.3, 40.4, "3"), (-73.6, 40.95, "4"))
        for lon, lat, uhf_id in corners:
            wkt = f"POINT({lon} {lat})"
            traffic = _traffic([_row("1", f"seg-{uhf_id}", wkt)])
            boundaries = _boundaries(
                [{"UHFCODE": uhf_id, "geometry": _box_around(lon, lat)}],
                "EPSG:4326",
            )
            code, text, value = _assign(
                capsys, assign_points, traffic, boundaries, [uhf_id]
            )
            assert code == 0, text
            locations, _joined, _audit, _exclusions = _frames(value)
            assert _text(locations.iloc[0]["source_crs"]) == "EPSG:4326"
            assert _text(locations.iloc[0]["assignment"]) == "interior"
            assert _id_text(locations.iloc[0]["uhf42_id"]) == uhf_id
        from shapely.geometry import box

        wkt = "POINT(1001 1001)"
        traffic = _traffic([_row("1", "seg-5", wkt)])
        boundaries = _boundaries(
            [{"UHFCODE": "5", "geometry": box(0, 0, 5000, 5000)}],
            "EPSG:2263",
        )
        code, text, value = _assign(
            capsys, assign_points, traffic, boundaries, ["5"]
        )
        assert code == 0, text
        locations, _joined, _audit, _exclusions = _frames(value)
        assert _text(locations.iloc[0]["source_crs"]) == "EPSG:2263"
        assert _text(locations.iloc[0]["assignment"]) == "interior"
        assert _id_text(locations.iloc[0]["uhf42_id"]) == "5"
        _assert_no_local_outputs(tmp_path)


def test_equal_lion_lengths_stay_boundary(monkeypatch, capsys, tmp_path):
    reason = (
        "When the two longest LION intersections are within 0.001 feet, "
        "resolve_non_interior stays boundary and leaves uhf42_id null."
    )
    with _guard_repo():
        resolve_non_interior = _require_resolve(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        locations, boundaries, lion = _lion_fixture(("tie", "near"))
        code, text, value = _quiet_call(
            capsys, resolve_non_interior, locations, boundaries, lion
        )
        assert code == 0, text
        resolved = _resolved_locations(value, locations)
        for segment in ("tie", "near"):
            row = _one(resolved, "road_segment_id", segment)
            assert _text(row["assignment"]) == "boundary", segment
            assert _is_null(row["uhf42_id"]), segment
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"


def test_unique_longer_lion_length_is_lion_majority(monkeypatch, capsys, tmp_path):
    reason = (
        "A unique longer LION intersection sets assignment to lion_majority "
        "and that one uhf42_id."
    )
    with _guard_repo():
        resolve_non_interior = _require_resolve(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        locations, boundaries, lion = _lion_fixture(("tenth", "major"))
        code, text, value = _quiet_call(
            capsys, resolve_non_interior, locations, boundaries, lion
        )
        assert code == 0, text
        resolved = _resolved_locations(value, locations)
        for segment in ("tenth", "major"):
            row = _one(resolved, "road_segment_id", segment)
            assert _text(row["assignment"]) == "lion_majority", segment
            assert _id_text(row["uhf42_id"]) == "2", segment
        assert len(resolved) == 2
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"


def test_missing_shapefile_exits_2_and_mentions_select_sources(
    monkeypatch, capsys, tmp_path
):
    reason = (
        "A missing shapefile exits 2 and the message contains select-sources."
    )
    with _guard_repo():
        _assign_points, script = _require_script(reason)
        traffic = _traffic(
            [_row("1", SEGMENT, f"POINT({INTERIOR_LON:.10f} {INTERIOR_LAT:.10f}")]
        )
        code, text, repo, _opened = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            traffic,
            ["101"],
            None,
        )
        assert code == 2, text
        assert "select-sources" in text, text
        assert not (repo / SHAPEFILE).exists()


def test_fixture_does_not_create_repository_outputs_or_data(
    monkeypatch, capsys, tmp_path
):
    reason = (
        "Calling assign_points on a fixture must not create an outputs/ "
        "directory and must not call Socrata."
    )
    with _guard_repo():
        assign_points = _require_assign(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        first, _second = _paired_interior_wkts()
        traffic = _traffic([_row("1", SEGMENT, first)])
        boundaries = _boundaries(
            [{"UHFCODE": "101", "geometry": _interior_box()}],
            "EPSG:4326",
        )
        code, text, _value = _assign(
            capsys, assign_points, traffic, boundaries, ["101"]
        )
        assert code == 0, text
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"
        assert not (ROOT / "outputs").exists()
        assert not (ROOT / "data").exists()


def test_near_edge_and_overlapping_polygons_are_boundary(
    monkeypatch, capsys, tmp_path
):
    reason = (
        "A point within boundary_tolerance_meters of an edge, or a point "
        "inside more than one polygon, is boundary."
    )
    with _guard_repo():
        assign_points = _require_assign(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        from shapely.geometry import Point, box

        tolerance = _tolerance_feet()
        near = tolerance * 0.5
        far = tolerance + 2.0
        assert near < tolerance < far
        boundaries = _boundaries(
            [
                {
                    "UHFCODE": "101",
                    "geometry": box(990000, 200000, 992000, 202000),
                }
            ],
            "EPSG:2263",
        )
        near_wkt = f"POINT({990000 + near} 201000)"
        far_wkt = f"POINT({990000 + far} 201000)"
        traffic = _traffic(
            [
                _row("1", "near-edge", near_wkt),
                _row("2", "inside-edge", far_wkt),
            ]
        )
        code, text, value = _assign(
            capsys, assign_points, traffic, boundaries, ["101"]
        )
        assert code == 0, text
        locations, joined, _audit, exclusions = _frames(value)
        near_row = _one(locations, "road_segment_id", "near-edge")
        far_row = _one(locations, "road_segment_id", "inside-edge")
        assert _text(near_row["assignment"]) == "boundary"
        assert _is_null(near_row["uhf42_id"])
        assert _text(near_row["source_crs"]) == "EPSG:2263"
        assert _text(far_row["assignment"]) == "interior"
        assert _id_text(far_row["uhf42_id"]) == "101"
        assert _text(near_row["monitoring_location_id"]) in _exclusion_keys(
            exclusions
        )
        assert _text(far_row["monitoring_location_id"]) not in _exclusion_keys(
            exclusions
        )
        assert "near-edge" not in _column_values(joined, "segment")
        assert "inside-edge" in _column_values(joined, "segment")
        overlap = _boundaries(
            [
                {
                    "UHFCODE": "201",
                    "geometry": box(-74.05, 40.65, -73.85, 40.85),
                },
                {
                    "UHFCODE": "202",
                    "geometry": box(-74.00, 40.70, -73.90, 40.80),
                },
            ],
            "EPSG:4326",
        )
        point = Point(INTERIOR_LON, INTERIOR_LAT)
        assert point.within(overlap.geometry.iloc[0])
        assert point.within(overlap.geometry.iloc[1])
        overlap_traffic = _traffic(
            [_row("1", "overlap", f"POINT({INTERIOR_LON:.10f} {INTERIOR_LAT:.10f}")]
        )
        code, text, value = _assign(
            capsys, assign_points, overlap_traffic, overlap, ["201", "202"]
        )
        assert code == 0, text
        locations, joined, _audit, exclusions = _frames(value)
        assert len(locations) == 1
        assert _text(locations.iloc[0]["assignment"]) == "boundary"
        assert _is_null(locations.iloc[0]["uhf42_id"])
        assert len(joined) == 0
        assert _exclusion_keys(exclusions) == [
            _text(locations.iloc[0]["monitoring_location_id"])
        ]
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"


def test_borough_mismatch_uses_canonical_names(monkeypatch, capsys, tmp_path):
    reason = (
        "A traffic borough that disagrees with the polygon borough is "
        "borough_mismatch. Compared values use borough_aliases and the "
        "canonical name. A missing borough column is not a mismatch."
    )
    with _guard_repo():
        assign_points = _require_assign(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        from shapely.geometry import box

        boundaries = _boundaries(
            [
                {
                    "UHFCODE": "101",
                    "BOROUGH": "Queens",
                    "geometry": box(-74.05, 40.65, -73.85, 40.85),
                },
                {
                    "UHFCODE": "102",
                    "BOROUGH": "Manhattan",
                    "geometry": box(-73.80, 40.65, -73.70, 40.85),
                },
            ],
            "EPSG:4326",
        )
        traffic = _traffic(
            [
                _row("1", "queens-segment", "POINT(-73.95 40.75)", borough="Manhattan"),
                _row("2", "manhattan-segment", "POINT(-73.75 40.75)", borough="manhattan"),
            ]
        )
        code, text, value = _assign(
            capsys, assign_points, traffic, boundaries, ["101", "102"]
        )
        assert code == 0, text
        locations, joined, audit, exclusions = _frames(value)
        _assert_columns(audit, AUDIT_COLUMNS)
        mismatch = _one(locations, "road_segment_id", "queens-segment")
        match = _one(locations, "road_segment_id", "manhattan-segment")
        assert _text(mismatch["assignment"]) == "borough_mismatch"
        assert _is_null(mismatch["uhf42_id"])
        assert _text(match["assignment"]) == "interior"
        assert _id_text(match["uhf42_id"]) == "102"
        mismatch_audit = _one(
            audit, "monitoring_location_id", _text(mismatch["monitoring_location_id"])
        )
        match_audit = _one(
            audit, "monitoring_location_id", _text(match["monitoring_location_id"])
        )
        assert _text(mismatch_audit["borough_traffic"]) == "Manhattan"
        assert _text(mismatch_audit["borough_polygon"]) == "Queens"
        assert _text(mismatch_audit["assignment"]) == "borough_mismatch"
        assert _text(match_audit["borough_traffic"]) == "Manhattan"
        assert _text(match_audit["borough_polygon"]) == "Manhattan"
        assert "manhattan-segment" in _column_values(joined, "segment")
        assert "queens-segment" not in _column_values(joined, "segment")
        keys = _exclusion_keys(exclusions)
        assert _text(mismatch["monitoring_location_id"]) in keys
        assert _text(match["monitoring_location_id"]) not in keys
        plain = _boundaries(
            [{"UHFCODE": "101", "geometry": _interior_box()}],
            "EPSG:4326",
        )
        plain_traffic = _traffic(
            [_row("1", SEGMENT, f"POINT({INTERIOR_LON:.10f} {INTERIOR_LAT:.10f}")]
        )
        code, text, value = _assign(
            capsys, assign_points, plain_traffic, plain, ["101"]
        )
        assert code == 0, text
        locations, _joined, _audit, _exclusions = _frames(value)
        assert _text(locations.iloc[0]["assignment"]) == "interior"
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"


def test_id_mismatch_without_crosswalk_exits_2(monkeypatch, capsys, tmp_path):
    reason = (
        "When the boundary ids differ from the asthma ids and "
        "geography.id_crosswalk_csv is empty, the process exits 2 and the "
        "message contains id_crosswalk."
    )
    with _guard_repo():
        assign_points = _require_assign(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        assert load_config()["geography"]["id_crosswalk_csv"] == ""
        traffic = _traffic(
            [_row("1", SEGMENT, f"POINT({INTERIOR_LON:.10f} {INTERIOR_LAT:.10f}")]
        )
        boundaries = _boundaries(
            [{"UHFCODE": "999", "geometry": _interior_box()}],
            "EPSG:4326",
        )
        code, text, _value = _assign(
            capsys, assign_points, traffic, boundaries, ["101"]
        )
        assert code == 2, text
        assert "id_crosswalk" in text, text
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"


def test_crosswalk_maps_boundary_id_to_asthma_id(monkeypatch, capsys, tmp_path):
    reason = (
        "A crosswalk CSV with boundary_id and uhf42_id maps the boundary "
        "code onto the asthma id."
    )
    with _guard_repo():
        _assign_points, script = _require_script(reason)
        traffic = _traffic(
            [_row("1", SEGMENT, f"POINT({INTERIOR_LON:.10f} {INTERIOR_LAT:.10f}")]
        )
        boundaries = _boundaries(
            [{"UHFCODE": "999", "geometry": _interior_box()}],
            "EPSG:4326",
        )
        relative = CROSSWALK_RELATIVE.as_posix()
        code, text, repo, _opened = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            traffic,
            ["101"],
            boundaries,
            edits=(
                ('id_crosswalk_csv = ""', f'id_crosswalk_csv = "{relative}"'),
            ),
            crosswalk_rows=[{"boundary_id": "999", "uhf42_id": "101"}],
        )
        assert code == 0, text
        _assert_terminal(text, 1, 0, 0, 0)
        locations = _read_locations(repo / LOCATIONS_PATH)
        assert len(locations) == 1
        assert _id_text(locations.iloc[0]["uhf42_id"]) == "101"
        assert _text(locations.iloc[0]["assignment"]) == "interior"


def test_normalized_ids_match_and_code_zero_is_ignored(monkeypatch, capsys, tmp_path):
    reason = (
        "Boundary ids use the same normalization as asthma ids: 01 and 1 "
        "are 1, and code 0 is ignored."
    )
    with _guard_repo():
        assign_points = _require_assign(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        from shapely.geometry import box

        boundaries = _boundaries(
            [
                {"UHFCODE": "01", "geometry": _interior_box()},
                {"UHFCODE": "0", "geometry": box(-74.2, 40.5, -74.15, 40.55)},
            ],
            "EPSG:4326",
        )
        traffic = _traffic(
            [_row("1", SEGMENT, f"POINT({INTERIOR_LON:.10f} {INTERIOR_LAT:.10f}")]
        )
        code, text, value = _assign(
            capsys, assign_points, traffic, boundaries, ["1"]
        )
        assert code == 0, text
        locations, joined, _audit, exclusions = _frames(value)
        assert len(locations) == 1
        assert _text(locations.iloc[0]["assignment"]) == "interior"
        assert _id_text(locations.iloc[0]["uhf42_id"]) == "1"
        assert len(joined) == 1
        assert _exclusion_keys(exclusions) == []
        _assert_no_local_outputs(tmp_path)
        assert calls == [], "fixture test must not call Socrata or GitHub"


def _lion_fixture(segments):
    """Axis-aligned LION lengths in EPSG:2263 feet.

    Polygon 1 covers x 990000–990100 and polygon 2 covers x 990100–990300.
    ``tie`` is 50 feet in each polygon. ``near`` is 0.0005 feet longer in
    polygon 2, still within 0.001 feet. ``tenth`` is 0.01 feet longer in
    polygon 2. ``major`` is 150 feet in polygon 2 and 100 feet in polygon 1.
    """
    import geopandas as gpd
    from shapely.geometry import LineString, Point, box

    specs = {
        "tie": ((990050, 200100), (990150, 200100)),
        "near": ((990050, 200110), (990150.0005, 200110)),
        "tenth": ((990050, 200120), (990150.01, 200120)),
        "major": ((990000, 200130), (990250, 200130)),
    }
    chosen = [segment for segment in segments]
    lines = [LineString(specs[segment]) for segment in chosen]
    points = [Point(990100, line.coords[0][1]) for line in lines]
    locations = gpd.GeoDataFrame(
        {
            "monitoring_location_id": [f"loc-{segment}" for segment in chosen],
            "road_segment_id": chosen,
            "uhf42_id": [None] * len(chosen),
            "assignment": ["boundary"] * len(chosen),
            "source_crs": ["EPSG:2263"] * len(chosen),
        },
        geometry=points,
        crs="EPSG:2263",
    )
    boundaries = _boundaries(
        [
            {"UHFCODE": "1", "geometry": box(990000, 200000, 990100, 200200)},
            {"UHFCODE": "2", "geometry": box(990100, 200000, 990300, 200200)},
        ],
        "EPSG:2263",
    )
    lion = gpd.GeoDataFrame(
        {
            "SegmentID": chosen,
            "segment_id": chosen,
            "segmentid": chosen,
            "road_segment_id": chosen,
        },
        geometry=lines,
        crs="EPSG:2263",
    )
    _assert_lion_lengths(lion, boundaries)
    return locations, boundaries, lion


def _assert_lion_lengths(lion, boundaries):
    """Lock the fixture lengths the tie rule is measured against."""
    expected = {
        "tie": (50.0, 50.0),
        "near": (50.0, 50.0005),
        "tenth": (50.0, 50.01),
        "major": (100.0, 150.0),
    }
    for row in lion.itertuples(index=False):
        segment = row.SegmentID
        left = row.geometry.intersection(boundaries.geometry.iloc[0]).length
        right = row.geometry.intersection(boundaries.geometry.iloc[1]).length
        want_left, want_right = expected[segment]
        assert abs(left - want_left) < 1e-6, (segment, left, right)
        assert abs(right - want_right) < 1e-6, (segment, left, right)
        difference = abs(left - right)
        if segment in {"tie", "near"}:
            assert difference <= 0.001, (segment, difference)
        else:
            assert difference > 0.001, (segment, difference)
            assert right > left, (segment, left, right)


def _resolved_locations(value, original):
    frame = _first_locations(value)
    if frame is None:
        frame = original
    assert isinstance(frame, pd.DataFrame), type(value)
    assert "assignment" in frame.columns
    assert "uhf42_id" in frame.columns
    assert "road_segment_id" in frame.columns
    return frame


def _first_locations(value):
    if isinstance(value, pd.DataFrame):
        return value
    if isinstance(value, tuple):
        for item in value:
            if isinstance(item, pd.DataFrame) and "assignment" in item.columns:
                return item
    return None


def _read_locations(path):
    import geopandas as gpd

    assert path.is_file(), path
    return gpd.read_file(path)
