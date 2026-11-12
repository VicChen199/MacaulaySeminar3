"""Contract tests for the sample description and figures S1–S7.

The describe module, the sample-figure function, and the script are absent
until this slice is implemented.
"""

import json
import shutil
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "describe_sample"
SCRIPT_NAME = "scripts/08_describe.py"
MERGED_PATH = Path("outputs/data/final_merged_dataset.csv")
TRAFFIC_PATH = Path("outputs/data/traffic_uhf42_year.csv")
LOCATIONS_PATH = Path("outputs/data/traffic_monitoring_locations.geojson")
BASEMAP_PATH = Path("data/raw/geography/UHF42.geo.json")
SELECTION_PATH = Path("outputs/audit/source_selection.json")
YEAR_PATH = Path("outputs/stats/descriptive_statistics_by_year.csv")
BOROUGH_PATH = Path("outputs/stats/descriptive_statistics_by_borough.csv")
FIGURE_DIR = Path("outputs/figures/sample")
YEAR_COLUMNS = (
    "period",
    "variable",
    "n",
    "mean",
    "median",
    "std",
    "min",
    "max",
    "range",
    "q1",
    "q3",
    "iqr",
    "n_missing",
    "pct_missing",
)
BOROUGH_COLUMNS = ("period", "borough", "coverage_status", "n", "pct")
FIGURE_STEMS = (
    "S1_traffic_distribution",
    "S2_asthma_distribution",
    "S3_coverage_by_period",
    "S4_coverage_heatmap",
    "S5_asthma_boxplots",
    "S6_traffic_boxplots",
)
ANNOTATION = "no eligible neighborhood is available"
S4_NOTE = "Missing cells are masked"


def _require(reason):
    missing = []
    primary_period = descriptive_tables = sample_figures = None
    try:
        from lib.describe import descriptive_tables as tables
        from lib.describe import primary_period as choose

        descriptive_tables = tables
        primary_period = choose
    except ImportError:
        missing.append("lib/describe.py descriptive_tables primary_period")
    try:
        from lib.plots import sample_figures as figures

        sample_figures = figures
    except ImportError:
        missing.append("lib/plots.py sample_figures")
    script = ROOT / SCRIPT_NAME
    if not script.is_file():
        missing.append("scripts/08_describe.py (python scripts/08_describe.py takes no arguments)")
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    assert callable(primary_period)
    assert callable(descriptive_tables)
    assert callable(sample_figures)
    return primary_period, descriptive_tables, sample_figures, script


def _row(uhf_id, period, rate, traffic, segments, borough, status="valid"):
    return {
        "uhf42_id": str(uhf_id),
        "uhf42_name": f"N{uhf_id}",
        "borough": borough,
        "period": str(period),
        "asthma_ed_count": 1,
        "asthma_ed_rate_per_10000": rate,
        "traffic_mean_vehicles_per_hour": traffic,
        "traffic_median_vehicles_per_hour": traffic,
        "traffic_sd_vehicles_per_hour": 1.0,
        "n_road_segments": segments,
        "n_observation_days": 4,
        "n_complete_hours": 30,
        "traffic_source": "automated",
        "coverage_status": status,
        "exclusion_reason": "" if status == "valid" else "missing_rate",
        "traffic_mean_hour_balanced": traffic,
        "p_value": 0.9 if str(period) == "2023" else 0.0001,
    }


def _qualifying_frame():
    rows = []
    for index in range(1, 31):
        rows.append(_row(index, "2023", 10.0, 20.0, 6, "Manhattan"))
        rows.append(_row(index, "2019", 1.0, 1.0, 6, "Queens"))
    rows.append(_row(31, "2023", None, None, None, "Bronx", status="insufficient"))
    return pd.DataFrame(rows)


def _selection(periods):
    return {
        "scenario": "2",
        "mix_used": False,
        "comparability_pass": True,
        "comparability_r": None,
        "comparability_n": None,
        "periods": periods,
    }


def _included(period, source="automated", included=True):
    return {
        "period": period,
        "source": source,
        "years": [int(token) for token in str(period).replace("to", " ").replace("-", " ").split() if token.isdigit()],
        "included": included,
        "reason": "" if included else "gap",
    }


def _qualifying_selection():
    return _selection(
        [
            _included("2023"),
            _included("2019"),
            _included("2018", source="historical"),
            _included("2015", source="historical", included=False),
        ]
    )


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
        calls.append(getattr(prepared, "url", ""))
        raise AssertionError("fixture test must not call Socrata")

    def get(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("url", ""))
        raise AssertionError("fixture test must not call Socrata")

    def urlopen(*args, **kwargs):
        calls.append(args[0] if args else "")
        raise AssertionError("fixture test must not call Socrata")

    monkeypatch.setattr("requests.sessions.Session.send", send)
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


