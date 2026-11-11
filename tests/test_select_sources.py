"""Contract tests for selecting one traffic source per asthma period.

The product functions are absent in this worktree. These tests lock the
select-sources route, the pagination offset rule, and the scenario screen.
"""

import copy
import csv
import json
import re
import runpy
import shutil
import sys
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
import requests
from scipy import stats

from lib.config import load_config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "select_sources"
SCHEMA_PATH = FIXTURES / "schema_snapshot.json"
SOURCES = ("automated", "historical", "aadt")
COMPARISON_COLUMNS = (
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
COVERAGE_COLUMNS = (
    "period",
    "uhf42_id",
    "n_eligible_segments",
    "n_locations",
    "n_complete_hours",
    "adequate",
    "source",
)
SELECTION_KEYS = (
    "scenario",
    "mix_used",
    "comparability_pass",
    "comparability_r",
    "comparability_n",
    "periods",
)
PERIOD_KEYS = ("period", "source", "years", "included", "reason")
QUARTERS = (0, 15, 30, 45)
ORIGIN_X = -74.20
ORIGIN_Y = 40.50
CELL_W = 0.08
CELL_H = 0.05
N_COL = 6
N_ROW = 7
SHAPEFILE_DIR = "UHF_42_DOHMH_2009"
SHAPEFILE_STEM = "UHF_42_DOHMH_2009_WGS84"
COMMIT_URL = "https://api.github.com/repos/nycehs/NYC_geography/commits/master"
RAW_ROOT = "https://raw.githubusercontent.com/nycehs/NYC_geography"
PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"
SHAPE_PHRASE = "shapefile does not have 42 valid unique ids"
HOUR_REASON = "hourly_definition_unresolved"
COUNT_RE = re.compile(r"count\s*\(\s*\*\s*\)", re.IGNORECASE)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
CANONICAL_COVERAGE = (
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "volume",
    "segment",
    "wkt",
    "direction",
    "borough",
)


def _contract():
    """Import the select-sources surface. Missing names are the unfinished phase."""
    missing = []
    paginate = score_coverage = select_source = ensure_boundaries = None
    try:
        from lib.socrata import paginate
    except ImportError as exc:
        missing.append(f"paginate ({exc})")
    try:
        from lib.audit import score_coverage, select_source
    except ImportError as exc:
        missing.append(f"score_coverage, select_source ({exc})")
    try:
        from lib.geo import ensure_boundaries
    except ImportError as exc:
        missing.append(f"ensure_boundaries ({exc})")
    script = ROOT / "scripts" / "02_select_sources.py"
    if not script.is_file():
        missing.append(
            "scripts/02_select_sources.py (python scripts/02_select_sources.py "
            "is not implemented)"
        )
    if missing:
        pytest.fail(
            "select-sources behavior is not implemented: " + "; ".join(missing)
        )
    assert callable(paginate)
    assert callable(score_coverage)
    assert callable(select_source)
    assert callable(ensure_boundaries)
    return paginate, score_coverage, select_source, ensure_boundaries


def _config():
    return copy.deepcopy(load_config())


def _thresholds():
    return _config()["thresholds"]


def _sizes():
    thresholds = _thresholds()
    return {
        "uhf": int(thresholds["min_uhf_per_period"]),
        "segments": int(thresholds["min_segments_per_uhf"]),
        "dates": int(thresholds["min_observation_dates"]),
        "hours": int(thresholds["min_complete_hours"]),
    }


def _pearson(left, right):
    result = stats.pearsonr(left, right)
    if hasattr(result, "statistic"):
        return float(result.statistic)
    return float(result[0])


def _token(text, token):
    return (
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
            text or "",
        )
        is not None
    )


def _first_present(columns, aliases):
    have = set(columns)
    for alias in aliases:
        if alias in have:
            return alias
    return None


def _coverage_fields(snapshot, source):
    aliases = _config()["fields"][source]
    names = []
    for canonical in CANONICAL_COVERAGE:
        field = _first_present(snapshot[source], aliases[canonical])
        if field:
            names.append(field)
    if source == "aadt":
        field = _first_present(snapshot[source], aliases["aadt"])
        if field is None:
            raise AssertionError("AADT scoring needs the aadt value or a point field")
        names.append(field)
    return names


def _year_field(snapshot, source):
    return _first_present(snapshot[source], _config()["fields"][source]["year"])


def _load_snapshot():
    return json.loads(SCHEMA_PATH.read_text())


def _csv_table(path):
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _read_periods(path):
    fieldnames, records = _csv_table(path)
    assert "asthma_ed_rate_per_10000" in fieldnames
    periods = []
    for record in records:
        years = [int(part) for part in record["constituent_years"].split(",") if part]
        periods.append(
            {
                "period": record["period"],
                "kind": record["period_kind"],
                "years": years,
                "n_rows": int(record["n_rows"]),
            }
        )
    return periods


