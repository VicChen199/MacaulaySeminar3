import copy
import csv
import json
import re
import runpy
import shutil
import sys
import urllib.parse
from pathlib import Path

import pytest
import requests

from lib.config import load_config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "audit_sources"
PERIODS_DIR = FIXTURES / "periods"
EMPTY_DIR = FIXTURES / "empty"
TARGETS = [2015, 2016, 2017, 2018, 2019, 2022, 2023]
SOURCES = ("automated", "historical", "aadt")
PERIOD_COLUMNS = ("period", "period_kind", "constituent_years", "n_rows")
COUNT_COLUMNS = ("source", "year", "record_count", "schema_ok")

# Kept rows are the period rule applied to tests/fixtures/audit_sources/periods.
# constituent_years is the inclusive range, ascending, with no spaces.
EXPECTED_PERIODS = {
    "2019": ("year", "2019", 2),
    "2015": ("year", "2015", 1),
    "2015-2017": ("pooled", "2015,2016,2017", 3),
    "2015 to 2016": ("pooled", "2015,2016", 1),
    "2022-2023": ("pooled", "2022,2023", 1),
    "2023": ("year", "2023", 1),
}
DROPPED_PERIODS = {
    "2020",
    "2021",
    "2019-2022",
    "2014",
    "Winter",
    "2015-2016-2017",
    "2016",
    "2017",
    "2018",
    "2022",
}
# First configured alias present in these live keys wins: automated has yr, not year.
LIVE_ROWS = {
    "automated": {"yr": "2015", "volume": "3"},
    "historical": {"year": "2015", "yr": "2015", "volume": "3"},
    "aadt": {"year": "2015", "aadt": "9"},
}
RESOLVED_YEAR_FIELD = {
    "automated": "yr",
    "historical": "year",
    "aadt": "year",
}
COUNT_RE = re.compile(r"count\s*\(\s*\*\s*\)", re.IGNORECASE)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _count_for(source, year):
    base = {"automated": 110000, "historical": 220000, "aadt": 330000}[source]
    return base + year


def _token(text, token):
    return re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
        text,
    ) is not None


def _years(text):
    return [int(match) for match in YEAR_RE.findall(text or "")]


def _param(query, *names):
    for name in names:
        values = query.get(name)
        if values:
            return " ".join(values)
    return ""


def _records(result, columns):
    if hasattr(result, "to_dict") and hasattr(result, "columns"):
        names = [str(column).strip() for column in result.columns]
        missing = [column for column in columns if column not in names]
        if missing:
            raise AssertionError(f"missing {missing}; expected columns {list(columns)}")
        frame = result.copy()
        frame.columns = names
        return frame.to_dict(orient="records")
    if isinstance(result, list) and (not result or isinstance(result[0], dict)):
        if result:
            missing = [column for column in columns if column not in result[0]]
            if missing:
                raise AssertionError(
                    f"missing {missing}; expected columns {list(columns)}"
                )
        return result
    raise AssertionError(
        "expected rows with columns "
        + ", ".join(columns)
        + f", got {type(result).__name__}"
    )