def _pdf_text(path):
    return path.read_bytes().decode("latin-1", errors="ignore")


def _lookup(frame, period, variable):
    match = frame[
        (frame["period"].map(str) == str(period))
        & (frame["variable"].map(str) == variable)
    ]
    assert len(match) == 1, (period, variable, frame)
    return match.iloc[0]


def test_tables_and_primary_period_ignore_p_values(monkeypatch, tmp_path):
    reason = (
        "descriptive_tables summarizes the rate, the primary traffic mean, "
        "and n_road_segments. The primary period is the preference year when "
        "it qualifies, and an earlier period with a smaller p-value does not replace it."
    )
    with _guard_repo():
        primary_period, descriptive_tables, _figures, _script = _require(reason)
        monkeypatch.chdir(tmp_path)
        merged = _qualifying_frame()
        chosen = primary_period(merged, _qualifying_selection())
        assert str(chosen) == "2023"
        below = pd.DataFrame(
            [_row(index, "2023", 10.0, 20.0, 6, "Manhattan") for index in range(1, 3)]
            + [_row(index, "2019", 1.0, 1.0, 6, "Queens") for index in range(1, 31)]
            + [_row(index, "2018", 2.0, 2.0, 6, "Bronx") for index in range(1, 31)]
        )
        below["p_value"] = np.where(below["period"].astype(str) == "2018", 0.00001, 0.4)
        fallback = primary_period(
            below,
            _selection([_included("2023"), _included("2019"), _included("2018")]),
        )
        assert str(fallback) == "2019"
        by_year, by_borough = descriptive_tables(merged)
        assert tuple(by_year.columns) == YEAR_COLUMNS
        assert tuple(by_borough.columns) == BOROUGH_COLUMNS
        rate = _lookup(by_year, "2023", "asthma_ed_rate_per_10000")
        assert int(rate["n"]) == 30
        assert int(rate["n_missing"]) == 1
        assert float(rate["mean"]) == pytest.approx(10)
        assert float(rate["median"]) == pytest.approx(10)
        assert float(rate["std"]) == pytest.approx(0)
        assert float(rate["min"]) == pytest.approx(10)
        assert float(rate["max"]) == pytest.approx(10)
        assert float(rate["range"]) == pytest.approx(0)
        assert float(rate["q1"]) == pytest.approx(10)
        assert float(rate["q3"]) == pytest.approx(10)
        assert float(rate["iqr"]) == pytest.approx(0)
        assert float(rate["pct_missing"]) == pytest.approx(100 / 31)
        traffic = _lookup(by_year, "2023", "traffic_mean_vehicles_per_hour")
        assert int(traffic["n"]) == 30
        assert float(traffic["mean"]) == pytest.approx(20)
        segments = _lookup(by_year, "2023", "n_road_segments")
        assert int(segments["n"]) == 30
        assert float(segments["mean"]) == pytest.approx(6)
        usable_rates = [10.0] * 30 + [1.0] * 30
        usable = _lookup(by_year, "usable", "asthma_ed_rate_per_10000")
        assert int(usable["n"]) == 60
        assert float(usable["mean"]) == pytest.approx(5.5)
        assert float(usable["std"]) == pytest.approx(float(np.std(usable_rates, ddof=1)))
        assert float(usable["q1"]) == pytest.approx(float(np.quantile(usable_rates, 0.25)))
        borough = by_borough[by_borough["period"].map(str) == "2023"]
        assert str(borough.loc[borough["n"].astype(int).idxmax(), "borough"]) == "Manhattan"
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()