def _write_counts(path, served):
    years = [int(year) for year in _config()["years"]["targets"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("source", "year", "record_count", "schema_ok"))
        for source in SOURCES:
            for year in years:
                writer.writerow((source, year, int(served.get((source, year), 0)), "true"))


def _point(uhf, seg, n_seg):
    column = (uhf - 1) % N_COL
    row = (uhf - 1) // N_COL
    fraction = (seg + 1) / (n_seg + 1)
    lon = ORIGIN_X + (column + fraction) * CELL_W
    lat = ORIGIN_Y + (row + 0.5) * CELL_H
    return lon, lat


def _cell(uhf):
    from shapely.geometry import box

    column = (uhf - 1) % N_COL
    row = (uhf - 1) // N_COL
    minx = ORIGIN_X + column * CELL_W
    miny = ORIGIN_Y + row * CELL_H
    return box(minx, miny, minx + CELL_W, miny + CELL_H)


def _write_geography(directory, n_polygons, source_sha=None):
    """Write a local UHF shapefile. source_sha seeds SOURCE.txt for reuse."""
    import geopandas as gpd
    from shapely.geometry import mapping

    directory.mkdir(parents=True, exist_ok=True)
    count = n_polygons
    frame = gpd.GeoDataFrame(
        {"UHFCODE": [f"{index:02d}" for index in range(1, count + 1)]},
        geometry=[_cell(index) for index in range(1, count + 1)],
        crs="EPSG:4326",
    )
    folder = directory / SHAPEFILE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    frame.to_file(folder / f"{SHAPEFILE_STEM}.shp")
    sidecar = folder / f"{SHAPEFILE_STEM}.cpg"
    if not sidecar.is_file():
        sidecar.write_text("UTF-8\n")
    features = []
    for row in frame.itertuples(index=False):
        features.append(
            {
                "type": "Feature",
                "properties": {"UHFCODE": row.UHFCODE},
                "geometry": mapping(row.geometry),
            }
        )
    geojson = {"type": "FeatureCollection", "features": features}
    (directory / "UHF42.geo.json").write_text(json.dumps(geojson))
    if source_sha is not None:
        (directory / "SOURCE.txt").write_text(f"{source_sha}\n")
    blobs = {}
    for path in directory.rglob("*"):
        if path.is_file() and path.name != "SOURCE.txt":
            blobs[path.relative_to(directory).as_posix()] = path.read_bytes()
    return blobs


def _schedule(dates, hours):
    return [(1, 6 + index, list(range(hours))) for index in range(dates)]


def _automated_rows(year, schedule, mean_for, asthma_for=None, quarters=QUARTERS, extra_direction=False):
    sizes = _sizes()
    rows = []
    next_id = 1
    for uhf in range(1, sizes["uhf"] + 1):
        mean = float(mean_for(uhf))
        quarter_volume = mean / float(len(QUARTERS))
        rate = None if asthma_for is None else float(asthma_for(uhf))
        for seg in range(sizes["segments"]):
            lon, lat = _point(uhf, seg, sizes["segments"])
            wkt = f"POINT({lon:.8f} {lat:.8f})"
            segment = f"seg-{uhf}-{seg}"
            for month, day, hours in schedule:
                for hour in hours:
                    for minute in quarters:
                        row = {
                            ":id": next_id,
                            "yr": str(year),
                            "m": month,
                            "d": day,
                            "hh": hour,
                            "mm": minute,
                            "vol": quarter_volume,
                            "segmentid": segment,
                            "wktgeom": wkt,
                            "direction": "NB",
                            "boro": "Manhattan",
                        }
                        if rate is not None:
                            row["asthma_ed_rate_per_10000"] = rate
                        rows.append(row)
                        next_id += 1
            if extra_direction:
                month, day, hours = schedule[0]
                row = {
                    ":id": next_id,
                    "yr": str(year),
                    "m": month,
                    "d": day,
                    "hh": hours[0],
                    "mm": 0,
                    "vol": quarter_volume,
                    "segmentid": segment,
                    "wktgeom": wkt,
                    "direction": "SB",
                    "boro": "Manhattan",
                }
                if rate is not None:
                    row["asthma_ed_rate_per_10000"] = rate
                rows.append(row)
                next_id += 1
    return rows


def _historical_rows(year, schedule, mean_for, asthma_for=None, minute=None):
    sizes = _sizes()
    rows = []
    next_id = 1
    for uhf in range(1, sizes["uhf"] + 1):
        mean = float(mean_for(uhf))
        rate = None if asthma_for is None else float(asthma_for(uhf))
        for seg in range(sizes["segments"]):
            lon, lat = _point(uhf, seg, sizes["segments"])
            wkt = f"POINT({lon:.8f} {lat:.8f})"
            segment = f"seg-{uhf}-{seg}"
            for month, day, hours in schedule:
                for hour in hours:
                    row = {
                        ":id": next_id,
                        "year": str(year),
                        "month": month,
                        "day": day,
                        "hour": hour,
                        "volume": mean,
                        "segment": segment,
                        "wkt": wkt,
                        "direction": "NB",
                        "borough": "Manhattan",
                    }
                    if minute is not None:
                        row["minute"] = minute
                    if rate is not None:
                        row["asthma_ed_rate_per_10000"] = rate
                    rows.append(row)
                    next_id += 1
    return rows


def _aadt_rows(year, schedule):
    sizes = _sizes()
    rows = []
    next_id = 1
    for uhf in range(1, sizes["uhf"] + 1):
        for seg in range(sizes["segments"]):
            lon, lat = _point(uhf, seg, sizes["segments"])
            wkt = f"POINT({lon:.8f} {lat:.8f})"
            segment = f"seg-{uhf}-{seg}"
            for month, day, hours in schedule:
                for hour in hours:
                    rows.append(
                        {
                            ":id": next_id,
                            "year": str(year),
                            "month": month,
                            "day": day,
                            "hour": hour,
                            "vol": 10,
                            "aadt": 240,
                            "segmentid": segment,
                            "wktgeom": wkt,
                            "direction": "NB",
                            "borough": "Manhattan",
                        }
                    )
                    next_id += 1
    return rows


def _identity(uhf):
    return float(uhf)


def _reversed(uhf):
    return float(_sizes()["uhf"] + 1 - uhf)


def _full_automated(year, mean_for=_identity, asthma_for=None):
    sizes = _sizes()
    return _automated_rows(
        year,
        _schedule(sizes["dates"], sizes["hours"]),
        mean_for,
        asthma_for=asthma_for,
    )


def _full_historical(year, mean_for=_identity, asthma_for=None):
    sizes = _sizes()
    return _historical_rows(
        year,
        _schedule(sizes["dates"], sizes["hours"]),
        mean_for,
        asthma_for=asthma_for,
    )


def _full_aadt(year):
    sizes = _sizes()
    return _aadt_rows(year, _schedule(sizes["dates"], sizes["hours"]))


class _Snapshot(dict):
    def __init__(self, mapping, samples):
        super().__init__(mapping)
        self.samples = samples


def _json_response(payload, url, status=200):
    response = requests.Response()
    response.status_code = status
    response.reason = "OK" if status == 200 else "ERROR"
    response.url = url
    response.encoding = "utf-8"
    response._content = json.dumps(payload).encode("utf-8")
    if status == 429:
        response.headers["Retry-After"] = "0"
    return response


def _bytes_response(payload, url):
    response = requests.Response()
    response.status_code = 200
    response.reason = "OK"
    response.url = url
    response._content = payload
    return response


def _query(url):
    parsed = urllib.parse.urlsplit(url)
    raw = urllib.parse.parse_qs(parsed.query)
    decoded = urllib.parse.unquote(url)

    def value(*names):
        for name in names:
            found = raw.get(name)
            if found:
                return found[0]
        return ""

    return decoded, {
        "where": value("$where", "where"),
        "limit": value("$limit", "limit"),
        "offset": value("$offset", "offset"),
        "order": value("$order", "order"),
        "select": value("$select", "select"),
    }


def _years_in(text):
    return [int(match) for match in YEAR_RE.findall(text or "")]


def _block_real_http(monkeypatch):
    def urlopen(*args, **kwargs):
        raise AssertionError("fixture test must not call a real host")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(time, "sleep", lambda *args, **kwargs: None)


class _Catalog:
    def __init__(self):
        self.rows = {}

    def add(self, source, year, rows):
        self.rows[(source, int(year))] = list(rows)
        return self

    def allow(self, source, year):
        key = (source, int(year))
        if source not in {name for name, _year in self.rows}:
            raise AssertionError(
                f"must not page {source}; this case does not request that source"
            )
        if key not in self.rows:
            raise AssertionError(
                f"must not page {source} {year}; only constituent years are paged"
            )
        return self.rows[key]


def _install_catalog(monkeypatch, catalog, snapshot):
    _block_real_http(monkeypatch)
    urls = {source: _config()["api"][source]["base_url"] for source in SOURCES}
    calls = []

    def send(self, prepared, **kwargs):
        if len(calls) > 500:
            raise AssertionError("pagination made too many HTTP calls")
        decoded, query = _query(prepared.url)
        if "github.com" in decoded or "githubusercontent.com" in decoded:
            raise AssertionError(
                f"SOURCE.txt is present or this case must not call GitHub ({prepared.url})"
            )
        source = next((name for name, base in urls.items() if decoded.startswith(base)), None)
        if source is None:
            raise AssertionError(f"fixture test must not call a real host ({prepared.url})")
        if query["limit"] == "1" and not COUNT_RE.search(decoded):
            field = _year_field(snapshot, source) or "year"
            return _json_response([{field: "2019"}], prepared.url)
        years = _years_in(query["where"] or decoded)
        if len(years) != 1:
            raise AssertionError(f"$where must name one year ({decoded})")
        year = years[0]
        rows = catalog.allow(source, year)
        field = _year_field(snapshot, source)
        assert field and _token(query["where"] or decoded, field), (
            f"{source} $where must use the first live year alias {field}"
        )
        if COUNT_RE.search(decoded):
            calls.append(
                {
                    "source": source,
                    "year": year,
                    "kind": "count",
                    "where": query["where"],
                    "select": query["select"],
                    "url": decoded,
                }
            )
            return _json_response([{"count": str(len(rows))}], prepared.url)
        if query["offset"] == "":
            raise AssertionError("$offset starts at 0")
        offset = int(query["offset"])
        limit = int(query["limit"])
        page = rows[offset : offset + limit]
        calls.append(
            {
                "source": source,
                "year": year,
                "kind": "page",
                "offset": offset,
                "limit": query["limit"],
                "order": query["order"],
                "where": query["where"],
                "select": query["select"],
                "returned": len(page),
                "url": decoded,
            }
        )
        return _json_response(page, prepared.url)

    monkeypatch.setattr("requests.sessions.Session.send", send)
    return calls


def _assert_paging(calls, snapshot, catalog, page_size):
    blocks = []
    for call in calls:
        if call["kind"] not in {"count", "page"}:
            continue
        key = (call["source"], call["year"])
        if not blocks or blocks[-1][0] != key:
            blocks.append((key, []))
        blocks[-1][1].append(call)
    expected_order = []
    for source, _year in catalog.rows:
        if source not in expected_order:
            expected_order.append(source)
    rank = {source: index for index, source in enumerate(expected_order)}
    last = -1
    for key, group in blocks:
        assert key in catalog.rows, f"must not page {key[0]} {key[1]}"
        position = rank[key[0]]
        assert position >= last, (
            "page automated, then historical only when automated leaves a gap, "
            "then AADT only when neither other source covers a period"
        )
        last = position
        count_calls = [item for item in group if item["kind"] == "count"]
        pages = [item for item in group if item["kind"] == "page"]
        assert count_calls, f"{key[0]} {key[1]} must request count(*)"
        rows = catalog.rows[key]
        if not rows:
            assert pages == [] or all(page["returned"] == 0 for page in pages)
            continue
        expected_offsets = []
        cursor = 0
        while cursor < len(rows):
            expected_offsets.append(cursor)
            cursor += min(page_size, len(rows) - cursor)
        assert [page["offset"] for page in pages] == expected_offsets
        for previous, nxt in zip(pages, pages[1:]):
            assert nxt["offset"] == previous["offset"] + previous["returned"], (
                "offset advances by the number of rows in the page"
            )
        assert pages[-1]["returned"] == len(rows) - pages[-1]["offset"]
        if len(rows) % page_size:
            assert pages[-1]["returned"] < page_size
        for page in pages:
            assert page["limit"] == str(page_size)
            assert page["order"] == ":id"
            for field in _coverage_fields(snapshot, key[0]):
                assert _token(page["select"], field), page["select"]
            assert _token(page["select"], ":id"), page["select"]
    keys = [key for key, _group in blocks]
    assert len(keys) == len(set(keys)), "one year at a time"
    assert set(keys) == set(catalog.rows)


def _assert_year_folder(repo, source, year, row_count, page_size):
    folder = repo / "data" / "raw" / "traffic" / source / f"year={year}"
    parquet = folder / "coverage.parquet"
    assert parquet.is_file(), f"coverage.parquet for {source} {year}"
    import pandas as pd

    frame = pd.read_parquet(parquet)
    assert len(frame.index) == row_count
    assert ":id" in list(frame.columns)
    checkpoint = json.loads((folder / "checkpoint.json").read_text())
    assert int(checkpoint["row_count"]) == row_count
    assert int(checkpoint["next_offset"]) == row_count, (
        f"last page is shorter than page_size {page_size}; "
        f"next_offset advances by that page's row count to {row_count}"
    )
    assert isinstance(checkpoint["page_hash"], str) and checkpoint["page_hash"]
    pages = folder / "pages.jsonl"
    assert pages.is_file()
    line_count = sum(1 for line in pages.read_text().splitlines() if line.strip())
    assert line_count == row_count


def _as_bool(value):
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise AssertionError(f"expected a boolean, got {value!r}")


def _assert_selection(path, scenario, comparability, periods):
    payload = json.loads(path.read_text())
    assert set(payload) == set(SELECTION_KEYS)
    assert payload["scenario"] == scenario
    assert isinstance(payload["scenario"], str)
    included = {item["source"] for item in periods.values() if item["included"]}
    assert payload["mix_used"] is ("automated" in included and "historical" in included)
    assert isinstance(payload["mix_used"], bool)
    if "aadt" in included:
        assert included == {"aadt"}, "AADT does not mix with another source"
    if comparability is None:
        assert payload["comparability_pass"] is None
        assert payload["comparability_r"] is None
        assert payload["comparability_n"] is None
    else:
        passed, correlation, overlap_n = comparability
        assert payload["comparability_pass"] is passed
        assert isinstance(payload["comparability_r"], float)
        assert payload["comparability_r"] == pytest.approx(correlation, abs=1e-6)
        assert type(payload["comparability_n"]) is int
        assert payload["comparability_n"] == overlap_n
    assert isinstance(payload["periods"], list)
    got = {}
    for item in payload["periods"]:
        assert set(item) == set(PERIOD_KEYS)
        assert item["period"] not in got, "a pooled TimePeriod stays one period"
        got[item["period"]] = item
    assert set(got) == set(periods)
    for period, want in periods.items():
        item = got[period]
        assert item["years"] == want["years"]
        assert item["included"] is want["included"]
        assert isinstance(item["included"], bool)
        assert isinstance(item["source"], str)
        assert isinstance(item["reason"], str)
        if want["included"]:
            assert item["source"] == want["source"]
            assert item["reason"] == ""
        else:
            assert item["reason"] != ""
            assert item["source"] != "aadt"
        assert all(type(year) is int for year in item["years"])
    return payload


def _assert_comparison(path, expected):
    fieldnames, records = _csv_table(path)
    assert tuple(fieldnames) == COMPARISON_COLUMNS
    got = {}
    for record in records:
        assert record["period"] not in got
        got[record["period"]] = record
    assert set(got) == set(expected)
    for period, want in expected.items():
        row = got[period]
        assert _as_bool(row["asthma_available"]) is True
        assert _as_bool(row["automated_available"]) is want["automated_available"]
        assert _as_bool(row["historical_available"]) is want["historical_available"]
        assert _as_bool(row["aadt_available"]) is want["aadt_available"]
        assert row["n_matched"].strip() == ""
        assert row["n_usable"].strip() == ""
        assert int(row["n_uhf_with_asthma"]) == want["n_rows"]
        assert row["recommended_source"].strip() == want["recommended_source"]
        if "raw_record_count" in want:
            assert int(row["raw_record_count"]) == want["raw_record_count"]
        if "distinct_segments" in want:
            assert int(row["distinct_segments"]) == want["distinct_segments"]
        if "n_uhf_with_traffic" in want:
            assert int(row["n_uhf_with_traffic"]) == want["n_uhf_with_traffic"]


def _assert_coverage(path, period, source, adequate_ids):
    sizes = _sizes()
    fieldnames, records = _csv_table(path)
    assert tuple(fieldnames) == COVERAGE_COLUMNS
    matched = [
        record
        for record in records
        if record["period"] == period and record["source"] == source
    ]
    by_id = {}
    for record in matched:
        uhf = record["uhf42_id"].strip()
        assert uhf not in by_id
        by_id[uhf] = record
    hours = sizes["segments"] * sizes["dates"] * sizes["hours"]
    for uhf in adequate_ids:
        record = by_id[str(uhf)]
        assert int(record["n_eligible_segments"]) == sizes["segments"]
        assert int(record["n_locations"]) == sizes["segments"]
        assert int(record["n_complete_hours"]) == hours
        assert _as_bool(record["adequate"]) is True
    assert "01" not in by_id
    for record in records:
        if record["uhf42_id"].strip() == "01":
            raise AssertionError("uhf42_id drops leading zeros")


def _assert_terminal(text, scenario, included, excluded):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    assert f"scenario {scenario}" in lines
    start = lines.index(f"scenario {scenario}")
    body = lines[start + 1 :]
    for period, source in included.items():
        assert f"{period} {source}" in body
    for period in excluded:
        matches = [line for line in body if line.startswith(f"{period} excluded ")]
        assert len(matches) == 1
        reason = matches[0][len(f"{period} excluded ") :].strip()
        assert reason
    included_periods = set(included)
    for line in body:
        for period in excluded:
            assert line != f"{period} aadt"
        if line in {f"{period} {source}" for period, source in included.items()}:
            continue
        for period in included_periods:
            assert not line.startswith(f"{period} excluded")


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
        assert not created, "fixture test must not write the repository outputs/ or data/ tree"


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


def _prepare_project(tmp_path, periods_path, schema, served, geography):
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    script_src = ROOT / "scripts" / "02_select_sources.py"
    script = repo / "scripts" / "02_select_sources.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    if script_src.is_file():
        shutil.copy2(script_src, script)
    audit = repo / "outputs" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    shutil.copy2(periods_path, audit / "asthma_time_periods.csv")
    _write_counts(audit / "traffic_year_counts.csv", served)
    (audit / "schema_snapshot.json").write_text(json.dumps(schema, indent=2) + "\n")
    geo = repo / "data" / "raw" / "geography"
    if geography == "valid":
        _write_geography(geo, N_COL * N_ROW, source_sha="b" * 40)
    elif geography == "bad":
        _write_geography(geo, 41, source_sha="b" * 40)
    return repo, script


def _run_script(monkeypatch, capsys, tmp_path, periods_path, schema, catalog, geography="valid"):
    repo, script = _prepare_project(
        tmp_path,
        periods_path,
        schema,
        {key: len(rows) for key, rows in catalog.rows.items()},
        geography,
    )
    root_toml = (ROOT / "config" / "pipeline.toml").read_bytes()
    project_toml = (repo / "config" / "pipeline.toml").read_bytes()
    base = _config()
    base["api"]["retry_backoff_seconds"] = 0.0
    base["api"]["timeout_seconds"] = 5
    saved = _pop_lib_modules()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "path", _copy_path(repo))
    monkeypatch.setattr(sys, "argv", [str(script)])
    calls = _install_catalog(monkeypatch, catalog, schema)
    try:
        import lib.config as copied_config

        monkeypatch.setattr(
            copied_config,
            "load_config",
            lambda config=base: copy.deepcopy(config),
        )
        assert sys.argv == [str(script)], "python scripts/02_select_sources.py takes no arguments"
        code = _invoke(script)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert (repo / "config" / "pipeline.toml").read_bytes() == project_toml
        assert (ROOT / "config" / "pipeline.toml").read_bytes() == root_toml
        return code, text, repo, calls
    finally:
        _restore_modules(saved)