def _csv_records(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _schema_ok(value):
    if value == True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


def _period_table(records):
    got = {}
    for record in records:
        period = str(record["period"]).strip()
        assert period not in got, "a pooled TimePeriod stays one period"
        got[period] = (
            str(record["period_kind"]).strip(),
            str(record["constituent_years"]).strip(),
            int(record["n_rows"]),
        )
    assert got == EXPECTED_PERIODS, "a pooled TimePeriod stays one period"
    assert DROPPED_PERIODS.isdisjoint(got)


def _count_table(records):
    got = set()
    for record in records:
        source = str(record["source"]).strip()
        year = int(record["year"])
        key = (source, year)
        assert key not in got, "traffic_year_counts has one row per source per target year"
        assert int(record["record_count"]) == _count_for(source, year)
        assert _schema_ok(record["schema_ok"]), "schema_ok is true when the year field is found"
        got.add(key)
    expected = {(source, year) for source in SOURCES for year in TARGETS}
    assert got == expected


def _json_response(payload, url):
    response = requests.Response()
    response.status_code = 200
    response.reason = "OK"
    response.url = url
    response.encoding = "utf-8"
    response._content = json.dumps(payload).encode("utf-8")
    return response


def _install_socrata(monkeypatch):
    config = load_config()
    urls = {source: config["api"][source]["base_url"] for source in SOURCES}
    calls = []

    def send(self, prepared, **kwargs):
        decoded = urllib.parse.unquote(prepared.url)
        source = next(
            (name for name, base in urls.items() if decoded.startswith(base)),
            None,
        )
        if source is None:
            raise AssertionError(f"unexpected Socrata URL {prepared.url}")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(prepared.url).query)
        where = _param(query, "$where", "where")
        limit = _param(query, "$limit", "limit")
        calls.append(
            {
                "source": source,
                "url": decoded,
                "where": where,
                "limit": limit,
            }
        )
        years = _years(where or decoded)
        if limit == "1":
            payload = [LIVE_ROWS[source]]
        elif COUNT_RE.search(decoded) and len(years) == 1 and years[0] in TARGETS:
            payload = [{"count": str(_count_for(source, years[0]))}]
        else:
            field = RESOLVED_YEAR_FIELD[source]
            payload = [{field: str(year), "count": "1"} for year in TARGETS]
        return _json_response(payload, prepared.url)

    monkeypatch.setattr("requests.sessions.Session.send", send)
    return calls


def _forbid_socrata(monkeypatch):
    def send(self, prepared, **kwargs):
        raise AssertionError(f"fixture test must not call Socrata ({prepared.url})")

    monkeypatch.setattr("requests.sessions.Session.send", send)


def _assert_count_requests(calls):
    for call in calls:
        assert call["limit"] in ("", "1"), "traffic volumes are not paged"
        assert not _token(call["url"], "offset"), "traffic volumes are not paged"
    for source, field in RESOLVED_YEAR_FIELD.items():
        counted = []
        for call in calls:
            if call["source"] != source or not COUNT_RE.search(call["url"]):
                continue
            years = _years(call["where"] or call["url"])
            if len(years) != 1 or years[0] not in TARGETS:
                continue
            haystack = call["where"] or call["url"]
            assert _token(haystack, field), (
                f"{source} count(*) must use the first live year alias {field}"
            )
            if field == "yr":
                assert not _token(haystack, "year")
            counted.append(years[0])
        assert set(counted) == set(TARGETS), (
            f"{source} must request count(*) for every target year"
        )
        grouped = [
            call
            for call in calls
            if call["source"] == source and "group" in call["url"].lower()
        ]
        assert len(grouped) == 1, f"{source} must request one grouped year list"
        assert _token(grouped[0]["url"], field)


def _line_has_period(line, period):
    if period.isdigit():
        return re.search(rf"(?<!\d){re.escape(period)}(?!\d)", line) is not None
    return period in line


def _assert_terminal(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for period, (kind, _constituent, _nrows) in EXPECTED_PERIODS.items():
        matched = False
        for index, line in enumerate(lines):
            if not _line_has_period(line, period):
                continue
            nearby = line if index + 1 == len(lines) else f"{line} {lines[index + 1]}"
            if _token(nearby, kind):
                matched = True
                break
        assert matched, f"terminal should print period {period} as {kind}"
    for source in SOURCES:
        assert source in text
        for year in TARGETS:
            assert str(_count_for(source, year)) in text


def _existed():
    return {path: path.exists() for path in (ROOT / "outputs", ROOT / "data")}


def _cleanup(existed):
    created = []
    for path, was_there in existed.items():
        if path.exists() and not was_there:
            shutil.rmtree(path)
            created.append(path.name)
    return created


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


def _run_script(monkeypatch, tmp_path, raw_dir):
    """Run a copy of the audit script so writes stay off the repository tree."""
    script_src = ROOT / "scripts" / "01_audit_sources.py"
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    script = repo / "scripts" / "01_audit_sources.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_src, script)
    base = copy.deepcopy(load_config())
    base["asthma"]["raw_dir"] = str(raw_dir)
    saved = _pop_lib_modules()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "path", _copy_path(repo))
    monkeypatch.setattr(sys, "argv", [str(script)])
    try:
        import lib.config as copied_config

        monkeypatch.setattr(
            copied_config,
            "load_config",
            lambda config=base: copy.deepcopy(config),
        )
        calls = _install_socrata(monkeypatch)
        code = _invoke(script)
        return code, repo, calls
    finally:
        _restore_modules(saved)


