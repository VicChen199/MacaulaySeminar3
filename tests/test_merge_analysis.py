"""Contract tests for joining neighborhood traffic to asthma rates.

The merge module and script are absent until this slice is implemented.
These tests lock the outer join, the usable count, and the duplicate-key stop.
"""

import csv
import shutil
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAME = "scripts/07_merge.py"
ASTHMA_PATH = Path("outputs/data/cleaned_asthma.csv")
TRAFFIC_PATH = Path("outputs/data/traffic_uhf42_year.csv")
COMPARISON_PATH = Path("outputs/dataset_source_comparison.csv")
MERGED_PATH = Path("outputs/data/final_merged_dataset.csv")
MISSING_PATH = Path("outputs/quality/missing_data_report.csv")
EXCLUSIONS_PATH = Path("outputs/audit/exclusions_merge.csv")
MERGED_COLUMNS = (
    "uhf42_id",
    "uhf42_name",
    "borough",
    "period",
    "asthma_ed_count",
    "asthma_ed_rate_per_10000",
    "traffic_mean_vehicles_per_hour",
    "traffic_median_vehicles_per_hour",
    "traffic_sd_vehicles_per_hour",
    "n_road_segments",
    "n_observation_days",
    "n_complete_hours",
    "traffic_source",
    "coverage_status",
    "exclusion_reason",
    "traffic_mean_hour_balanced",
)
MISSING_COLUMNS = (
    "period",
    "uhf42_id",
    "missing_rate",
    "missing_traffic",
    "exclusion_reason",
)
EXCLUSION_COLUMNS = ("stage", "key", "reason", "source")
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


def _require(reason):
    missing = []
    merge_uhf_period = None
    try:
        from lib.merge import merge_uhf_period as imported

        merge_uhf_period = imported
    except ImportError:
        missing.append("lib/merge.py merge_uhf_period")
    script = ROOT / SCRIPT_NAME
    if not script.is_file():
        missing.append("scripts/07_merge.py (python scripts/07_merge.py takes no arguments)")
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    assert callable(merge_uhf_period)
    return merge_uhf_period, script


def _asthma_row(uhf_id, period, rate, count=10, name="Neighborhood", borough="Manhattan"):
    return {
        "uhf42_id": str(uhf_id),
        "uhf42_name": name,
        "borough": borough,
        "period": str(period),
        "period_kind": "year",
        "constituent_years": str(period),
        "asthma_ed_count": count,
        "asthma_ed_rate_per_10000": rate,
    }


def _traffic_row(uhf_id, period, mean, segments, source="automated", balanced=None):
    return {
        "uhf42_id": str(uhf_id),
        "period": str(period),
        "traffic_mean_vehicles_per_hour": mean,
        "traffic_mean_hour_balanced": mean if balanced is None else balanced,
        "traffic_median_vehicles_per_hour": mean,
        "traffic_sd_vehicles_per_hour": 1.0 if mean is not None else None,
        "n_road_segments": segments,
        "n_monitoring_locations": segments,
        "n_observation_days": 4,
        "n_complete_hours": 30,
        "n_sessions": 2,
        "months_represented": "6",
        "weekday_weekend_coverage": "both",
        "traffic_source": source,
        "coverage_status": "valid" if segments is not None and segments >= 5 else "insufficient",
    }


def _comparison_row(period, source):
    return {
        "period": str(period),
        "asthma_available": "true",
        "automated_available": "true",
        "historical_available": "false",
        "aadt_available": "false",
        "raw_record_count": "10",
        "distinct_segments": "8",
        "n_uhf_with_traffic": "",
        "n_uhf_with_asthma": "",
        "n_matched": "",
        "n_usable": "",
        "recommended_source": source,
    }