def _page_config(page_size):
    config = _config()
    config["api"]["page_size"] = page_size
    config["api"]["retry_backoff_seconds"] = 0.0
    config["api"]["timeout_seconds"] = 5
    assert config["api"]["order_field"] == ":id"
    return config


def _tiny_rows(year_value, count):
    rows = []
    for index in range(1, count + 1):
        rows.append(
            {
                ":id": index,
                "yr": year_value,
                "m": 1,
                "d": 1,
                "hh": 0,
                "mm": 0,
                "vol": 1,
                "segmentid": "seg",
                "wktgeom": "POINT(-74.16 40.525)",
                "direction": "NB",
                "boro": "Manhattan",
            }
        )
    return rows


def _install_fixed_pages(monkeypatch, rows, count, page_size, status=200, truncate=None):
    _block_real_http(monkeypatch)
    calls = []
    seen = {"n": 0}

    def send(self, prepared, **kwargs):
        seen["n"] += 1
        if seen["n"] > 20:
            raise AssertionError("pagination made too many HTTP calls")
        decoded, query = _query(prepared.url)
        if "github.com" in decoded:
            raise AssertionError("paginate must not call GitHub")
        if not decoded.startswith(_config()["api"]["automated"]["base_url"]):
            raise AssertionError(f"fixture test must not call a real host ({prepared.url})")
        if status == 400 and seen["n"] == 1:
            calls.append({"status": 400, "url": decoded})
            return _json_response({"message": "bad"}, prepared.url, status=400)
        if status == 429 and seen["n"] == 1:
            calls.append({"status": 429, "url": decoded})
            return _json_response({"message": "slow"}, prepared.url, status=429)
        if COUNT_RE.search(decoded):
            calls.append({"kind": "count", "where": query["where"], "url": decoded})
            return _json_response([{"count": str(count)}], prepared.url)
        offset = int(query["offset"] or "0")
        limit = int(query["limit"] or str(page_size))
        page = rows[offset : offset + limit]
        if truncate is not None and offset == 0:
            page = rows[:truncate]
        calls.append(
            {
                "kind": "page",
                "offset": offset,
                "limit": query["limit"],
                "order": query["order"],
                "where": query["where"],
                "select": query["select"],
                "returned": len(page),
                "url": decoded,
            }
        )
        return _json_response(page, prepared.url)

    monkeypatch.setattr("requests.sessions.Session.send", send)
    return calls