def test_pooled_time_period_stays_one_period(monkeypatch, tmp_path):
    existed = _existed()
    monkeypatch.chdir(tmp_path)
    _forbid_socrata(monkeypatch)
    config = copy.deepcopy(load_config())
    config["asthma"]["raw_dir"] = str(PERIODS_DIR)
    monkeypatch.setattr(
        "lib.config.load_config",
        lambda config=config: copy.deepcopy(config),
    )
    try:
        from lib.audit import inventory_asthma

        result = inventory_asthma(str(PERIODS_DIR))
        _period_table(_records(result, PERIOD_COLUMNS))
        assert not (tmp_path / "outputs").exists(), (
            "inventory_asthma on a fixture must not write outputs/"
        )
    finally:
        created = _cleanup(existed)
    assert not created, "inventory_asthma on a fixture must not write outputs/"


def test_empty_export_exits_2(monkeypatch, capsys, tmp_path):
    script = ROOT / "scripts" / "01_audit_sources.py"
    if not script.is_file():
        pytest.fail(
            "scripts/01_audit_sources.py is missing; an empty export "
            "(headers and zero rows) must print that the export has no rows and exit 2"
        )
    existed = _existed()
    try:
        code, _repo, _calls = _run_script(monkeypatch, tmp_path, EMPTY_DIR)
        captured = capsys.readouterr()
        message = f"{captured.out}{captured.err}"
        assert code == 2
        assert "has no rows" in message.lower()
    finally:
        created = _cleanup(existed)
    assert not created, "fixture test must not write the repository outputs/ tree"


def test_year_counts_from_mocked_count(monkeypatch, tmp_path):
    existed = _existed()
    monkeypatch.chdir(tmp_path)
    calls = _install_socrata(monkeypatch)
    try:
        from lib.socrata import fetch_year_counts

        result = fetch_year_counts(load_config(), LIVE_ROWS)
        _assert_count_requests(calls)
        _count_table(_records(result, COUNT_COLUMNS))
    finally:
        created = _cleanup(existed)
    assert not created, "fetch_year_counts on a fixture must not write outputs/"


def test_fixture_audit_prints_period_kinds_and_year_counts(monkeypatch, capsys, tmp_path):
    script = ROOT / "scripts" / "01_audit_sources.py"
    if not script.is_file():
        pytest.fail(
            "scripts/01_audit_sources.py is missing; a fixture audit must print "
            "each period kind and the three API year counts and exit 0"
        )
    existed = _existed()
    try:
        code, repo, calls = _run_script(monkeypatch, tmp_path, PERIODS_DIR)
        captured = capsys.readouterr()
        assert code == 0
        _assert_terminal(f"{captured.out}{captured.err}")
        _assert_count_requests(calls)
        for source in SOURCES:
            assert any(
                call["source"] == source and call["limit"] == "1" for call in calls
            ), f"fetch_schema must request $limit=1 from {source}"
        audit = repo / "outputs" / "audit"
        _period_table(_csv_records(audit / "asthma_time_periods.csv"))
        _count_table(_csv_records(audit / "traffic_year_counts.csv"))
        snapshot = json.loads((audit / "schema_snapshot.json").read_text())
        assert set(snapshot) == set(SOURCES)
        for source, field in RESOLVED_YEAR_FIELD.items():
            assert field in json.dumps(snapshot[source])
    finally:
        created = _cleanup(existed)
    assert not created, "fixture test must not write the repository outputs/ tree"
