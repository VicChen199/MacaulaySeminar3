"""Contract tests for paging selected traffic years into records.parquet.

The download script is absent in this worktree. These tests lock the page
request, the manifest beside records.parquet, and the download log.
"""

import copy
import csv
import hashlib
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

import pandas as pd
import pytest
import requests

from lib.config import load_config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "download_traffic"
SCHEMA_PATH = FIXTURES / "schema_snapshot.json"
SCRIPT_NAME = "scripts/03_download_traffic.py"
LOG_COLUMNS = (
    "source",
    "year",
    "expected_count",
    "downloaded_count",
    "duplicate_ids",
    "parquet_sha256",
)
MANIFEST_KEYS = ("row_count", "min_id", "max_id", "parquet_sha256")
COUNT_RE = re.compile(r"count\s*\(\s*\*\s*\)", re.IGNORECASE)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
# Sample type of the resolved year field: automated yr is text, historical year is numeric.
QUOTED_YEAR = {"automated": True, "historical": False, "aadt": True}
YEAR_FIELD = {"automated": "yr", "historical": "year", "aadt": "year"}


def _require_script():
    script = ROOT / SCRIPT_NAME
    if not script.is_file():
        pytest.fail(
            "scripts/03_download_traffic.py is missing; "
            "each included source-year must be paged into records.parquet "
            "and manifest.json, and outputs/audit/download_log.csv must "
            "record that year. A duplicate :id must exit 2 with no records.parquet."
        )
    return script


def _config(page_size=2, max_retries=5, backoff=0.0):
    config = copy.deepcopy(load_config())
    config["api"]["page_size"] = int(page_size)
    config["api"]["max_retries"] = int(max_retries)
    config["api"]["retry_backoff_seconds"] = float(backoff)
    config["api"]["timeout_seconds"] = 5
    assert config["api"]["order_field"] == ":id"
    return config


def _load_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert set(schema) == {"automated", "historical", "aadt"}
    assert isinstance(schema["automated"]["yr"], str)
    assert type(schema["historical"]["year"]) is int
    return schema


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _selection(periods, scenario="A"):
    return {
        "scenario": scenario,
        "mix_used": False,
        "comparability_pass": None,
        "comparability_r": None,
        "comparability_n": None,
        "periods": periods,
    }


def _token(text, token):
    return (
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(str(token))}(?![A-Za-z0-9_])",
            text or "",
        )
        is not None
    )


def _number_token(text, number):
    return re.search(rf"(?<!\d){int(number)}(?!\d)", text or "") is not None


def _automated_row(year, identity, minute="0"):
    return {
        ":id": int(identity),
        "yr": str(year),
        "m": "6",
        "d": "1",
        "hh": "0",
        "mm": str(minute),
        "vol": "1",
        "segmentid": "seg",
        "wktgeom": "POINT(-74.01 40.71)",
        "direction": "NB",
        "boro": "Manhattan",
    }


def _historical_row(year, identity):
    return {
        ":id": int(identity),
        "year": int(year),
        "month": 6,
        "day": 1,
        "hour": 0,
        "volume": 1,
        "segment": "seg",
        "wkt": "POINT(-74.01 40.71)",
        "direction": "NB",
        "borough": "Manhattan",
    }


def _rows_for(source, year, identities):
    rows = []
    for index, identity in enumerate(identities):
        if source == "historical":
            rows.append(_historical_row(year, identity))
        else:
            rows.append(_automated_row(year, identity, minute=str(15 * index)))
    return rows


def _query(url):
    parsed = urllib.parse.urlsplit(url)
    raw = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    def value(*names):
        for name in names:
            found = raw.get(name)
            if found:
                return found[0]
        return ""

    return urllib.parse.unquote(url), {
        "where": value("$where", "where"),
        "limit": value("$limit", "limit"),
        "offset": value("$offset", "offset"),
        "order": value("$order", "order"),
        "select": value("$select", "select"),
    }