def _fingerprint(directory):
    found = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            found[path.relative_to(directory).as_posix()] = path.read_bytes()
    return found


def _assert_no_outputs(tmp_path):
    assert not (tmp_path / "outputs").exists()
    assert list(tmp_path.rglob("outputs")) == []


def test_paginate_offset_advances_by_rows_in_the_page(monkeypatch, tmp_path):
    with _guard_repo():
        paginate, _score, _select, _boundaries = _contract()
        monkeypatch.chdir(tmp_path)
        snapshot = _load_snapshot()
        page_size = 4
        config = _page_config(page_size)
        rows = _tiny_rows("2019", 6)
        schema = _Snapshot(
            snapshot,
            {"automated": {"yr": "2019", "m": "1", "mm": "0"}},
        )
        calls = _install_fixed_pages(monkeypatch, rows, count=6, page_size=page_size)
        output = tmp_path / "paged" / "coverage.parquet"
        code, message, _value = _call(
            paginate, config, "automated", 2019, schema, output
        )
        assert code == 0, message
        pages = [call for call in calls if call.get("kind") == "page"]
        assert [page["offset"] for page in pages] == [0, 4]
        assert [page["returned"] for page in pages] == [4, 2]
        assert pages[1]["offset"] == pages[0]["returned"]
        assert pages[1]["returned"] < page_size
        for page in pages:
            assert page["order"] == ":id"
            assert page["limit"] == str(page_size)
            assert re.search(r"yr\s*=\s*'2019'", page["where"])
        checkpoint = json.loads((output.parent / "checkpoint.json").read_text())
        assert checkpoint["next_offset"] == 6
        assert checkpoint["row_count"] == 6
        assert checkpoint["next_offset"] == pages[1]["offset"] + pages[1]["returned"]
        assert checkpoint["next_offset"] != pages[1]["offset"] + page_size
        import pandas as pd

        assert len(pd.read_parquet(output).index) == 6
        _assert_no_outputs(tmp_path)

        numeric = _Snapshot(snapshot, {"automated": {"yr": 2019}})
        numeric_rows = _tiny_rows(2019, 6)
        numeric_calls = _install_fixed_pages(
            monkeypatch, numeric_rows, count=6, page_size=page_size
        )
        numeric_output = tmp_path / "paged-numeric" / "coverage.parquet"
        code, message, _value = _call(
            paginate, config, "automated", 2019, numeric, numeric_output
        )
        assert code == 0, message
        for call in numeric_calls:
            where = call.get("where") or ""
            if not where:
                continue
            assert re.search(r"yr\s*=\s*2019\b", where)
            assert not re.search(r"yr\s*=\s*'2019'", where)
        _assert_no_outputs(tmp_path)


