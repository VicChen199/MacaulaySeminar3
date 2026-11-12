"""Contract tests for the workbook, result figures, and both narratives.

The report module, the result-figure function, and the script are absent until
this slice is implemented.
"""

import json
import shutil
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAME = "scripts/10_report.py"
WORKBOOK_PATH = Path("outputs/research_results.xlsx")
SUMMARY_PATH = Path("outputs/results_summary.md")
GUIDE_PATH = Path("outputs/interpretation_guide.md")
EXCLUSIONS_PATH = Path("outputs/quality/excluded_observations.csv")
FIGURE_DIR = Path("outputs/figures/results")
SHEETS = (
    "Year and Source Coverage",
    "Matched Dataset",
    "Sample Characteristics",
    "Descriptive Statistics",
    "Pearson Correlations",
    "Spearman Sensitivity",
    "Correlation Comparisons",
    "Missing Data and Exclusions",
    "Data Dictionary",
)
HEADINGS = (
    "## Question and hypotheses",
    "## Sources, sample size, variables, and descriptive statistics",
    "## Primary numbers",
    "## Test justification",
)
GUIDE_PHRASES = (
    "n is",
    "r is",
    "p-value is",
    "confidence interval is",
    "directional reading is",
    "cross-period consistency is",
    "ecological limit is",
)
R2_SENTENCE = "R2 is omitted because only one period was tested."
LIMITATION = "No Pearson test has test_status=ok."
R_PRIMARY = "0.512345678"
P_PRIMARY = "0.0123456789"
R_SECOND = "0.256789012"
P_SECOND = "0.0345678901"
CI_LOW = "0.101"
CI_HIGH = "0.834"
RHO_PRIMARY = "0.423456789"
SHARED_SCALE = "xlim=10,30; ylim=5,25"
FIGURES = (
    "R1_primary_scatter",
    "R2_comparison_scatter",
    "R3_correlation_forest",
    "R4_pearson_spearman",
)


def _require(reason):
    missing = []
    functions = None
    try:
        from lib.plots import result_figures
        from lib.report import compile_exclusions, narratives, workbook

        functions = {
            "result_figures": result_figures,
            "workbook": workbook,
            "narratives": narratives,
            "compile_exclusions": compile_exclusions,
        }
    except ImportError:
        missing.append(
            "lib/report.py workbook narratives compile_exclusions; "
            "lib/plots.py result_figures"
        )
    script = ROOT / SCRIPT_NAME
    if not script.is_file():
        missing.append("scripts/10_report.py (python scripts/10_report.py takes no arguments)")
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    for function in functions.values():
        assert callable(function)
    return functions, script


def _pearson_row(period, role, correlation, p_value, status="ok"):
    return {
        "period": period,
        "role": role,
        "n": "30" if status == "ok" else "2",
        "r": correlation if status == "ok" else "",
        "t": "3.1" if status == "ok" else "",
        "df": "28" if status == "ok" else "",
        "p_raw": p_value if status == "ok" else "",
        "p_holm": "",
        "ci_low": CI_LOW if status == "ok" else "",
        "ci_high": CI_HIGH if status == "ok" else "",
        "alpha": "0.05",
        "p_below_alpha": "true" if status == "ok" else "false",
        "sign": "positive" if status == "ok" else "",
        "primary_reason": "primary_preference",
        "test_status": status,
        "skip_reason": "" if status == "ok" else "below_min_uhf",
        "traffic_source": "automated",
    }


def _merged(periods):
    rows = []
    points = {
        "2023": ((10, 5), (20, 15), (30, 25)),
        "2019": ((12, 8), (18, 16), (28, 22)),
    }
    for period in periods:
        for index, (traffic, rate) in enumerate(points[period], start=1):
            rows.append(
                {
                    "uhf42_id": str(100 + index),
                    "uhf42_name": f"N{index}",
                    "borough": "Manhattan",
                    "period": period,
                    "asthma_ed_count": "4",
                    "asthma_ed_rate_per_10000": str(rate),
                    "traffic_mean_vehicles_per_hour": str(traffic),
                    "traffic_median_vehicles_per_hour": str(traffic),
                    "traffic_sd_vehicles_per_hour": "1",
                    "n_road_segments": "6",
                    "n_observation_days": "4",
                    "n_complete_hours": "40",
                    "traffic_source": "automated",
                    "coverage_status": "valid",
                    "exclusion_reason": "",
                    "traffic_mean_hour_balanced": "0",
                }
            )
    return pd.DataFrame(rows)