def test_sample_figures_cover_s1_through_s7(monkeypatch, tmp_path):
    reason = (
        "sample_figures writes S1 through S7 as PNG and PDF. "
        "S1 and S2 show n, mean, median, and standard deviation. "
        "S4 masks missing cells. A figure with n of 0 says no eligible neighborhood is available. "
        "The figures carry no p-value."
    )
    with _guard_repo():
        _primary, _tables, sample_figures, _script = _require(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        output = tmp_path / "figures"
        paths = sample_figures(
            _qualifying_frame(),
            FIXTURES / "locations.geojson",
            FIXTURES / "UHF42.geo.json",
            output,
            "2023",
            _qualifying_selection(),
        )
        expected = []
        for stem in FIGURE_STEMS:
            expected.extend([output / f"{stem}.png", output / f"{stem}.pdf"])
        for period in ("2023", "2019", "2018"):
            expected.extend(
                [
                    output / f"S7_coverage_map_{period}.png",
                    output / f"S7_coverage_map_{period}.pdf",
                ]
            )
        got = {Path(path) for path in paths}
        for path in expected:
            assert path in got, path
            assert path.is_file() and path.stat().st_size > 0
        s1 = _pdf_text(output / "S1_traffic_distribution.pdf")
        s2 = _pdf_text(output / "S2_asthma_distribution.pdf")
        assert "n=30, mean=20, median=20, sd=0" in s1
        assert "n=30, mean=10, median=10, sd=0" in s2
        assert "p-value" not in s1.lower() and "p=" not in s1.lower()
        assert "p-value" not in s2.lower() and "p=" not in s2.lower()
        assert S4_NOTE in _pdf_text(output / "S4_coverage_heatmap.pdf")
        empty = _pdf_text(output / "S7_coverage_map_2018.pdf")
        assert ANNOTATION in empty
        assert calls == []
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()


def _copy_tree(src, dest):
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        target = dest / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _run_script(monkeypatch, capsys, tmp_path, script_src, merged, selection):
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    script = repo / SCRIPT_NAME
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_src, script)
    if merged is not None:
        target = repo / MERGED_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(target, index=False)
    traffic = repo / TRAFFIC_PATH
    traffic.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"uhf42_id": "1", "period": "2023", "traffic_mean_vehicles_per_hour": 20}]
    ).to_csv(traffic, index=False)
    shutil.copy2(FIXTURES / "locations.geojson", repo / LOCATIONS_PATH)
    basemap = repo / BASEMAP_PATH
    basemap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURES / "UHF42.geo.json", basemap)
    selection_path = repo / SELECTION_PATH
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    saved = {}
    for name in list(sys.modules):
        if name == "lib" or name.startswith("lib."):
            saved[name] = sys.modules.pop(name)
    monkeypatch.chdir(repo)
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
    monkeypatch.setattr(sys, "path", entries)
    monkeypatch.setattr(sys, "argv", [str(script)])
    calls = _forbid_network(monkeypatch)
    import runpy

    try:
        assert sys.argv == [str(script)], "python scripts/08_describe.py takes no arguments"
        try:
            runpy.run_path(str(script), run_name="__main__")
            code = 0
        except SystemExit as exc:
            code = 0 if exc.code is None else exc.code
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert calls == [], "fixture test must not call Socrata"
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()
        return code, text, repo
    finally:
        for name in list(sys.modules):
            if name == "lib" or name.startswith("lib."):
                del sys.modules[name]
        sys.modules.update(saved)


def test_script_prints_figure_paths_and_primary_period(monkeypatch, capsys, tmp_path):
    reason = (
        "A fixture run prints the S1–S7 paths and the primary period and exits 0. "
        "A figure with n of 0 says no eligible neighborhood is available."
    )
    with _guard_repo():
        _primary, _tables, _figures, script = _require(reason)
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            _qualifying_frame(),
            _qualifying_selection(),
        )
        assert code == 0, text
        assert "primary period 2023" in text
        for stem in FIGURE_STEMS:
            for suffix in (".png", ".pdf"):
                relative = f"outputs/figures/sample/{stem}{suffix}"
                assert relative in text, relative
                assert (repo / relative).is_file()
        for period in ("2023", "2019", "2018"):
            relative = f"outputs/figures/sample/S7_coverage_map_{period}.pdf"
            assert relative in text
            assert ANNOTATION in _pdf_text(repo / relative) or period != "2018"
        assert ANNOTATION in _pdf_text(repo / FIGURE_DIR / "S7_coverage_map_2018.pdf")
        assert (repo / YEAR_PATH).is_file()
        assert (repo / BOROUGH_PATH).is_file()
        assert not (repo / "outputs" / "figures" / "results").exists()
        source = "\n".join(
            (repo / name).read_text(encoding="utf-8")
            for name in ("lib/describe.py", "lib/plots.py", "scripts/08_describe.py")
        )
        assert "lib.stats" not in source and "lib/stats" not in source
        assert "result_figures" not in source


def test_missing_merged_file_exits_2(monkeypatch, capsys, tmp_path):
    reason = "A missing merged file exits 2."
    with _guard_repo():
        _primary, _tables, _figures, script = _require(reason)
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            None,
            _qualifying_selection(),
        )
        assert code == 2, text
        assert "final_merged_dataset.csv" in text
        assert not (repo / YEAR_PATH).exists()
