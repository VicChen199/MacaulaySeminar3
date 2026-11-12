"""Contract tests for the neighborhood correlation and the period comparison.

The stats module and the hypothesis script are absent until this slice is implemented.
"""

import math
import shutil
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_NAME = "scripts/09_test_hypotheses.py"
MERGED_PATH = Path("outputs/data/final_merged_dataset.csv")
LOCATION_PATH = Path("outputs/data/traffic_location_year.parquet")
PEARSON_PATH = Path("outputs/stats/pearson_results_by_year.csv")
SPEARMAN_PATH = Path("outputs/stats/spearman_results_by_year.csv")
SENSITIVITY_PATH = Path("outputs/stats/sensitivity_analysis_results.csv")
COMPARISON_PATH = Path("outputs/stats/correlation_comparison_results.csv")
PEARSON_COLUMNS = (
    "period",
    "role",
    "n",
    "r",
    "t",
    "df",
    "p_raw",
    "p_holm",
    "ci_low",
    "ci_high",
    "alpha",
    "p_below_alpha",
    "sign",
    "primary_reason",
    "test_status",
    "skip_reason",
    "traffic_source",
)
SPEARMAN_COLUMNS = ("period", "n", "rho", "p_raw", "test_status", "skip_reason")
SENSITIVITY_COLUMNS = ("analysis_id", "period", "n", "r", "p_raw", "note")
COMPARISON_COLUMNS = (
    "period",
    "n",
    "r",
    "p_raw",
    "p_holm",
    "ci_low",
    "ci_high",
    "sign",
    "traffic_source",
    "n_segments_median",
    "measurement_consistency",
)
IDS = [str(number) for number in range(1, 31)]
IDS_2018 = [str(number) for number in range(2, 32)]


def _require(reason):
    missing = []
    functions = None
    try:
        from lib.stats import (
            compare_periods,
            pearson_by_period,
            primary_period,
            sensitivity,
            spearman_by_period,
        )

        functions = {
            "primary_period": primary_period,
            "pearson_by_period": pearson_by_period,
            "spearman_by_period": spearman_by_period,
            "sensitivity": sensitivity,
            "compare_periods": compare_periods,
        }
    except ImportError:
        missing.append(
            "lib/stats.py primary_period pearson_by_period spearman_by_period "
            "sensitivity compare_periods"
        )
    script = ROOT / SCRIPT_NAME
    if not script.is_file():
        missing.append("scripts/09_test_hypotheses.py (python scripts/09_test_hypotheses.py takes no arguments)")
    if missing:
        pytest.fail(f"{reason} Missing: {'; '.join(missing)}")
    for function in functions.values():
        assert callable(function)
    return functions, script


def _rng_series():
    generator = np.random.default_rng(1)
    traffic = np.arange(1, 31, dtype=float)
    shared = np.array([(index * 3) % 17 + (index % 2) * 4 for index in range(1, 31)], dtype=float)
    rate_2023 = traffic * 1.5 + generator.normal(0, 2, size=30)
    rate_2019 = traffic * 3 + generator.normal(0, 0.5, size=30)
    rate_2018 = np.arange(1, 31, dtype=float) + generator.normal(0, 8, size=30)
    return traffic, shared, rate_2023, rate_2019, rate_2018


def _row(uhf, period, rate, traffic, status="valid", p_value=0.9):
    return {
        "uhf42_id": str(uhf),
        "uhf42_name": f"N{uhf}",
        "borough": "Manhattan",
        "period": str(period),
        "asthma_ed_count": 1,
        "asthma_ed_rate_per_10000": rate,
        "traffic_mean_vehicles_per_hour": traffic,
        "traffic_median_vehicles_per_hour": traffic,
        "traffic_sd_vehicles_per_hour": 1.0,
        "n_road_segments": 6 if status == "valid" else None,
        "n_observation_days": 4,
        "n_complete_hours": 40,
        "traffic_source": "automated",
        "coverage_status": status,
        "exclusion_reason": "" if status == "valid" else "missing_rate",
        "traffic_mean_hour_balanced": 0.0,
        "p_value": p_value,
    }


def _selection(periods):
    return {
        "scenario": "2",
        "mix_used": False,
        "comparability_pass": True,
        "comparability_r": None,
        "comparability_n": None,
        "periods": periods,
    }