def _years_in(text):
    return [int(match) for match in YEAR_RE.findall(text or "")]


def _json_response(payload, url, status=200):
    response = requests.Response()
    response.status_code = status
    response.reason = "OK" if status == 200 else "ERROR"
    response.url = url
    response.encoding = "utf-8"
    response._content = json.dumps(payload).encode("utf-8")
    return response


def _line_count(path):
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _fingerprint(directory):
    found = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            found[path.relative_to(directory).as_posix()] = path.read_bytes()
    return found


def _year_dir(repo, source, year):
    return repo / "data" / "raw" / "traffic" / source / f"year={int(year)}"


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


def _write_api(toml_path, config):
    api = config["api"]
    replacements = {
        "page_size": str(int(api["page_size"])),
        "timeout_seconds": str(int(api["timeout_seconds"])),
        "max_retries": str(int(api["max_retries"])),
        "retry_backoff_seconds": str(float(api["retry_backoff_seconds"])),
    }
    lines = []
    for raw in toml_path.read_text(encoding="utf-8").splitlines():
        key = raw.split("=", 1)[0].strip()
        if key in replacements:
            lines.append(f"{key} = {replacements[key]}")
        else:
            lines.append(raw)
    toml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepare(tmp_path, selection, config):
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    script_src = ROOT / SCRIPT_NAME
    script = repo / "scripts" / "03_download_traffic.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    if script_src.is_file():
        shutil.copy2(script_src, script)
    _write_api(repo / "config" / "pipeline.toml", config)
    audit = repo / "outputs" / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "source_selection.json").write_text(
        json.dumps(selection, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(SCHEMA_PATH, audit / "schema_snapshot.json")
    return repo, script


class _Catalog:
    def __init__(self):
        self.rows = {}
        self.counts = {}

    def add(self, source, year, rows, count=None):
        key = (source, int(year))
        self.rows[key] = list(rows)
        self.counts[key] = len(rows) if count is None else int(count)
        return self


def _install_http(
    monkeypatch,
    catalog,
    config,
    *,
    error_status=None,
    error_times=0,
    retry_after=None,
    short_second=False,
    max_calls=24,
):
    def urlopen(*args, **kwargs):
        raise AssertionError("fixture test must not call Socrata")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    slept = []

    def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(time, "sleep", sleep)
    bases = {source: config["api"][source]["base_url"] for source in YEAR_FIELD}
    calls = []
    seen = {"n": 0}

    def send(self, prepared, **kwargs):
        seen["n"] += 1
        if seen["n"] > max_calls:
            raise AssertionError("pagination retries did not stop")
        decoded, query = _query(prepared.url)
        if "github.com" in decoded or "githubusercontent.com" in decoded:
            raise AssertionError(f"fixture test must not call Socrata ({prepared.url})")
        source = next((name for name, base in bases.items() if decoded.startswith(base)), None)
        if source is None:
            raise AssertionError(f"fixture test must not call Socrata ({prepared.url})")
        status = 200
        if error_status is not None and seen["n"] <= error_times:
            status = error_status
        if status != 200:
            calls.append(
                {
                    "source": source,
                    "kind": "error",
                    "status": status,
                    "url": decoded,
                    "where": query["where"],
                }
            )
            response = _json_response({"message": "retry"}, prepared.url, status=status)
            if retry_after is not None:
                response.headers["Retry-After"] = str(retry_after)
            return response
        years = _years_in(query["where"] or decoded)
        if len(years) != 1:
            raise AssertionError(f"$where must name one constituent year ({decoded})")
        year = years[0]
        key = (source, year)
        if key not in catalog.rows:
            raise AssertionError(
                f"must not page {source} {year}; only included constituent years "
                "of that period's source are paged"
            )
        _assert_where(query["where"], source, year)
        if COUNT_RE.search(query["select"] or decoded):
            calls.append(
                {
                    "source": source,
                    "year": year,
                    "kind": "count",
                    "status": 200,
                    "where": query["where"],
                    "select": query["select"],
                    "url": decoded,
                }
            )
            return _json_response(
                [{"count": str(catalog.counts[key])}],
                prepared.url,
            )
        if query["offset"] == "":
            raise AssertionError("$offset starts at 0")
        offset = int(query["offset"])
        limit = int(query["limit"] or "0")
        page = catalog.rows[key][offset : offset + limit]
        if short_second and offset > 0:
            page = page[:1]
        calls.append(
            {
                "source": source,
                "year": year,
                "kind": "page",
                "status": 200,
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
    return calls, slept


def _assert_where(where, source, year):
    field = YEAR_FIELD[source]
    if QUOTED_YEAR[source]:
        assert re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(field)}\s*=\s*'{int(year)}'",
            where or "",
        ), where
        return
    assert re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(field)}\s*=\s*{int(year)}\b",
        where or "",
    ), where
    assert not re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(field)}\s*=\s*'{int(year)}'",
        where or "",
    ), where