def _selection(periods):
    return {
        "scenario": "2",
        "mix_used": False,
        "comparability_pass": True,
        "comparability_r": None,
        "comparability_n": None,
        "periods": [
            {
                "period": period,
                "source": "automated",
                "years": [int(period)],
                "included": True,
                "reason": "",
            }
            for period in periods
        ],
    }


def _bundle(periods, status="ok"):
    correlations = {"2023": (R_PRIMARY, P_PRIMARY, RHO_PRIMARY), "2019": (R_SECOND, P_SECOND, "0.301")}
    pearson = pd.DataFrame(
        [
            _pearson_row(
                period,
                "primary" if period == "2023" else "secondary",
                correlations[period][0],
                correlations[period][1],
                status=status,
            )
            for period in periods
        ]
    )
    spearman = pd.DataFrame(
        [
            {
                "period": period,
                "n": "30" if status == "ok" else "2",
                "rho": correlations[period][2] if status == "ok" else "",
                "p_raw": "0.2" if status == "ok" else "",
                "test_status": status,
                "skip_reason": "" if status == "ok" else "below_min_uhf",
            }
            for period in periods
        ]
    )
    sensitivity = pd.DataFrame(
        [
            {
                "analysis_id": "loo_101",
                "period": "2023",
                "n": "29",
                "r": "0.49",
                "p_raw": "0.02",
                "note": "101",
            }
        ]
    )
    comparison = pd.DataFrame(
        [
            {
                "period": period,
                "n": "30" if status == "ok" else "2",
                "r": correlations[period][0] if status == "ok" else "",
                "p_raw": correlations[period][1] if status == "ok" else "",
                "p_holm": "",
                "ci_low": CI_LOW if status == "ok" else "",
                "ci_high": CI_HIGH if status == "ok" else "",
                "sign": "positive" if status == "ok" else "",
                "traffic_source": "automated",
                "n_segments_median": "6",
                "measurement_consistency": "direction=positive; overlap=3",
            }
            for period in periods
        ]
    )
    descriptive = pd.DataFrame(
        [
            {
                "period": "2023",
                "variable": "asthma_ed_rate_per_10000",
                "n": "30",
                "mean": "15",
                "median": "15",
                "std": "8",
                "min": "5",
                "max": "25",
                "range": "20",
                "q1": "10",
                "q3": "20",
                "iqr": "10",
                "n_missing": "0",
                "pct_missing": "0",
            }
        ]
    )
    borough = pd.DataFrame(
        [
            {
                "period": "2023",
                "borough": "Manhattan",
                "coverage_status": "valid",
                "n": "3",
                "pct": "100",
            }
        ]
    )
    source_comparison = pd.DataFrame(
        [
            {
                "period": period,
                "asthma_available": "true",
                "automated_available": "true",
                "historical_available": "false",
                "aadt_available": "false",
                "raw_record_count": "10",
                "distinct_segments": "6",
                "n_uhf_with_traffic": "3",
                "n_uhf_with_asthma": "3",
                "n_matched": "3",
                "n_usable": "3",
                "recommended_source": "automated",
            }
            for period in periods
        ]
    )
    exclusions = [
        pd.DataFrame(
            [
                {
                    "stage": "clean",
                    "key": "uhf-clean-1",
                    "reason": "suppressed",
                    "source": "asthma",
                }
            ]
        ),
        pd.DataFrame(
            [
                {
                    "stage": "merge",
                    "key": "uhf-merge-1",
                    "reason": "missing_rate",
                    "source": "asthma",
                }
            ]
        ),
    ]
    return {
        "merged": _merged(periods),
        "pearson": pearson,
        "spearman": spearman,
        "sensitivity": sensitivity,
        "comparison": comparison,
        "descriptive": descriptive,
        "borough": borough,
        "source_comparison": source_comparison,
        "selection": _selection(periods),
        "exclusions": exclusions,
    }


def _pdf_text(path):
    return path.read_bytes().decode("latin-1", errors="ignore")


def _sheet_text(workbook, name):
    sheet = workbook[name]
    values = []
    for row in sheet.iter_rows(values_only=True):
        values.extend("" if value is None else str(value) for value in row)
    return "\n".join(values)


def _section(text, heading):
    start = text.index(heading) + len(heading)
    endings = [text.find(item, start) for item in HEADINGS if text.find(item, start) != -1]
    end = min(endings) if endings else len(text)
    return text[start:end]