def _included(period, included=True):
    years = [int(token) for token in str(period).split("-") if token.isdigit()]
    return {
        "period": str(period),
        "source": "automated",
        "years": years or [int(period)],
        "included": included,
        "reason": "" if included else "gap",
    }


def _success_selection():
    return _selection([_included("2023"), _included("2019"), _included("2018"), _included("2014", included=False)])


def _success_frames():
    traffic, shared, rate_2023, rate_2019, rate_2018 = _rng_series()
    rows = []
    for index, uhf in enumerate(IDS):
        rows.append(_row(uhf, "2023", rate_2023[index], traffic[index], p_value=0.9))
        rows.append(_row(uhf, "2019", rate_2019[index], traffic[index], p_value=1e-12))
        rows.append(_row(uhf, "2014", float(index + 1), float(index + 1), p_value=1e-30))
    for index, uhf in enumerate(IDS_2018):
        rows.append(_row(uhf, "2018", rate_2018[index], float(index + 1), p_value=1e-9))
    rows.append(_row("99", "2023", None, None, status="insufficient", p_value=0.0))
    locations = _locations(traffic, shared)
    return pd.DataFrame(rows), locations


def _location(uhf, segment, year, mean, hours, days=4, eligible=True):
    return {
        "monitoring_location_id": f"{uhf}-{segment}-{year}",
        "road_segment_id": segment,
        "uhf42_id": str(uhf),
        "year": int(year),
        "mean_hourly": float(mean),
        "hour_balanced_mean": 0.0,
        "n_complete_hours": int(hours),
        "n_days": int(days),
        "n_sessions": 1,
        "months": "6",
        "weekday_hours": int(hours),
        "weekend_hours": 0,
        "eligible": bool(eligible),
        "exclusion_reason": "" if eligible else "ineligible",
    }


def _locations(traffic, shared):
    extra = 6 * traffic - 5 * shared
    rows = []
    for index, uhf in enumerate(IDS):
        for piece in range(4):
            rows.append(_location(uhf, f"seg-{uhf}-{piece}", 2023, shared[index], 50))
            rows.append(_location(uhf, f"seg-{uhf}-{piece}", 2019, shared[index], 50))
        rows.append(_location(uhf, f"seg-{uhf}-4", 2023, shared[index], 30))
        rows.append(_location(uhf, f"seg-{uhf}-4", 2019, shared[index], 50))
        rows.append(_location(uhf, f"seg-{uhf}-extra", 2023, extra[index], 50))
        rows.append(_location(uhf, f"seg-{uhf}-bad", 2023, 9999, 100, eligible=False))
        rows.append(_location(uhf, f"seg-{uhf}-short", 2023, 9999, 100, days=1))
    for index, uhf in enumerate(IDS_2018):
        for piece in range(5):
            rows.append(_location(uhf, f"seg-{uhf}-{piece}", 2018, 1.0, 50))
    return pd.DataFrame(rows)


def _valid(frame, period):
    rows = frame[frame["period"].astype(str) == str(period)]
    return rows[rows["coverage_status"].astype(str) == "valid"].copy()


def _pearson(x_values, y_values):
    result = stats.pearsonr(np.asarray(x_values, dtype=float), np.asarray(y_values, dtype=float))
    interval = result.confidence_interval(confidence_level=0.95)
    return result, interval


def _formula_t(correlation, count):
    return correlation * math.sqrt((count - 2) / (1 - correlation * correlation))


def _scipy_t(x_values, y_values):
    fit = stats.linregress(np.asarray(x_values, dtype=float), np.asarray(y_values, dtype=float))
    return float(fit.slope / fit.stderr)


def _holm(p_values):
    count = len(p_values)
    order = sorted(range(count), key=lambda index: p_values[index])
    adjusted = [None] * count
    running = 0.0
    for rank, index in enumerate(order):
        value = min(1.0, (count - rank) * p_values[index])
        running = max(running, value)
        adjusted[index] = running
    return adjusted