def _plant_coverage(repo, source, year):
    folder = _year_dir(repo, source, year)
    _write_parquet(folder / "coverage.parquet", pd.DataFrame({":id": [999], "yr": ["2019"]}))
    return (folder / "coverage.parquet").read_bytes()


def _run(monkeypatch, capsys, tmp_path, selection, catalog, config, before=None, **http):
    _require_script()
    root_toml = (ROOT / "config" / "pipeline.toml").read_bytes()
    repo, script = _prepare(tmp_path, selection, config)
    if before is not None:
        before(repo)
    saved = _pop_lib_modules()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "path", _copy_path(repo))
    monkeypatch.setattr(sys, "argv", [str(script)])
    calls, slept = _install_http(monkeypatch, catalog, config, **http)
    try:
        import lib.config as copied_config

        monkeypatch.setattr(
            copied_config,
            "load_config",
            lambda config=config: copy.deepcopy(config),
        )
        assert sys.argv == [str(script)], (
            "python scripts/03_download_traffic.py takes no arguments"
        )
        code = _invoke(script)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert (ROOT / "config" / "pipeline.toml").read_bytes() == root_toml
        return code, text, repo, calls, slept
    finally:
        _restore_modules(saved)


def _data_calls(calls):
    return [call for call in calls if call["kind"] in {"count", "page"}]


def _blocks(calls):
    blocks = []
    for call in _data_calls(calls):
        key = (call["source"], call["year"])
        if not blocks or blocks[-1][0] != key:
            blocks.append((key, []))
        blocks[-1][1].append(call)
    return blocks


def _assert_paging(calls, schema, expected, page_size, starts=None):
    blocks = _blocks(calls)
    assert [key for key, _group in blocks] == list(expected), (
        "one year file is paged at a time, in selection order"
    )
    starts = starts or {}
    for key, group in blocks:
        source, year = key
        kinds = [item["kind"] for item in group]
        assert kinds[0] == "count", f"{source} {year} must request count(*) before pages"
        assert kinds.count("count") == 1
        pages = [item for item in group if item["kind"] == "page"]
        row_count = expected[key]
        start = int(starts.get(key, 0))
        offsets = []
        cursor = start
        while cursor < row_count:
            offsets.append(cursor)
            step = min(page_size, row_count - cursor)
            cursor += step
        assert [page["offset"] for page in pages] == offsets
        assert [page["returned"] for page in pages] == [
            min(page_size, row_count - offset) for offset in offsets
        ]
        for previous, nxt in zip(pages, pages[1:]):
            assert nxt["offset"] == previous["offset"] + previous["returned"], (
                "offset advances by the number of rows in the page"
            )
        for page in pages:
            assert page["limit"] == str(page_size)
            assert page["order"] == ":id"
            assert _token(page["select"], ":id"), page["select"]
            for field in schema[source]:
                assert _token(page["select"], field), page["select"]