def _assert_numbers_match(text, pearson):
    import re

    correlations = {str(value) for value in pearson["r"].tolist() if str(value)}
    probabilities = {str(value) for value in pearson["p_raw"].tolist() if str(value)}
    for value in re.findall(r"(?:^|[\s;])r=([0-9.]+)", text):
        assert value in correlations, value
    for value in re.findall(r"(?:^|[\s;])p=([0-9.]+)", text):
        assert value in probabilities, value


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


def test_two_periods_keep_shared_scale_and_match_the_pearson_csv(monkeypatch, tmp_path):
    reason = (
        "R1 and R3 show n, r, and the exact p-value. R2 uses the latest tested period "
        "that is not primary and shares R1's scale. R4 labels Pearson and Spearman. "
        "The workbook has the nine sheets. Every r and p in the narratives matches the Pearson CSV."
    )
    with _guard_repo():
        functions, _script = _require(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        bundle = _bundle(("2023", "2019"))
        figure_dir = tmp_path / "figures"
        paths = functions["result_figures"](
            bundle["merged"],
            bundle["pearson"],
            bundle["spearman"],
            bundle["selection"],
            figure_dir,
        )
        expected = [figure_dir / f"{stem}{suffix}" for stem in FIGURES for suffix in (".png", ".pdf")]
        got = {Path(path) for path in paths}
        for path in expected:
            assert path in got and path.is_file() and path.stat().st_size > 0
        primary = _pdf_text(figure_dir / "R1_primary_scatter.pdf")
        assert f"n=30; r={R_PRIMARY}; p={P_PRIMARY}" in primary
        comparison = _pdf_text(figure_dir / "R2_comparison_scatter.pdf")
        assert P_SECOND in comparison
        assert SHARED_SCALE in primary and SHARED_SCALE in comparison
        forest = _pdf_text(figure_dir / "R3_correlation_forest.pdf")
        assert f"n=30; r={R_PRIMARY}; p={P_PRIMARY}" in forest
        assert f"n=30; r={R_SECOND}; p={P_SECOND}" in forest
        paired = _pdf_text(figure_dir / "R4_pearson_spearman.pdf")
        assert "Pearson" in paired and "Spearman" in paired
        assert R_PRIMARY in paired and RHO_PRIMARY in paired
        summary, guide = functions["narratives"](bundle, tmp_path / "notes")
        summary_text = Path(summary).read_text(encoding="utf-8")
        guide_text = Path(guide).read_text(encoding="utf-8")
        for heading in HEADINGS:
            assert heading in summary_text
        primary_section = _section(summary_text, "## Primary numbers")
        assert R_PRIMARY in primary_section and P_PRIMARY in primary_section
        lowered = primary_section.lower()
        assert "because" not in lowered
        assert "cause" not in lowered
        assert "literature" not in lowered
        justification = _section(summary_text, "## Test justification")
        assert "R1_primary_scatter" in justification and "R3_correlation_forest" in justification
        assert P_PRIMARY in justification
        assert R2_SENTENCE not in summary_text
        for phrase in GUIDE_PHRASES:
            assert phrase in guide_text
        assert R_PRIMARY in guide_text and P_PRIMARY in guide_text
        assert CI_LOW in guide_text and CI_HIGH in guide_text
        for text in (summary_text, guide_text):
            assert "caus" not in text.lower()
            assert "literature" not in text.lower()
            _assert_numbers_match(text, bundle["pearson"])
        exclusions = functions["compile_exclusions"](
            bundle["exclusions"],
            tmp_path / "excluded_observations.csv",
        )
        assert tuple(exclusions.columns) == ("stage", "key", "reason", "source")
        assert set(exclusions["key"].astype(str)) == {"uhf-clean-1", "uhf-merge-1"}
        workbook_path = functions["workbook"](bundle, tmp_path / "research_results.xlsx")
        from openpyxl import load_workbook

        book = load_workbook(workbook_path)
        assert tuple(book.sheetnames) == SHEETS
        dictionary = book["Data Dictionary"]
        headers = [cell.value for cell in next(dictionary.iter_rows(max_row=1))]
        assert tuple(headers) == ("file", "column", "type", "meaning", "units")
        assert "101" in _sheet_text(book, "Matched Dataset")
        assert "Manhattan" in _sheet_text(book, "Sample Characteristics")
        assert "asthma_ed_rate_per_10000" in _sheet_text(book, "Descriptive Statistics")
        assert R_PRIMARY in _sheet_text(book, "Pearson Correlations")
        assert RHO_PRIMARY in _sheet_text(book, "Spearman Sensitivity")
        assert "loo_101" in _sheet_text(book, "Spearman Sensitivity")
        assert "overlap=3" in _sheet_text(book, "Correlation Comparisons")
        assert "uhf-clean-1" in _sheet_text(book, "Missing Data and Exclusions")
        assert "automated" in _sheet_text(book, "Year and Source Coverage")
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


def _write_inputs(repo, bundle):
    mapping = {
        "outputs/data/final_merged_dataset.csv": bundle["merged"],
        "outputs/stats/pearson_results_by_year.csv": bundle["pearson"],
        "outputs/stats/spearman_results_by_year.csv": bundle["spearman"],
        "outputs/stats/sensitivity_analysis_results.csv": bundle["sensitivity"],
        "outputs/stats/correlation_comparison_results.csv": bundle["comparison"],
        "outputs/stats/descriptive_statistics_by_year.csv": bundle["descriptive"],
        "outputs/stats/descriptive_statistics_by_borough.csv": bundle["borough"],
        "outputs/dataset_source_comparison.csv": bundle["source_comparison"],
    }
    for relative, frame in mapping.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
    selection = repo / "outputs/audit/source_selection.json"
    selection.parent.mkdir(parents=True, exist_ok=True)
    selection.write_text(json.dumps(bundle["selection"]), encoding="utf-8")
    names = ("exclusions_clean.csv", "exclusions_merge.csv")
    for name, frame in zip(names, bundle["exclusions"]):
        path = repo / "outputs/audit" / name
        frame.to_csv(path, index=False)


def _run_script(monkeypatch, capsys, tmp_path, script_src, bundle):
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    shutil.copy2(ROOT / "README.md", repo / "README.md")
    readme = (repo / "README.md").read_bytes()
    script = repo / SCRIPT_NAME
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_src, script)
    if bundle is not None:
        _write_inputs(repo, bundle)
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
        assert sys.argv == [str(script)], "python scripts/10_report.py takes no arguments"
        try:
            runpy.run_path(str(script), run_name="__main__")
            code = 0
        except SystemExit as exc:
            code = 0 if exc.code is None else exc.code
        captured = capsys.readouterr()
        text = f"{captured.out}{captured.err}"
        assert calls == [], "fixture test must not call Socrata"
        assert (repo / "README.md").read_bytes() == readme
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()
        return code, text, repo
    finally:
        for name in list(sys.modules):
            if name == "lib" or name.startswith("lib."):
                del sys.modules[name]
        sys.modules.update(saved)