def _pairs(frame, period, traffic_by_uhf):
    rows = _valid(frame, period)
    pairs = []
    for record in rows.to_dict(orient="records"):
        uhf = str(record["uhf42_id"])
        if uhf not in traffic_by_uhf:
            continue
        rate = record["asthma_ed_rate_per_10000"]
        if rate is None or (isinstance(rate, float) and math.isnan(rate)):
            continue
        pairs.append((uhf, float(traffic_by_uhf[uhf]), float(rate)))
    return pairs


def _eligible_locations(frame, min_hours):
    hours = pd.to_numeric(frame["n_complete_hours"], errors="coerce")
    days = pd.to_numeric(frame["n_days"], errors="coerce")
    eligible = frame["eligible"].map(lambda value: bool(value) is True and value is not False)
    return frame.loc[(hours >= min_hours) & (days >= 2) & eligible].copy()


def _neighborhood_means(frame, years, min_segments, min_hours, segment_ids=None):
    rows = _eligible_locations(frame, min_hours)
    if segment_ids is not None:
        rows = rows[rows["road_segment_id"].astype(str).isin(segment_ids)]
    means = {}
    uhfs = sorted(set(rows["uhf42_id"].astype(str)), key=lambda value: int(value))
    for uhf in uhfs:
        year_means = []
        complete = True
        for year in years:
            part = rows[
                (rows["uhf42_id"].astype(str) == uhf) & (rows["year"].astype(int) == int(year))
            ]
            if part.empty:
                complete = False
                break
            segment_means = part.groupby(part["road_segment_id"].astype(str))["mean_hourly"].mean()
            if len(segment_means) < min_segments:
                complete = False
                break
            year_means.append(float(segment_means.mean()))
        if complete and year_means:
            means[uhf] = sum(year_means) / len(year_means)
    return means


def _segment_intersection(frame, selection):
    included = [item for item in selection["periods"] if item["included"]]
    sets = []
    for item in included:
        for year in item["years"]:
            rows = _eligible_locations(frame, 24)
            rows = rows[rows["year"].astype(int) == int(year)]
            sets.append(set(rows["road_segment_id"].astype(str)))
    if not sets:
        return set()
    return set.intersection(*sets)


def _lookup(frame, period):
    match = frame[frame["period"].astype(str) == str(period)]
    assert len(match) == 1, (period, frame)
    return match.iloc[0]


def _blank(value):
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


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


def _assert_ok_row(row, x_values, y_values, role, reason):
    result, interval = _pearson(x_values, y_values)
    correlation = float(result.statistic)
    count = len(x_values)
    assert row["role"] == role
    assert int(row["n"]) == count
    assert float(row["r"]) == pytest.approx(correlation)
    assert int(row["df"]) == count - 2
    assert float(row["t"]) == pytest.approx(_formula_t(correlation, count))
    assert float(row["t"]) == pytest.approx(_scipy_t(x_values, y_values), rel=1e-4)
    assert float(row["p_raw"]) == pytest.approx(float(result.pvalue))
    assert float(row["ci_low"]) == pytest.approx(float(interval.low))
    assert float(row["ci_high"]) == pytest.approx(float(interval.high))
    assert float(row["alpha"]) == pytest.approx(0.05)
    assert bool(row["p_below_alpha"]) is True
    assert row["sign"] == "positive"
    assert row["primary_reason"] == reason
    assert row["test_status"] == "ok"
    assert _blank(row["skip_reason"]) or str(row["skip_reason"]) == ""
    assert row["traffic_source"] == "automated"