def _assert_print(text, rows):
    lines = [line.strip() for line in text.splitlines()]
    for source, year, expected, downloaded in rows:
        assert f"{source} {year} {expected} {downloaded}" in lines


def _read_ids(path):
    frame = pd.read_parquet(path)
    assert ":id" in list(frame.columns)
    return [int(value) for value in frame[":id"].tolist()]


def _assert_manifest(folder, ids):
    parquet = folder / "records.parquet"
    manifest_path = folder / "manifest.json"
    assert parquet.is_file()
    assert manifest_path.is_file()
    assert not (folder / "pages.jsonl").exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == set(MANIFEST_KEYS)
    assert type(manifest["row_count"]) is int
    assert manifest["row_count"] == len(ids)
    assert type(manifest["min_id"]) is int
    assert type(manifest["max_id"]) is int
    assert manifest["min_id"] == min(ids)
    assert manifest["max_id"] == max(ids)
    digest = hashlib.sha256(parquet.read_bytes()).hexdigest()
    assert manifest["parquet_sha256"] == digest
    got = _read_ids(parquet)
    assert sorted(got) == sorted(ids)
    checkpoint = json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))
    assert int(checkpoint["row_count"]) == len(ids)
    assert int(checkpoint["next_offset"]) == len(ids)
    assert isinstance(checkpoint["page_hash"], str) and checkpoint["page_hash"]
    return manifest


def _read_log(repo):
    path = repo / "outputs" / "audit" / "download_log.csv"
    assert path.is_file(), "outputs/audit/download_log.csv has one row per year"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or []) == LOG_COLUMNS
        return list(reader)


def _assert_log(repo, expected_rows, manifests):
    records = _read_log(repo)
    assert len(records) == len(expected_rows)
    got = {}
    for record in records:
        key = (record["source"].strip(), int(record["year"]))
        assert key not in got
        got[key] = record
    assert set(got) == set(expected_rows)
    for key, (expected, downloaded, identities) in expected_rows.items():
        record = got[key]
        assert int(record["expected_count"]) == expected
        assert int(record["downloaded_count"]) == downloaded
        assert int(record["duplicate_ids"]) == 0
        assert record["parquet_sha256"].strip() == manifests[key]["parquet_sha256"]
        assert downloaded == len(identities)