def _frames():
    asthma = pd.DataFrame(
        [
            _asthma_row("101", "2019", 12.5, count=10),
            _asthma_row("102", "2019", None, count=4, name="Harlem", borough="Manhattan"),
            _asthma_row("201", "2018", 3.0, count=0, name="Mott Haven", borough="Bronx"),
            _asthma_row("104", "2019", 6.0, count=2, name="Highbridge", borough="Bronx"),
        ]
    )
    traffic = pd.DataFrame(
        [
            _traffic_row("101", "2019", 30.0, 6, balanced=28.0),
            _traffic_row("102", "2019", 8.0, 6, balanced=7.0),
            _traffic_row("103", "2019", 4.0, 6, balanced=4.0),
            _traffic_row("104", "2019", 15.0, 4, balanced=14.0),
        ]
    )
    comparison = pd.DataFrame(
        [
            _comparison_row("2019", "automated"),
            _comparison_row("2018", "historical"),
        ]
    )
    return asthma, traffic, comparison


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
        calls.append(args[0] if args else "")
        raise AssertionError("fixture test must not call Socrata")

    monkeypatch.setattr("requests.sessions.Session.send", send)
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


def _call(func, *args):
    try:
        value = func(*args)
    except SystemExit as exc:
        code = 0 if exc.code is None else exc.code
        return code, str(exc), None
    except Exception as exc:
        code = getattr(exc, "exit_code", None)
        if code is None:
            raise
        message = getattr(exc, "message", None) or str(exc)
        return code, message, None
    return 0, "", value


def _is_null(value):
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value):
    if _is_null(value):
        return ""
    return str(value).strip()


def _by_key(frame):
    found = {}
    for record in frame.to_dict(orient="records"):
        key = (_text(record["uhf42_id"]), _text(record["period"]))
        assert key not in found, key
        found[key] = record
    return found


def _write_csv(path, frame, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, columns=list(columns), na_rep="")


def _copy_tree(src, dest):
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        target = dest / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _prepare(tmp_path, script_src, asthma, traffic, comparison):
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    script = repo / SCRIPT_NAME
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_src, script)
    _write_csv(repo / ASTHMA_PATH, asthma, list(asthma.columns))
    _write_csv(repo / TRAFFIC_PATH, traffic, list(traffic.columns))
    _write_csv(repo / COMPARISON_PATH, comparison, COMPARISON_COLUMNS)
    return repo, script


def _copy_path(repo):
    entries = [str(repo)]
    for entry in sys.path:
        if entry in {"", "."} or entry == str(repo):
            continue
        try:
            if Path(entry).resolve() == ROOT:
                continue
        except OSError:
            pass
        entries.append(entry)
    return entries


def _invoke(script):
    import runpy

    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code
    return 0


def _run_script(monkeypatch, capsys, tmp_path, script_src, asthma, traffic, comparison):
    root_toml = (ROOT / "config" / "pipeline.toml").read_bytes()
    repo, script = _prepare(tmp_path, script_src, asthma, traffic, comparison)
    saved = {}
    for name in list(sys.modules):
        if name == "lib" or name.startswith("lib."):
            saved[name] = sys.modules.pop(name)
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "path", _copy_path(repo))
    monkeypatch.setattr(sys, "argv", [str(script)])
    calls = _forbid_network(monkeypatch)
    try:
        assert sys.argv == [str(script)], "python scripts/07_merge.py takes no arguments"
        code = _invoke(script)
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert (ROOT / "config" / "pipeline.toml").read_bytes() == root_toml
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()
        assert calls == [], "fixture test must not call Socrata"
        return code, text, repo
    finally:
        for name in list(sys.modules):
            if name == "lib" or name.startswith("lib."):
                del sys.modules[name]
        sys.modules.update(saved)


def _csv_table(path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or []), list(reader)