def test_one_tested_period_prints_r1_and_r3_and_omits_r2(monkeypatch, capsys, tmp_path):
    reason = (
        "A fixture with one ok Pearson row prints the workbook path and the R1 and R3 paths "
        "and exits 0. R2 is omitted and the summary says so."
    )
    with _guard_repo():
        _functions, script = _require(reason)
        code, text, repo = _run_script(
            monkeypatch, capsys, tmp_path, script, _bundle(("2023",))
        )
        assert code == 0, text
        assert "outputs/research_results.xlsx" in text
        for stem in ("R1_primary_scatter", "R3_correlation_forest"):
            for suffix in (".png", ".pdf"):
                relative = f"outputs/figures/results/{stem}{suffix}"
                assert relative in text
                assert (repo / relative).is_file()
        assert not (repo / FIGURE_DIR / "R2_comparison_scatter.png").exists()
        assert "R2_comparison_scatter" not in text
        summary = (repo / SUMMARY_PATH).read_text(encoding="utf-8")
        assert R2_SENTENCE in summary
        assert R_PRIMARY in summary and P_PRIMARY in summary
        from openpyxl import load_workbook

        book = load_workbook(repo / WORKBOOK_PATH)
        assert tuple(book.sheetnames) == SHEETS
        primary = _pdf_text(repo / FIGURE_DIR / "R1_primary_scatter.pdf")
        assert f"n=30; r={R_PRIMARY}; p={P_PRIMARY}" in primary
        assert (repo / EXCLUSIONS_PATH).is_file()
        exclusions = pd.read_csv(repo / EXCLUSIONS_PATH, dtype=str)
        assert set(exclusions["key"]) == {"uhf-clean-1", "uhf-merge-1"}


def test_no_ok_pearson_test_states_the_limitation_and_exits_2(monkeypatch, capsys, tmp_path):
    reason = "When no Pearson test is ok, the summary states the limitation and the process exits 2."
    with _guard_repo():
        _functions, script = _require(reason)
        code, _text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            _bundle(("2023",), status="skipped"),
        )
        assert code == 2
        summary = (repo / SUMMARY_PATH).read_text(encoding="utf-8")
        assert LIMITATION in summary
        assert P_PRIMARY not in summary
        assert R_PRIMARY not in summary