def test_preference_year_stays_primary_when_an_earlier_p_is_smaller(monkeypatch, tmp_path):
    reason = (
        "The preference year stays primary when it qualifies. "
        "An earlier year with a smaller p-value stays secondary. "
        "Holm adjusts only secondary tests. Spearman does not replace the Pearson row."
    )
    with _guard_repo():
        functions, _script = _require(reason)
        monkeypatch.chdir(tmp_path)
        calls = _forbid_network(monkeypatch)
        merged, locations = _success_frames()
        selection = _success_selection()
        chosen, why = functions["primary_period"](merged, selection)
        assert chosen == "2023"
        assert why == "primary_preference"
        pearson = functions["pearson_by_period"](merged, selection)
        spearman = functions["spearman_by_period"](merged, selection)
        sensitivity_table = functions["sensitivity"](merged, locations, selection)
        comparison = functions["compare_periods"](merged, pearson, selection)
        assert tuple(pearson.columns) == PEARSON_COLUMNS
        assert tuple(spearman.columns) == SPEARMAN_COLUMNS
        assert tuple(sensitivity_table.columns) == SENSITIVITY_COLUMNS
        assert tuple(comparison.columns) == COMPARISON_COLUMNS
        assert set(pearson["period"].astype(str)) == {"2023", "2019", "2018"}
        traffic, shared, rate_2023, rate_2019, rate_2018 = _rng_series()
        _assert_ok_row(_lookup(pearson, "2023"), traffic, rate_2023, "primary", "primary_preference")
        _assert_ok_row(_lookup(pearson, "2019"), traffic, rate_2019, "secondary", "primary_preference")
        _assert_ok_row(
            _lookup(pearson, "2018"),
            np.arange(1, 31, dtype=float),
            rate_2018,
            "secondary",
            "primary_preference",
        )
        assert _blank(_lookup(pearson, "2023")["p_holm"])
        secondary_p = [
            float(_lookup(pearson, "2019")["p_raw"]),
            float(_lookup(pearson, "2018")["p_raw"]),
        ]
        assert secondary_p[0] < float(_lookup(pearson, "2023")["p_raw"])
        adjusted = _holm(secondary_p)
        assert float(_lookup(pearson, "2019")["p_holm"]) == pytest.approx(adjusted[0])
        assert float(_lookup(pearson, "2018")["p_holm"]) == pytest.approx(adjusted[1])
        spearman_2023 = _lookup(spearman, "2023")
        spearman_result = stats.spearmanr(traffic, rate_2023)
        assert float(spearman_2023["rho"]) == pytest.approx(float(spearman_result.statistic))
        assert float(spearman_2023["p_raw"]) == pytest.approx(float(spearman_result.pvalue))
        assert float(_lookup(pearson, "2023")["r"]) != pytest.approx(float(spearman_2023["rho"]))
        _assert_sensitivity(sensitivity_table, merged, locations, selection, traffic, shared, rate_2023)
        _assert_comparison(comparison, pearson, merged)
        _assert_skipped_period(functions, merged, selection)
        _assert_undefined_correlations(functions)
        _assert_fallback_period(functions)
        assert not any("bootstrap" in str(value) for value in sensitivity_table["analysis_id"])
        assert calls == []
        assert not (tmp_path / "outputs").exists()
        assert not (tmp_path / "data").exists()
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "lib" / "stats.py", ROOT / "scripts" / "09_test_hypotheses.py")
        )
        assert "lib.describe" not in source and "lib/describe" not in source
        assert "lib.plots" not in source and "lib/plots" not in source
        assert "result_figures" not in (ROOT / "lib" / "plots.py").read_text(encoding="utf-8")


def _assert_sensitivity(table, merged, locations, selection, traffic, shared, rate_2023):
    loo = table[table["analysis_id"].astype(str) == "loo_15"].iloc[0]
    kept = [index for index, uhf in enumerate(IDS) if uhf != "15"]
    loo_result, _interval = _pearson(traffic[kept], rate_2023[kept])
    assert str(loo["period"]) == "2023"
    assert int(loo["n"]) == 29
    assert float(loo["r"]) == pytest.approx(float(loo_result.statistic))
    assert sum(str(value).startswith("loo_") for value in table["analysis_id"]) == 30
    full = _neighborhood_means(locations, [2023], 5, 24)
    reduced = _neighborhood_means(locations, [2023], 5, 48)
    assert float(_sensitivity_row(table, "segments_5_hours_24")["r"]) == pytest.approx(
        _correlation(merged, "2023", full)
    )
    assert int(_sensitivity_row(table, "segments_5_hours_24")["n"]) == 30
    assert float(_sensitivity_row(table, "segments_5_hours_48")["r"]) == pytest.approx(
        _correlation(merged, "2023", reduced)
    )
    assert _correlation(merged, "2023", full) != pytest.approx(_correlation(merged, "2023", reduced))
    for segments, hours in ((8, 24), (8, 48), (10, 24), (10, 48)):
        row = _sensitivity_row(table, f"segments_{segments}_hours_{hours}")
        assert int(row["n"]) == 0
        assert _blank(row["r"])
    shared_ids = _segment_intersection(locations, selection)
    shared_means = _neighborhood_means(locations, [2023], 5, 24, shared_ids)
    segment_row = _sensitivity_row(table, "segment_intersection")
    assert int(segment_row["n"]) == 29
    assert float(segment_row["r"]) == pytest.approx(_correlation(merged, "2023", shared_means))
    overlap = set(IDS) & set(IDS_2018)
    overlap_means = {uhf: float(traffic[IDS.index(uhf)]) for uhf in overlap}
    neighborhood_row = _sensitivity_row(table, "neighborhood_intersection")
    assert int(neighborhood_row["n"]) == 29
    assert float(neighborhood_row["r"]) == pytest.approx(_correlation(merged, "2023", overlap_means))
    assert float(segment_row["r"]) != pytest.approx(float(neighborhood_row["r"]))