def test_outer_join_keeps_nulls_and_one_row_per_neighborhood(monkeypatch, capsys, tmp_path):
    reason = (
        "merge_uhf_period outer-joins on uhf42_id and period. "
        "A null rate and a null traffic mean stay null, and each neighborhood-period appears once."
    )
    with _guard_repo():
        merge_uhf_period, _script = _require(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        asthma, traffic, comparison = _frames()
        code, text, value = _call(merge_uhf_period, asthma, traffic, comparison)
        assert code == 0, text
        merged, updated, missing, exclusions = value
        assert tuple(merged.columns) == MERGED_COLUMNS
        got = _by_key(merged)
        assert len(got) == 5, "one row per neighborhood per period, including unmatched rows"
        valid = got[("101", "2019")]
        assert float(valid["asthma_ed_rate_per_10000"]) == pytest.approx(12.5)
        assert float(valid["traffic_mean_vehicles_per_hour"]) == pytest.approx(30.0)
        assert float(valid["traffic_mean_hour_balanced"]) == pytest.approx(28.0)
        assert _text(valid["coverage_status"]) == "valid"
        assert _text(valid["exclusion_reason"]) == ""
        suppressed = got[("102", "2019")]
        assert _is_null(suppressed["asthma_ed_rate_per_10000"]), "a null rate stays null"
        assert float(suppressed["traffic_mean_vehicles_per_hour"]) == pytest.approx(8.0)
        assert _text(suppressed["coverage_status"]) == "insufficient"
        assert _text(suppressed["exclusion_reason"]) == "missing_rate"
        traffic_only = got[("103", "2019")]
        assert _is_null(traffic_only["asthma_ed_rate_per_10000"]), "missing asthma stays null"
        assert _is_null(traffic_only["uhf42_name"])
        assert float(traffic_only["traffic_mean_vehicles_per_hour"]) == pytest.approx(4.0)
        assert _text(traffic_only["coverage_status"]) == "insufficient"
        assert "missing_rate" in _text(traffic_only["exclusion_reason"])
        low_segments = got[("104", "2019")]
        assert float(low_segments["asthma_ed_rate_per_10000"]) == pytest.approx(6.0)
        assert float(low_segments["traffic_mean_vehicles_per_hour"]) == pytest.approx(15.0)
        assert _text(low_segments["coverage_status"]) == "insufficient"
        assert _text(low_segments["exclusion_reason"]) == "segment_threshold"
        no_traffic = got[("201", "2018")]
        assert float(no_traffic["asthma_ed_count"]) == pytest.approx(0), "a real zero stays zero"
        assert _is_null(no_traffic["traffic_mean_vehicles_per_hour"]), "missing traffic stays null"
        assert _text(no_traffic["coverage_status"]) == "insufficient"
        assert _text(no_traffic["exclusion_reason"]) == "missing_traffic"
        report = _by_key(missing)
        assert tuple(missing.columns) == MISSING_COLUMNS
        assert bool(report[("102", "2019")]["missing_rate"]) is True
        assert bool(report[("102", "2019")]["missing_traffic"]) is False
        assert bool(report[("201", "2018")]["missing_traffic"]) is True
        assert bool(report[("101", "2019")]["missing_rate"]) is False
        reasons = {_text(row["reason"]) for row in exclusions.to_dict(orient="records")}
        assert "missing_rate" in reasons
        assert "missing_traffic" in reasons
        assert "segment_threshold" in reasons
        assert all(_text(row["stage"]) == "merge" for row in exclusions.to_dict(orient="records"))
        by_period = {
            _text(row["period"]): row for row in updated.to_dict(orient="records")
        }
        assert int(by_period["2019"]["n_matched"]) == 3
        assert int(by_period["2019"]["n_usable"]) == 1
        assert _text(by_period["2019"]["recommended_source"]) == "automated"
        assert int(by_period["2018"]["n_matched"]) == 0
        assert int(by_period["2018"]["n_usable"]) == 0
        assert _text(by_period["2018"]["recommended_source"]) == "historical"
        assert not (tmp_path / "outputs").exists(), (
            "merge_uhf_period on a fixture must not create outputs/"
        )
        assert calls == [], "fixture test must not call Socrata"


def test_script_prints_usable_n_and_keeps_a_zero_period(monkeypatch, capsys, tmp_path):
    reason = (
        "A fixture merge prints usable n for each period and exits 0. "
        "A period with usable n of 0 is printed and still exits 0."
    )
    with _guard_repo():
        merge_uhf_period, script = _require(reason)
        asthma, traffic, comparison = _frames()
        code, text, repo = _run_script(
            monkeypatch, capsys, tmp_path, script, asthma, traffic, comparison
        )
        assert code == 0, text
        assert "2019 usable 1" in text
        assert "2018 usable 0" in text
        assert "merge_uhf_period" in script.read_text(encoding="utf-8")
        assert merge_uhf_period is not None
        names, rows = _csv_table(repo / MERGED_PATH)
        assert tuple(names) == MERGED_COLUMNS
        by_key = {(_text(row["uhf42_id"]), _text(row["period"])): row for row in rows}
        assert by_key[("101", "2019")]["asthma_ed_rate_per_10000"] == "12.5"
        assert by_key[("102", "2019")]["asthma_ed_rate_per_10000"] == ""
        assert by_key[("201", "2018")]["traffic_mean_vehicles_per_hour"] == ""
        assert by_key[("201", "2018")]["asthma_ed_count"] == "0"
        assert by_key[("101", "2019")]["coverage_status"] == "valid"
        assert by_key[("104", "2019")]["coverage_status"] == "insufficient"
        assert by_key[("104", "2019")]["exclusion_reason"] == "segment_threshold"
        missing_names, missing_rows = _csv_table(repo / MISSING_PATH)
        assert tuple(missing_names) == MISSING_COLUMNS
        exclusion_names, exclusion_rows = _csv_table(repo / EXCLUSIONS_PATH)
        assert tuple(exclusion_names) == EXCLUSION_COLUMNS
        assert exclusion_rows
        assert all(row["stage"] == "merge" for row in exclusion_rows)
        _comparison_names, comparison_rows = _csv_table(repo / COMPARISON_PATH)
        by_period = {_text(row["period"]): row for row in comparison_rows}
        assert by_period["2019"]["n_matched"] == "3"
        assert by_period["2019"]["n_usable"] == "1"
        assert by_period["2019"]["recommended_source"] == "automated"
        assert by_period["2018"]["n_usable"] == "0"
        assert by_period["2018"]["recommended_source"] == "historical"


def test_duplicate_neighborhood_period_exits_2_and_writes_no_file(
    monkeypatch, capsys, tmp_path
):
    reason = (
        "A duplicate (uhf42_id, period) exits 2 and leaves no new merged file."
    )
    with _guard_repo():
        merge_uhf_period, script = _require(reason)
        monkeypatch.chdir(tmp_path)
        _forbid_network(monkeypatch)
        asthma, traffic, comparison = _frames()
        asthma = pd.concat(
            [asthma, pd.DataFrame([_asthma_row("101", "2019", 9.0)])],
            ignore_index=True,
        )
        code, text, value = _call(merge_uhf_period, asthma, traffic, comparison)
        assert code == 2, text
        assert "101" in text and "2019" in text
        assert value is None
        assert not (tmp_path / "outputs").exists()
        code, text, repo = _run_script(
            monkeypatch, capsys, tmp_path, script, asthma, traffic, comparison
        )
        assert code == 2, text
        assert "101" in text and "2019" in text
        assert not (repo / MERGED_PATH).exists(), "a duplicate key writes no merged file"


def test_more_than_42_neighborhoods_exits_2(monkeypatch, capsys, tmp_path):
    reason = "A count above 42 exits 2 and writes no merged file."
    with _guard_repo():
        _merge_uhf_period, script = _require(reason)
        asthma_rows = [
            _asthma_row(str(200 + index), "2019", 1.0, name=f"N{index}")
            for index in range(43)
        ]
        traffic_rows = [
            _traffic_row(str(200 + index), "2019", 10.0, 6) for index in range(43)
        ]
        comparison = pd.DataFrame([_comparison_row("2019", "automated")])
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            pd.DataFrame(asthma_rows),
            pd.DataFrame(traffic_rows),
            comparison,
        )
        assert code == 2, text
        assert "2019" in text
        assert not (repo / MERGED_PATH).exists()