def _assert_no_year_output(repo, source, year):
    folder = _year_dir(repo, source, year)
    assert not (folder / "records.parquet").exists()
    assert not (folder / "manifest.json").exists()
    log = repo / "outputs" / "audit" / "download_log.csv"
    if not log.is_file():
        return
    with log.open(newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            assert not (
                record["source"].strip() == source and int(record["year"]) == int(year)
            )


def _assert_failure_counts(text, year, *counts):
    assert _number_token(text, year), text
    for count in counts:
        assert _number_token(text, count), text


def _write_parquet(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def test_included_years_print_counts_and_write_manifest(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        schema = _load_schema()
        selection = _load_json(FIXTURES / "included" / "source_selection.json")
        automated_ids = [2, 9, 10]
        historical_ids = [3, 10]
        catalog = (
            _Catalog()
            .add("automated", 2019, _rows_for("automated", 2019, automated_ids))
            .add("historical", 2015, _rows_for("historical", 2015, historical_ids))
        )
        page_size = 2
        code, text, repo, calls, _slept = _run(
            monkeypatch,
            capsys,
            tmp_path,
            selection,
            catalog,
            _config(page_size=page_size),
        )
        assert code == 0, text
        _assert_print(
            text,
            (
                ("automated", 2019, 3, 3),
                ("historical", 2015, 2, 2),
            ),
        )
        assert "aadt 2018" not in text
        _assert_paging(
            calls,
            schema,
            {("automated", 2019): 3, ("historical", 2015): 2},
            page_size,
        )
        manifests = {
            ("automated", 2019): _assert_manifest(
                _year_dir(repo, "automated", 2019), automated_ids
            ),
            ("historical", 2015): _assert_manifest(
                _year_dir(repo, "historical", 2015), historical_ids
            ),
        }
        _assert_log(
            repo,
            {
                ("automated", 2019): (3, 3, automated_ids),
                ("historical", 2015): (2, 2, historical_ids),
            },
            manifests,
        )
        assert not _year_dir(repo, "aadt", 2018).exists()
        assert not (repo / "data" / "raw" / "traffic" / "aadt").exists()


def test_pooled_period_writes_one_parquet_per_constituent_year(
    monkeypatch, capsys, tmp_path
):
    with _guard_repo():
        schema = _load_schema()
        selection = _load_json(FIXTURES / "pooled" / "source_selection.json")
        assert len(selection["periods"]) == 1
        assert selection["periods"][0]["years"] == [2017, 2016]
        ids = [2, 9, 10]
        catalog = _Catalog()
        for year in (2017, 2016):
            catalog.add("automated", year, _rows_for("automated", year, ids))
        page_size = 2
        code, text, repo, calls, _slept = _run(
            monkeypatch,
            capsys,
            tmp_path,
            selection,
            catalog,
            _config(page_size=page_size),
        )
        assert code == 0, text
        _assert_print(
            text,
            (
                ("automated", 2017, 3, 3),
                ("automated", 2016, 3, 3),
            ),
        )
        _assert_paging(
            calls,
            schema,
            {("automated", 2017): 3, ("automated", 2016): 3},
            page_size,
        )
        manifests = {}
        for year in (2017, 2016):
            manifests[("automated", year)] = _assert_manifest(
                _year_dir(repo, "automated", year), ids
            )
        _assert_log(
            repo,
            {
                ("automated", 2017): (3, 3, ids),
                ("automated", 2016): (3, 3, ids),
            },
            manifests,
        )
        written = _load_json(repo / "outputs" / "audit" / "source_selection.json")
        assert len(written["periods"]) == 1
        assert written["periods"][0]["period"] == "2016-2017"
        assert written["periods"][0]["source"] == "automated"
        assert written["periods"][0]["years"] == [2017, 2016]
        assert not (repo / "data" / "raw" / "traffic" / "historical").exists()


def test_duplicate_id_blocks_compaction(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        rows = _load_json(FIXTURES / "duplicate" / "rows.json")
        identities = [row[":id"] for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        assert duplicate_count == 1
        selection = _load_json(FIXTURES / "duplicate" / "source_selection.json")
        catalog = _Catalog().add("automated", 2019, rows)
        planted = {}

        def before(repo):
            planted["bytes"] = _plant_coverage(repo, "automated", 2019)

        code, text, repo, _calls, _slept = _run(
            monkeypatch,
            capsys,
            tmp_path,
            selection,
            catalog,
            _config(page_size=8),
            before=before,
        )
        folder = _year_dir(repo, "automated", 2019)
        assert code == 2, text
        _assert_failure_counts(text, 2019, 4, duplicate_count)
        assert not (folder / "records.parquet").exists()
        assert not (folder / "manifest.json").exists()
        pages = folder / "pages.jsonl"
        assert pages.is_file(), "pages.jsonl is left in place"
        assert _line_count(pages) == len(rows)
        assert (folder / "coverage.parquet").read_bytes() == planted["bytes"]
        assert _read_ids(folder / "coverage.parquet") == [999]
        _assert_no_year_output(repo, "automated", 2019)
        assert not (tmp_path / "outputs").exists()
        assert not (FIXTURES / "outputs").exists()
        assert not (FIXTURES / "data").exists()


def test_count_mismatch_exits_2_without_records(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        ids = [21, 22, 23, 24, 25, 26]
        rows = _rows_for("automated", 2019, ids)
        selection = _selection(
            [
                {
                    "period": "2019",
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            ]
        )
        catalog = _Catalog().add("automated", 2019, rows, count=5)
        planted = {}

        def before(repo):
            planted["bytes"] = _plant_coverage(repo, "automated", 2019)

        code, text, repo, _calls, _slept = _run(
            monkeypatch,
            capsys,
            tmp_path,
            selection,
            catalog,
            _config(page_size=3),
            before=before,
        )
        folder = _year_dir(repo, "automated", 2019)
        assert code == 2, text
        _assert_failure_counts(text, 2019, 5, 6)
        assert not (folder / "records.parquet").exists()
        assert not (folder / "manifest.json").exists()
        pages = folder / "pages.jsonl"
        assert pages.is_file(), "pages.jsonl is left in place"
        assert _line_count(pages) == 6
        assert (folder / "coverage.parquet").read_bytes() == planted["bytes"]
        _assert_no_year_output(repo, "automated", 2019)


def test_short_page_before_count_exits_2(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        ids = [11, 12, 13, 14, 15, 16]
        rows = _rows_for("automated", 2019, ids)
        selection = _selection(
            [
                {
                    "period": "2019",
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            ]
        )
        catalog = _Catalog().add("automated", 2019, rows, count=6)
        planted = {}

        def before(repo):
            planted["bytes"] = _plant_coverage(repo, "automated", 2019)

        code, text, repo, _calls, _slept = _run(
            monkeypatch,
            capsys,
            tmp_path,
            selection,
            catalog,
            _config(page_size=4),
            before=before,
            short_second=True,
        )
        folder = _year_dir(repo, "automated", 2019)
        assert code == 2, text
        _assert_failure_counts(text, 2019, 6, 5)
        assert not (folder / "records.parquet").exists()
        assert not (folder / "manifest.json").exists()
        pages = folder / "pages.jsonl"
        assert pages.is_file(), "pages.jsonl is left in place"
        assert _line_count(pages) in {4, 5}
        assert (folder / "coverage.parquet").read_bytes() == planted["bytes"]
        _assert_no_year_output(repo, "automated", 2019)


def test_checkpoint_mismatch_leaves_the_year_folder_unchanged(
    monkeypatch, capsys, tmp_path
):
    with _guard_repo():
        selection = _selection(
            [
                {
                    "period": "2019",
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            ]
        )
        catalog = _Catalog().add(
            "automated", 2019, _rows_for("automated", 2019, [2, 9, 10])
        )
        repo, script = _prepare(tmp_path, selection, _config(page_size=3))
        folder = _year_dir(repo, "automated", 2019)
        folder.mkdir(parents=True)
        pages = "\n".join(json.dumps({":id": index}) for index in (31, 32, 33, 34)) + "\n"
        (folder / "pages.jsonl").write_text(pages, encoding="utf-8")
        (folder / "checkpoint.json").write_text(
            json.dumps({"next_offset": 28, "row_count": 17, "page_hash": "abc"}) + "\n",
            encoding="utf-8",
        )
        _write_parquet(
            folder / "coverage.parquet",
            pd.DataFrame({":id": [999], "yr": ["2019"]}),
        )
        (folder / "keep.txt").write_text("stay", encoding="utf-8")
        before = _fingerprint(folder)
        saved = _pop_lib_modules()
        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "path", _copy_path(repo))
        monkeypatch.setattr(sys, "argv", [str(script)])
        _install_http(monkeypatch, catalog, _config(page_size=3))
        try:
            _require_script()
            import lib.config as copied_config

            config = _config(page_size=3)
            monkeypatch.setattr(
                copied_config,
                "load_config",
                lambda config=config: copy.deepcopy(config),
            )
            code = _invoke(script)
        finally:
            _restore_modules(saved)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert code == 2, text
        _assert_failure_counts(text, 2019, 4, 17)
        assert _fingerprint(folder) == before
        assert not (folder / "records.parquet").exists()
        assert not (folder / "manifest.json").exists()


def test_resume_continues_from_next_offset(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        schema = _load_schema()
        ids = [2, 3, 4, 9, 10]
        rows = _rows_for("automated", 2019, ids)
        selection = _selection(
            [
                {
                    "period": "2019",
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            ]
        )
        page_size = 2
        config = _config(page_size=page_size)
        catalog = _Catalog().add("automated", 2019, rows)
        repo, script = _prepare(tmp_path, selection, config)
        folder = _year_dir(repo, "automated", 2019)
        folder.mkdir(parents=True)
        seeded = rows[:page_size]
        (folder / "pages.jsonl").write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in seeded),
            encoding="utf-8",
        )
        (folder / "checkpoint.json").write_text(
            json.dumps(
                {"next_offset": page_size, "row_count": page_size, "page_hash": "pending"}
            )
            + "\n",
            encoding="utf-8",
        )
        assert _line_count(folder / "pages.jsonl") == page_size
        saved = _pop_lib_modules()
        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "path", _copy_path(repo))
        monkeypatch.setattr(sys, "argv", [str(script)])
        calls, _slept = _install_http(monkeypatch, catalog, config)
        try:
            _require_script()
            import lib.config as copied_config

            monkeypatch.setattr(
                copied_config,
                "load_config",
                lambda config=config: copy.deepcopy(config),
            )
            code = _invoke(script)
        finally:
            _restore_modules(saved)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert code == 0, text
        _assert_print(text, (("automated", 2019, 5, 5),))
        _assert_paging(
            calls,
            schema,
            {("automated", 2019): 5},
            page_size,
            starts={("automated", 2019): page_size},
        )
        manifest = _assert_manifest(folder, ids)
        _assert_log(repo, {("automated", 2019): (5, 5, ids)}, {("automated", 2019): manifest})
        assert any(
            call["kind"] == "count" and call["year"] == 2019 for call in calls
        )


def test_completed_year_is_downloaded_again(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        schema = _load_schema()
        ids = [2, 9, 10]
        rows = _rows_for("automated", 2019, ids)
        selection = _selection(
            [
                {
                    "period": "2019",
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            ]
        )
        page_size = 2
        config = _config(page_size=page_size)
        catalog = _Catalog().add("automated", 2019, rows)
        repo, script = _prepare(tmp_path, selection, config)
        folder = _year_dir(repo, "automated", 2019)
        _write_parquet(folder / "records.parquet", pd.DataFrame({":id": [100], "yr": ["2019"]}))
        _write_parquet(
            folder / "coverage.parquet",
            pd.DataFrame({":id": [999], "yr": ["2019"]}),
        )
        (folder / "checkpoint.json").write_text(
            json.dumps({"next_offset": 1, "row_count": 1, "page_hash": "finished"}) + "\n",
            encoding="utf-8",
        )
        assert not (folder / "pages.jsonl").exists()
        previous = (folder / "records.parquet").read_bytes()
        coverage_bytes = (folder / "coverage.parquet").read_bytes()
        saved = _pop_lib_modules()
        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "path", _copy_path(repo))
        monkeypatch.setattr(sys, "argv", [str(script)])
        calls, _slept = _install_http(monkeypatch, catalog, config)
        try:
            _require_script()
            import lib.config as copied_config

            monkeypatch.setattr(
                copied_config,
                "load_config",
                lambda config=config: copy.deepcopy(config),
            )
            code = _invoke(script)
        finally:
            _restore_modules(saved)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert code == 0, text
        assert any(call["kind"] == "count" and call["year"] == 2019 for call in calls), (
            "a count request happens even when records.parquet already exists"
        )
        _assert_paging(calls, schema, {("automated", 2019): 3}, page_size)
        _assert_print(text, (("automated", 2019, 3, 3),))
        manifest = _assert_manifest(folder, ids)
        assert (folder / "records.parquet").read_bytes() != previous
        assert (folder / "coverage.parquet").read_bytes() == coverage_bytes
        assert 999 not in _read_ids(folder / "records.parquet")
        assert 100 not in _read_ids(folder / "records.parquet")
        _assert_log(repo, {("automated", 2019): (3, 3, ids)}, {("automated", 2019): manifest})


def test_http_400_exits_1_and_prints_the_url(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        config = _config(page_size=2)
        selection = _selection(
            [
                {
                    "period": "2019",
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            ]
        )
        catalog = _Catalog().add(
            "automated", 2019, _rows_for("automated", 2019, [2, 9])
        )
        code, text, repo, calls, slept = _run(
            monkeypatch,
            capsys,
            tmp_path,
            selection,
            catalog,
            config,
            error_status=400,
            error_times=1,
        )
        assert code == 1, text
        assert config["api"]["automated"]["base_url"] in text
        assert len(calls) == 1
        assert calls[0]["status"] == 400
        assert slept == []
        _assert_no_year_output(repo, "automated", 2019)


def test_http_429_retries_then_succeeds(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        schema = _load_schema()
        ids = [2, 10]
        config = _config(page_size=2, backoff=0.0)
        selection = _selection(
            [
                {
                    "period": "2019",
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            ]
        )
        catalog = _Catalog().add("automated", 2019, _rows_for("automated", 2019, ids))
        code, text, repo, calls, slept = _run(
            monkeypatch,
            capsys,
            tmp_path,
            selection,
            catalog,
            config,
            error_status=429,
            error_times=1,
            retry_after=6,
        )
        assert code == 0, text
        assert any(call["status"] == 429 for call in calls)
        assert any(float(delay) >= 6 for delay in slept)
        _assert_print(text, (("automated", 2019, 2, 2),))
        _assert_paging(calls, schema, {("automated", 2019): 2}, 2)
        manifest = _assert_manifest(_year_dir(repo, "automated", 2019), ids)
        _assert_log(repo, {("automated", 2019): (2, 2, ids)}, {("automated", 2019): manifest})


def test_http_5xx_retries_then_succeeds(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        schema = _load_schema()
        ids = [2, 10]
        config = _config(page_size=2, backoff=0.0)
        selection = _selection(
            [
                {
                    "period": "2019",
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            ]
        )
        catalog = _Catalog().add("automated", 2019, _rows_for("automated", 2019, ids))
        code, text, repo, calls, slept = _run(
            monkeypatch,
            capsys,
            tmp_path,
            selection,
            catalog,
            config,
            error_status=503,
            error_times=1,
            retry_after=6,
        )
        assert code == 0, text
        assert any(call["status"] == 503 for call in calls)
        assert any(float(delay) >= 6 for delay in slept)
        _assert_print(text, (("automated", 2019, 2, 2),))
        _assert_paging(calls, schema, {("automated", 2019): 2}, 2)
        manifest = _assert_manifest(_year_dir(repo, "automated", 2019), ids)
        _assert_log(repo, {("automated", 2019): (2, 2, ids)}, {("automated", 2019): manifest})


def test_retry_exhaustion_exits_1_and_prints_the_url(monkeypatch, capsys, tmp_path):
    with _guard_repo():
        config = _config(page_size=2, max_retries=2, backoff=0.0)
        selection = _selection(
            [
                {
                    "period": "2019",
                    "source": "automated",
                    "years": [2019],
                    "included": True,
                    "reason": "",
                }
            ]
        )
        catalog = _Catalog().add(
            "automated", 2019, _rows_for("automated", 2019, [2, 9])
        )
        code, text, repo, calls, slept = _run(
            monkeypatch,
            capsys,
            tmp_path,
            selection,
            catalog,
            config,
            error_status=503,
            error_times=20,
            retry_after=5,
            max_calls=8,
        )
        assert code == 1, text
        assert config["api"]["automated"]["base_url"] in text
        assert len(calls) > 1
        assert any(float(delay) >= 5 for delay in slept)
        _assert_no_year_output(repo, "automated", 2019)