def _sensitivity_row(table, analysis_id):
    match = table[table["analysis_id"].astype(str) == analysis_id]
    assert len(match) == 1, analysis_id
    return match.iloc[0]


def _correlation(merged, period, means):
    pairs = _pairs(merged, period, means)
    result, _interval = _pearson([item[1] for item in pairs], [item[2] for item in pairs])
    return float(result.statistic)


def _assert_comparison(comparison, pearson, merged):
    assert set(comparison["period"].astype(str)) == {"2023", "2019", "2018"}
    for period, overlap in (("2023", 30), ("2019", 30), ("2018", 29)):
        row = _lookup(comparison, period)
        source = _lookup(pearson, period)
        assert int(row["n"]) == int(source["n"])
        assert float(row["r"]) == pytest.approx(float(source["r"]))
        assert float(row["p_raw"]) == pytest.approx(float(source["p_raw"]))
        if period == "2023":
            assert _blank(row["p_holm"])
        else:
            assert float(row["p_holm"]) == pytest.approx(float(source["p_holm"]))
        assert row["sign"] == "positive"
        assert float(row["ci_low"]) == pytest.approx(float(source["ci_low"]))
        assert float(row["ci_high"]) == pytest.approx(float(source["ci_high"]))
        assert float(row["n_segments_median"]) == pytest.approx(6)
        text = str(row["measurement_consistency"])
        low = float(source["ci_low"])
        high = float(source["ci_high"])
        contains = "true" if low <= 0 <= high else "false"
        assert "direction=positive" in text
        assert "magnitude=" in text
        assert f"contains_0={contains}" in text
        assert f"overlap={overlap}" in text
        assert "consistent_sign" in text
        assert int(row["n"]) != 90
    assert not any(str(period) in {"pooled", "all"} for period in comparison["period"])


def _assert_skipped_period(functions, merged, selection):
    extra = merged.copy()
    extra = pd.concat(
        [
            extra,
            pd.DataFrame(
                [
                    _row("1", "2016", 1.0, 3.0),
                    _row("2", "2016", 2.0, 4.0),
                ]
            ),
        ],
        ignore_index=True,
    )
    mixed = _selection(list(selection["periods"]) + [_included("2016")])
    pearson = functions["pearson_by_period"](extra, mixed)
    row = _lookup(pearson, "2016")
    assert row["role"] == "secondary"
    assert row["test_status"] == "skipped"
    assert row["skip_reason"] == "below_min_uhf"
    assert _blank(row["p_raw"])
    assert _blank(row["p_holm"])
    assert int(row["n"]) == 2
    assert _lookup(pearson, "2023")["role"] == "primary"


def _assert_undefined_correlations(functions):
    constant = pd.DataFrame(
        [_row(uhf, "2023", 10.0, float(index + 1), p_value=0.9) for index, uhf in enumerate(IDS)]
        + [_row(uhf, "2019", float(index + 1), float(index + 1), p_value=1e-9) for index, uhf in enumerate(IDS)]
    )
    selection = _selection([_included("2023"), _included("2019")])
    chosen, why = functions["primary_period"](constant, selection)
    assert chosen == "2023"
    assert why == "primary_preference"
    pearson = functions["pearson_by_period"](constant, selection)
    skipped = _lookup(pearson, "2023")
    assert skipped["role"] == "primary"
    assert skipped["test_status"] == "skipped"
    assert _blank(skipped["p_raw"])
    assert skipped["skip_reason"] not in {"", "below_min_uhf"}
    perfect = pd.DataFrame(
        [_row(uhf, "2023", float(index + 1) * 2, float(index + 1)) for index, uhf in enumerate(IDS)]
    )
    perfect_rows = functions["pearson_by_period"](perfect, _selection([_included("2023")]))
    perfect_row = _lookup(perfect_rows, "2023")
    assert perfect_row["test_status"] == "skipped"
    assert _blank(perfect_row["p_raw"])
    assert perfect_row["skip_reason"] not in {"", "below_min_uhf"}