def test_paginate_http_400_exits_1_with_url(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        paginate, _score, _select, _boundaries = _contract()
        monkeypatch.chdir(tmp_path)
        schema = _Snapshot(_load_snapshot(), {"automated": {"yr": "2019"}})
        _install_fixed_pages(monkeypatch, _tiny_rows("2019", 4), count=4, page_size=4, status=400)
        output = tmp_path / "paged" / "coverage.parquet"
        code, message, _value = _call(
            paginate, _page_config(4), "automated", 2019, schema, output
        )
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}{message}"
        assert code == 1
        assert _config()["api"]["automated"]["base_url"] in text
        assert not output.exists()
        _assert_no_outputs(tmp_path)


def test_paginate_retries_http_429(monkeypatch, tmp_path):
    with _guard_repo():
        paginate, _score, _select, _boundaries = _contract()
        monkeypatch.chdir(tmp_path)
        schema = _Snapshot(_load_snapshot(), {"automated": {"yr": "2019"}})
        calls = _install_fixed_pages(
            monkeypatch, _tiny_rows("2019", 6), count=6, page_size=4, status=429
        )
        output = tmp_path / "paged" / "coverage.parquet"
        code, message, _value = _call(
            paginate, _page_config(4), "automated", 2019, schema, output
        )
        assert code == 0, message
        assert any(call.get("status") == 429 for call in calls)
        checkpoint = json.loads((output.parent / "checkpoint.json").read_text())
        assert checkpoint["next_offset"] == 6
        assert output.is_file()
        _assert_no_outputs(tmp_path)


def test_paginate_duplicate_id_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        paginate, _score, _select, _boundaries = _contract()
        monkeypatch.chdir(tmp_path)
        rows = _tiny_rows("2019", 4)
        rows[2][":id"] = rows[1][":id"]
        schema = _Snapshot(_load_snapshot(), {"automated": {"yr": "2019"}})
        _install_fixed_pages(monkeypatch, rows, count=4, page_size=4)
        output = tmp_path / "paged" / "coverage.parquet"
        code, message, _value = _call(
            paginate, _page_config(4), "automated", 2019, schema, output
        )
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}{message}"
        assert code == 2
        assert "automated" in text
        assert "2019" in text
        assert not output.exists()
        _assert_no_outputs(tmp_path)


def test_paginate_short_page_before_count_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        paginate, _score, _select, _boundaries = _contract()
        monkeypatch.chdir(tmp_path)
        schema = _Snapshot(_load_snapshot(), {"automated": {"yr": "2019"}})
        _install_fixed_pages(
            monkeypatch,
            _tiny_rows("2019", 10),
            count=10,
            page_size=4,
            truncate=2,
        )
        output = tmp_path / "paged" / "coverage.parquet"
        code, message, _value = _call(
            paginate, _page_config(4), "automated", 2019, schema, output
        )
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}{message}"
        assert code == 2
        assert "automated" in text
        assert "2019" in text
        assert not output.exists()
        _assert_no_outputs(tmp_path)


def test_paginate_count_mismatch_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        paginate, _score, _select, _boundaries = _contract()
        monkeypatch.chdir(tmp_path)
        schema = _Snapshot(_load_snapshot(), {"automated": {"yr": "2019"}})
        _install_fixed_pages(monkeypatch, _tiny_rows("2019", 6), count=5, page_size=3)
        output = tmp_path / "paged" / "coverage.parquet"
        code, message, _value = _call(
            paginate, _page_config(3), "automated", 2019, schema, output
        )
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}{message}"
        assert code == 2
        assert "automated" in text
        assert "2019" in text
        assert not output.exists()
        _assert_no_outputs(tmp_path)


def test_checkpoint_mismatch_leaves_the_year_folder_unchanged(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        paginate, _score, _select, _boundaries = _contract()
        monkeypatch.chdir(tmp_path)
        folder = tmp_path / "year"
        folder.mkdir()
        (folder / "pages.jsonl").write_text('{" :id": 1}\n')
        (folder / "checkpoint.json").write_text(
            json.dumps({"next_offset": 5, "row_count": 5, "page_hash": "abc"}) + "\n"
        )
        (folder / "keep.txt").write_text("stay")
        before = _fingerprint(folder)
        schema = _Snapshot(_load_snapshot(), {"automated": {"yr": "2019"}})
        _install_fixed_pages(monkeypatch, _tiny_rows("2019", 4), count=4, page_size=4)
        output = folder / "coverage.parquet"
        code, message, _value = _call(
            paginate, _page_config(4), "automated", 2019, schema, output
        )
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}{message}"
        assert code == 2
        assert "automated" in text
        assert "2019" in text
        assert _fingerprint(folder) == before
        assert not output.exists()
        _assert_no_outputs(tmp_path)