def _assert_fallback_period(functions):
    traffic = np.arange(1, 31, dtype=float)
    rows = [_row(uhf, "2023", float(index + 1), float(index + 1), p_value=0.5) for index, uhf in enumerate(IDS[:10])]
    rows.extend(_row(uhf, "2019", float(index + 1), traffic[index], p_value=0.4) for index, uhf in enumerate(IDS))
    rows.extend(_row(uhf, "2018", float(index + 1), traffic[index], p_value=1e-9) for index, uhf in enumerate(IDS))
    frame = pd.DataFrame(rows)
    selection = _selection([_included("2023"), _included("2019"), _included("2018")])
    chosen, why = functions["primary_period"](frame, selection)
    assert chosen == "2019"
    assert why == "most_recent_included"
    pearson = functions["pearson_by_period"](frame, selection)
    assert _lookup(pearson, "2019")["role"] == "primary"
    assert _lookup(pearson, "2018")["role"] == "secondary"
    skipped = _lookup(pearson, "2023")
    assert skipped["role"] == "secondary"
    assert skipped["skip_reason"] == "below_min_uhf"
    assert _blank(skipped["p_raw"])


def _copy_tree(src, dest):
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        target = dest / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _run_script(monkeypatch, capsys, tmp_path, script_src, merged, locations, bootstrap=False):
    repo = tmp_path / "project"
    _copy_tree(ROOT / "lib", repo / "lib")
    _copy_tree(ROOT / "config", repo / "config")
    if bootstrap:
        config = repo / "config" / "pipeline.toml"
        text = config.read_text(encoding="utf-8")
        config.write_text(
            text.replace("bootstrap_comparisons = false", "bootstrap_comparisons = true"),
            encoding="utf-8",
        )
    script = repo / SCRIPT_NAME
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_src, script)
    if merged is not None:
        target = repo / MERGED_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(target, index=False)
    if locations is not None:
        target = repo / LOCATION_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        locations.to_parquet(target, index=False)
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
        assert sys.argv == [str(script)], "python scripts/09_test_hypotheses.py takes no arguments"
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


def _printed(text, kind, period):
    import re

    match = re.search(
        rf"^{kind} {period} r=(\S+) ci=([^,\s]+),(\S+) p=(\S+)$",
        text,
        flags=re.MULTILINE,
    )
    assert match, text
    return [float(value) for value in match.groups()]


def test_script_prints_the_primary_interval_and_writes_the_tables(monkeypatch, capsys, tmp_path):
    reason = (
        "A qualifying preference year prints that period, r, the interval, and the p-value "
        "and exits 0. One Pearson row exists per included period."
    )
    with _guard_repo():
        _functions, script = _require(reason)
        merged, locations = _success_frames()
        merged = merged[merged["period"].astype(str) != "2014"].copy()
        code, text, repo = _run_script(monkeypatch, capsys, tmp_path, script, merged, locations)
        assert code == 0, text
        traffic, shared, rate_2023, rate_2019, rate_2018 = _rng_series()
        printed = _printed(text, "primary", "2023")
        result, interval = _pearson(traffic, rate_2023)
        assert printed[0] == pytest.approx(float(result.statistic))
        assert printed[1] == pytest.approx(float(interval.low))
        assert printed[2] == pytest.approx(float(interval.high))
        assert printed[3] == pytest.approx(float(result.pvalue))
        secondary_2019 = _printed(text, "secondary", "2019")
        secondary_2018 = _printed(text, "secondary", "2018")
        result_2019, _interval_2019 = _pearson(traffic, rate_2019)
        result_2018, _interval_2018 = _pearson(np.arange(1, 31, dtype=float), rate_2018)
        assert secondary_2019[0] == pytest.approx(float(result_2019.statistic))
        assert secondary_2018[0] == pytest.approx(float(result_2018.statistic))
        assert "primary 2019" not in text
        pearson = pd.read_csv(repo / PEARSON_PATH)
        assert tuple(pearson.columns) == PEARSON_COLUMNS
        assert set(pearson["period"].astype(str)) == {"2023", "2019", "2018"}
        assert _lookup(pearson, "2023")["role"] == "primary"
        assert _lookup(pearson, "2019")["role"] == "secondary"
        spearman = pd.read_csv(repo / SPEARMAN_PATH)
        sensitivity_table = pd.read_csv(repo / SENSITIVITY_PATH)
        comparison = pd.read_csv(repo / COMPARISON_PATH)
        assert tuple(spearman.columns) == SPEARMAN_COLUMNS
        assert tuple(sensitivity_table.columns) == SENSITIVITY_COLUMNS
        assert tuple(comparison.columns) == COMPARISON_COLUMNS
        assert int(_sensitivity_row(sensitivity_table, "segment_intersection")["n"]) == 29
        assert not any("bootstrap" in str(value) for value in sensitivity_table["analysis_id"])
        assert (repo / PEARSON_PATH).is_file()


def test_every_skipped_period_exits_2(monkeypatch, capsys, tmp_path):
    reason = "When every period is skipped, the terminal prints the reasons and the process exits 2."
    with _guard_repo():
        _functions, script = _require(reason)
        rows = [_row(uhf, "2023", float(index + 1), float(index + 5)) for index, uhf in enumerate(("1", "2"))]
        rows.extend(_row(uhf, "2019", float(index + 1), float(index + 3)) for index, uhf in enumerate(("1", "2")))
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            pd.DataFrame(rows),
            _locations(*_rng_series()[:2]),
        )
        assert code == 2, text
        assert "below_min_uhf" in text
        assert "2023" in text and "2019" in text
        import re

        assert re.search(r"p=\d", text) is None
        assert re.search(r"\br=-?\d", text) is None
        pearson = pd.read_csv(repo / PEARSON_PATH)
        assert pearson["p_raw"].isna().all()
        assert set(pearson["skip_reason"].astype(str)) == {"below_min_uhf"}
        assert "primary" not in set(pearson["role"].astype(str))


def test_missing_merged_file_exits_2(monkeypatch, capsys, tmp_path):
    reason = "A missing merged file exits 2."
    with _guard_repo():
        _functions, script = _require(reason)
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            None,
            _locations(*_rng_series()[:2]),
        )
        assert code == 2, text
        assert "final_merged_dataset.csv" in text
        assert not (repo / PEARSON_PATH).exists()


def test_bootstrap_runs_only_when_the_flag_is_true(monkeypatch, capsys, tmp_path):
    reason = "Bootstrap comparisons run only when stats.bootstrap_comparisons is true."
    with _guard_repo():
        _functions, script = _require(reason)
        merged, locations = _success_frames()
        merged = merged[merged["period"].astype(str) != "2014"].copy()
        code, text, repo = _run_script(
            monkeypatch,
            capsys,
            tmp_path,
            script,
            merged,
            locations,
            bootstrap=True,
        )
        assert code == 0, text
        sensitivity_table = pd.read_csv(repo / SENSITIVITY_PATH)
        bootstrap_rows = sensitivity_table[
            sensitivity_table["analysis_id"].astype(str).str.startswith("bootstrap_")
        ]
        assert set(bootstrap_rows["analysis_id"].astype(str)) == {"bootstrap_2019", "bootstrap_2018"}
        assert int(_sensitivity_row(sensitivity_table, "bootstrap_2019")["n"]) == 30
        assert int(_sensitivity_row(sensitivity_table, "bootstrap_2018")["n"]) == 29
        pearson = pd.read_csv(repo / PEARSON_PATH)
        traffic, _shared, rate_2023, _rate_2019, _rate_2018 = _rng_series()
        result, _interval = _pearson(traffic, rate_2023)
        assert float(_lookup(pearson, "2023")["r"]) == pytest.approx(float(result.statistic))
        assert _lookup(pearson, "2023")["role"] == "primary"