def _adequate_ids():
    return list(range(1, _sizes()["uhf"] + 1))


def _traffic_counts(period):
    sizes = _sizes()
    return {
        "raw_record_count": sizes["uhf"] * sizes["segments"] * sizes["dates"] * sizes["hours"] * 4,
        "distinct_segments": sizes["uhf"] * sizes["segments"],
        "n_uhf_with_traffic": sizes["uhf"],
        "n_rows": period["n_rows"],
    }


def test_scenario_a_prints_one_source_per_period(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        paginate, score_coverage, select_source, ensure_boundaries = _contract()
        assert callable(score_coverage) and callable(select_source) and callable(ensure_boundaries)
        periods_path = FIXTURES / "scenario_a" / "asthma_time_periods.csv"
        periods = _read_periods(periods_path)
        assert periods == [
            {"period": "2019", "kind": "year", "years": [2019], "n_rows": 7}
        ]
        snapshot = _load_snapshot()
        rows = _full_automated(2019)
        fixture_counts = _csv_table(FIXTURES / "scenario_a" / "traffic_year_counts.csv")[1]
        expected = int(
            next(
                row["record_count"]
                for row in fixture_counts
                if row["source"] == "automated" and row["year"] == "2019"
            )
        )
        assert len(rows) == expected
        catalog = _Catalog().add("automated", 2019, rows)
        code, text, repo, calls = _run_script(
            monkeypatch, capsys, tmp_path, periods_path, snapshot, catalog
        )
        assert code == 0, text
        _assert_terminal(text, "A", {"2019": "automated"}, [])
        script_text = (repo / "scripts" / "02_select_sources.py").read_text()
        for name in ("load_config", "ensure_boundaries", "paginate", "score_coverage", "select_source"):
            assert name in script_text
        _assert_selection(
            repo / "outputs" / "audit" / "source_selection.json",
            "A",
            None,
            {
                "2019": {
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            },
        )
        counts = _traffic_counts(periods[0])
        _assert_comparison(
            repo / "outputs" / "dataset_source_comparison.csv",
            {
                "2019": {
                    "automated_available": True,
                    "historical_available": False,
                    "aadt_available": False,
                    "recommended_source": "automated",
                    **counts,
                }
            },
        )
        _assert_coverage(
            repo / "outputs" / "audit" / "coverage_for_selection.csv",
            "2019",
            "automated",
            _adequate_ids(),
        )
        page_size = int(_config()["api"]["page_size"])
        _assert_paging(calls, snapshot, catalog, page_size)
        _assert_year_folder(repo, "automated", 2019, len(rows), page_size)
        assert not (repo / "data" / "raw" / "traffic" / "historical").exists()
        assert not (repo / "data" / "raw" / "traffic" / "aadt").exists()


def test_scenario_d_zero_included_periods_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        periods_path = FIXTURES / "scenario_a" / "asthma_time_periods.csv"
        periods = _read_periods(periods_path)
        snapshot = _load_snapshot()
        catalog = (
            _Catalog()
            .add("automated", 2019, [])
            .add("historical", 2019, [])
            .add("aadt", 2019, [])
        )
        code, text, repo, _calls = _run_script(
            monkeypatch, capsys, tmp_path, periods_path, snapshot, catalog
        )
        assert code == 2
        _assert_terminal(text, "D", {}, ["2019"])
        _assert_selection(
            repo / "outputs" / "audit" / "source_selection.json",
            "D",
            None,
            {"2019": {"source": "*", "years": [2019], "included": False, "reason": "*"}},
        )
        _assert_comparison(
            repo / "outputs" / "dataset_source_comparison.csv",
            {
                "2019": {
                    "automated_available": False,
                    "historical_available": False,
                    "aadt_available": False,
                    "recommended_source": "",
                    "raw_record_count": 0,
                    "distinct_segments": 0,
                    "n_uhf_with_traffic": 0,
                    "n_rows": periods[0]["n_rows"],
                }
            },
        )
        fieldnames, records = _csv_table(repo / "outputs" / "audit" / "coverage_for_selection.csv")
        assert tuple(fieldnames) == COVERAGE_COLUMNS
        assert all(not _as_bool(record["adequate"]) for record in records)


def test_rule_2_pass_uses_traffic_pearson_not_asthma_rates(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        periods_path = FIXTURES / "two_periods" / "asthma_time_periods.csv"
        periods = {item["period"]: item for item in _read_periods(periods_path)}
        sizes = _sizes()
        means = [float(uhf) for uhf in range(1, sizes["uhf"] + 1)]
        asthma = [_reversed(uhf) for uhf in range(1, sizes["uhf"] + 1)]
        traffic_r = _pearson(means, means)
        asthma_r = _pearson(means, asthma)
        assert traffic_r >= float(_config()["sources"]["mix_min_traffic_r"])
        assert asthma_r < 0
        snapshot = _load_snapshot()
        automated = _full_automated(2019, asthma_for=_reversed)
        historical_2019 = _full_historical(2019, asthma_for=_reversed)
        historical_2015 = _full_historical(2015, asthma_for=_reversed)
        catalog = (
            _Catalog()
            .add("automated", 2019, automated)
            .add("automated", 2015, [])
            .add("historical", 2019, historical_2019)
            .add("historical", 2015, historical_2015)
        )
        code, text, repo, calls = _run_script(
            monkeypatch, capsys, tmp_path, periods_path, snapshot, catalog
        )
        assert code == 0, text
        _assert_terminal(
            text,
            "2",
            {"2019": "automated", "2015": "historical"},
            [],
        )
        selection = _assert_selection(
            repo / "outputs" / "audit" / "source_selection.json",
            "2",
            (True, traffic_r, sizes["uhf"]),
            {
                "2019": {
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                },
                "2015": {
                    "source": "historical",
                    "years": [2015],
                    "included": True,
                    "reason": "",
                },
            },
        )
        assert abs(selection["comparability_r"] - asthma_r) > 0.5
        auto_counts = _traffic_counts(periods["2019"])
        hist_counts = {
            "raw_record_count": len(historical_2015),
            "distinct_segments": sizes["uhf"] * sizes["segments"],
            "n_uhf_with_traffic": sizes["uhf"],
            "n_rows": periods["2015"]["n_rows"],
        }
        _assert_comparison(
            repo / "outputs" / "dataset_source_comparison.csv",
            {
                "2019": {
                    "automated_available": True,
                    "historical_available": True,
                    "aadt_available": False,
                    "recommended_source": "automated",
                    **auto_counts,
                },
                "2015": {
                    "automated_available": False,
                    "historical_available": True,
                    "aadt_available": False,
                    "recommended_source": "historical",
                    **hist_counts,
                },
            },
        )
        page_size = int(_config()["api"]["page_size"])
        _assert_paging(calls, snapshot, catalog, page_size)
        assert all(call["source"] != "aadt" for call in calls)


def test_rule_2_fail_excludes_gaps_without_aadt(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        periods_path = FIXTURES / "two_periods" / "asthma_time_periods.csv"
        sizes = _sizes()
        means = [float(uhf) for uhf in range(1, sizes["uhf"] + 1)]
        asthma = list(means)
        historical_means = [_reversed(uhf) for uhf in range(1, sizes["uhf"] + 1)]
        traffic_r = _pearson(means, historical_means)
        asthma_r = _pearson(means, asthma)
        assert traffic_r < float(_config()["sources"]["mix_min_traffic_r"])
        assert asthma_r >= float(_config()["sources"]["mix_min_traffic_r"])
        snapshot = _load_snapshot()
        catalog = (
            _Catalog()
            .add("automated", 2019, _full_automated(2019, asthma_for=_identity))
            .add("automated", 2015, [])
            .add("historical", 2019, _full_historical(2019, _reversed, asthma_for=_identity))
            .add("historical", 2015, _full_historical(2015, _reversed, asthma_for=_identity))
        )
        code, text, repo, calls = _run_script(
            monkeypatch, capsys, tmp_path, periods_path, snapshot, catalog
        )
        assert code == 0, text
        _assert_terminal(text, "2", {"2019": "automated"}, ["2015"])
        _assert_paging(calls, snapshot, catalog, int(_config()["api"]["page_size"]))
        selection = _assert_selection(
            repo / "outputs" / "audit" / "source_selection.json",
            "2",
            (False, traffic_r, sizes["uhf"]),
            {
                "2019": {
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                },
                "2015": {
                    "source": "*",
                    "years": [2015],
                    "included": False,
                    "reason": "*",
                },
            },
        )
        assert abs(selection["comparability_r"] - asthma_r) > 0.5
        assert all(call["source"] != "aadt" for call in calls)
        _fieldnames, records = _csv_table(repo / "outputs" / "dataset_source_comparison.csv")
        by_period = {record["period"]: record for record in records}
        assert by_period["2015"]["recommended_source"].strip() == ""
        assert by_period["2019"]["recommended_source"].strip() == "automated"
        assert by_period["2015"]["n_matched"].strip() == ""
        assert by_period["2015"]["n_usable"].strip() == ""


def test_rule_3_historical_does_not_page_aadt(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        periods_path = FIXTURES / "scenario_a" / "asthma_time_periods.csv"
        periods = _read_periods(periods_path)[0]
        snapshot = _load_snapshot()
        historical = _full_historical(2019)
        catalog = _Catalog().add("automated", 2019, []).add("historical", 2019, historical)
        code, text, repo, calls = _run_script(
            monkeypatch, capsys, tmp_path, periods_path, snapshot, catalog
        )
        assert code == 0, text
        _assert_terminal(text, "3", {"2019": "historical"}, [])
        _assert_selection(
            repo / "outputs" / "audit" / "source_selection.json",
            "3",
            None,
            {
                "2019": {
                    "source": "historical",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            },
        )
        sizes = _sizes()
        _assert_comparison(
            repo / "outputs" / "dataset_source_comparison.csv",
            {
                "2019": {
                    "automated_available": False,
                    "historical_available": True,
                    "aadt_available": False,
                    "recommended_source": "historical",
                    "raw_record_count": len(historical),
                    "distinct_segments": sizes["uhf"] * sizes["segments"],
                    "n_uhf_with_traffic": sizes["uhf"],
                    "n_rows": periods["n_rows"],
                }
            },
        )
        _assert_paging(calls, snapshot, catalog, int(_config()["api"]["page_size"]))
        assert all(call["source"] != "aadt" for call in calls)
        assert not (repo / "data" / "raw" / "traffic" / "aadt").exists()


def test_scenario_4_aadt_is_not_mixed_with_another_source(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        periods_path = FIXTURES / "scenario_a" / "asthma_time_periods.csv"
        periods = _read_periods(periods_path)[0]
        snapshot = _load_snapshot()
        rows = _full_aadt(2019)
        catalog = (
            _Catalog()
            .add("automated", 2019, [])
            .add("historical", 2019, [])
            .add("aadt", 2019, rows)
        )
        code, text, repo, calls = _run_script(
            monkeypatch, capsys, tmp_path, periods_path, snapshot, catalog
        )
        assert code == 0, text
        _assert_terminal(text, "4", {"2019": "aadt"}, [])
        _assert_selection(
            repo / "outputs" / "audit" / "source_selection.json",
            "4",
            None,
            {
                "2019": {
                    "source": "aadt",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            },
        )
        sizes = _sizes()
        _assert_comparison(
            repo / "outputs" / "dataset_source_comparison.csv",
            {
                "2019": {
                    "automated_available": False,
                    "historical_available": False,
                    "aadt_available": True,
                    "recommended_source": "aadt",
                    "raw_record_count": len(rows),
                    "distinct_segments": sizes["uhf"] * sizes["segments"],
                    "n_uhf_with_traffic": sizes["uhf"],
                    "n_rows": periods["n_rows"],
                }
            },
        )
        _assert_paging(calls, snapshot, catalog, int(_config()["api"]["page_size"]))
        assert any(_token(call.get("select", ""), "aadt") for call in calls if call["kind"] == "page")
        included = {
            item["source"]
            for item in json.loads(
                (repo / "outputs" / "audit" / "source_selection.json").read_text()
            )["periods"]
            if item["included"]
        }
        assert included == {"aadt"}


def test_hourly_definition_unresolved_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        periods_path = FIXTURES / "scenario_a" / "asthma_time_periods.csv"
        snapshot = _load_snapshot()
        historical_columns = list(snapshot["historical"])
        hour_at = historical_columns.index("hour")
        historical_columns.insert(hour_at + 1, "minute")
        snapshot["historical"] = historical_columns
        bad = _historical_rows(
            2019,
            [(1, 6, [0])],
            _identity,
            minute=10,
        )
        catalog = (
            _Catalog()
            .add("automated", 2019, [])
            .add("historical", 2019, bad)
            .add("aadt", 2019, [])
        )
        code, text, _repo, _calls = _run_script(
            monkeypatch, capsys, tmp_path, periods_path, snapshot, catalog
        )
        assert code == 2
        assert HOUR_REASON in text
        assert "historical" in text
        assert "2019" in text


def test_bad_shapefile_exits_2_without_network(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        periods_path = FIXTURES / "scenario_a" / "asthma_time_periods.csv"
        snapshot = _load_snapshot()
        catalog = _Catalog()
        code, text, _repo, calls = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            periods_path,
            snapshot,
            catalog,
            geography="bad",
        )
        assert code == 2
        assert SHAPE_PHRASE in text.casefold()
        assert calls == []


def test_missing_source_txt_downloads_pinned_sha_and_reuses_it(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _paginate, _score, _select, ensure_boundaries = _contract()
        monkeypatch.chdir(tmp_path)
        _block_real_http(monkeypatch)
        blobs = _write_geography(tmp_path / "staging", N_COL * N_ROW)
        calls = []

        def send(self, prepared, **kwargs):
            decoded = urllib.parse.unquote(prepared.url).split("?")[0]
            calls.append(decoded)
            if decoded == COMMIT_URL:
                return _json_response({"sha": PINNED_SHA}, prepared.url)
            prefix = f"{RAW_ROOT}/{PINNED_SHA}/"
            if decoded.startswith(prefix):
                relative = decoded[len(prefix) :]
                if relative in blobs:
                    return _bytes_response(blobs[relative], prepared.url)
            raise AssertionError(f"unexpected geography URL {prepared.url}")

        monkeypatch.setattr("requests.sessions.Session.send", send)
        config = _config()
        assert config["geography"]["git_ref"] == "master"
        code, message, _value = _call(ensure_boundaries, config)
        captured = capsys.readouterr()
        assert code == 0, f"{message}{captured.out}{captured.err}"
        assert COMMIT_URL in calls
        for relative in blobs:
            assert f"{RAW_ROOT}/{PINNED_SHA}/{relative}" in calls
        geography = tmp_path / "data" / "raw" / "geography"
        assert (geography / "SOURCE.txt").read_text() == PINNED_SHA + "\n"
        for relative, payload in blobs.items():
            assert (geography / relative).read_bytes() == payload
        seen = len(calls)
        code, message, _value = _call(ensure_boundaries, config)
        captured = capsys.readouterr()
        assert code == 0, f"{message}{captured.out}{captured.err}"
        assert len(calls) == seen
        _assert_no_outputs(tmp_path)


def test_boundary_network_failure_exits_1(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        _block_real_http(monkeypatch)

        def send(self, prepared, **kwargs):
            raise requests.ConnectionError("down")

        monkeypatch.setattr("requests.sessions.Session.send", send)
        repo, script = _prepare_project(
            tmp_path,
            FIXTURES / "scenario_a" / "asthma_time_periods.csv",
            _load_snapshot(),
            {},
            geography="absent",
        )
        saved = _pop_lib_modules()
        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "path", _copy_path(repo))
        monkeypatch.setattr(sys, "argv", [str(script)])
        base = _config()
        base["api"]["retry_backoff_seconds"] = 0.0
        try:
            import lib.config as copied_config

            monkeypatch.setattr(
                copied_config,
                "load_config",
                lambda config=base: copy.deepcopy(config),
            )
            code = _invoke(script)
        finally:
            _restore_modules(saved)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert code == 1
        assert COMMIT_URL in text


def test_pooled_time_period_stays_one_period(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        periods_path = FIXTURES / "pooled" / "asthma_time_periods.csv"
        periods = _read_periods(periods_path)
        assert len(periods) == 1
        period = periods[0]
        assert period["kind"] == "pooled"
        snapshot = _load_snapshot()
        catalog = _Catalog()
        for year in period["years"]:
            catalog.add("automated", year, _full_automated(year))
        code, text, repo, calls = _run_script(
            monkeypatch, capsys, tmp_path, periods_path, snapshot, catalog
        )
        assert code == 0, text
        _assert_terminal(text, "A", {period["period"]: "automated"}, [])
        for year in period["years"]:
            assert f"{year} automated" not in text.splitlines()
        payload = _assert_selection(
            repo / "outputs" / "audit" / "source_selection.json",
            "A",
            None,
            {
                period["period"]: {
                    "source": "automated",
                    "years": period["years"],
                    "included": True,
                    "reason": "",
                }
            },
        )
        assert len(payload["periods"]) == 1
        _assert_comparison(
            repo / "outputs" / "dataset_source_comparison.csv",
            {
                period["period"]: {
                    "automated_available": True,
                    "historical_available": False,
                    "aadt_available": False,
                    "recommended_source": "automated",
                    "n_rows": period["n_rows"],
                    "n_uhf_with_traffic": _sizes()["uhf"],
                    "distinct_segments": _sizes()["uhf"] * _sizes()["segments"],
                    "raw_record_count": sum(len(rows) for rows in catalog.rows.values()),
                }
            },
        )
        fieldnames, records = _csv_table(repo / "outputs" / "audit" / "coverage_for_selection.csv")
        assert tuple(fieldnames) == COVERAGE_COLUMNS
        pooled_rows = [record for record in records if record["period"] == period["period"]]
        assert pooled_rows
        assert {record["period"] for record in records} == {period["period"]}
        _assert_paging(calls, snapshot, catalog, int(_config()["api"]["page_size"]))


def _expect_scenario_d(monkeypatch, capsys, tmp_path, rows):
    periods_path = FIXTURES / "scenario_a" / "asthma_time_periods.csv"
    periods = _read_periods(periods_path)[0]
    snapshot = _load_snapshot()
    catalog = (
        _Catalog()
        .add("automated", 2019, rows)
        .add("historical", 2019, [])
        .add("aadt", 2019, [])
    )
    code, text, repo, _calls = _run_script(
        monkeypatch, capsys, tmp_path, periods_path, snapshot, catalog
    )
    assert code == 2
    _assert_terminal(text, "D", {}, ["2019"])
    _assert_selection(
        repo / "outputs" / "audit" / "source_selection.json",
        "D",
        None,
        {"2019": {"source": "*", "years": [2019], "included": False, "reason": "*"}},
    )
    _fieldnames, records = _csv_table(repo / "outputs" / "dataset_source_comparison.csv")
    assert records[0]["recommended_source"].strip() == ""
    assert records[0]["n_matched"].strip() == ""
    assert records[0]["n_usable"].strip() == ""
    assert int(records[0]["n_uhf_with_asthma"]) == periods["n_rows"]
    assert _as_bool(records[0]["automated_available"]) is False


def test_three_quarters_are_not_a_complete_hour(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        sizes = _sizes()
        rows = _automated_rows(
            2019,
            _schedule(sizes["dates"], sizes["hours"]),
            _identity,
            quarters=(0, 15, 30),
        )
        _expect_scenario_d(monkeypatch, capsys, tmp_path, rows)


def test_one_observation_date_is_not_eligible(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        sizes = _sizes()
        rows = _automated_rows(2019, _schedule(1, sizes["hours"]), _identity)
        _expect_scenario_d(monkeypatch, capsys, tmp_path, rows)


def test_twenty_three_complete_hours_are_not_eligible(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        rows = _automated_rows(
            2019,
            [(1, 6, list(range(12))), (1, 7, list(range(11)))],
            _identity,
        )
        _expect_scenario_d(monkeypatch, capsys, tmp_path, rows)


def test_two_directions_keep_an_hour_only_when_both_are_present(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        _contract()
        sizes = _sizes()
        rows = _automated_rows(
            2019,
            _schedule(sizes["dates"], sizes["hours"]),
            _identity,
            extra_direction=True,
        )
        _expect_scenario_d(monkeypatch, capsys, tmp_path, rows)
