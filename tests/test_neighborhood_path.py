"""The three neighborhood path charts, and the script that writes them."""

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "11_neighborhood_path.py"
EMPTY_NOTE = "no eligible neighborhood is available"
STEMS = (
    "N107_hunts_point_mott_haven",
    "N106_high_bridge_morrisania",
    "N105_crotona_tremont",
)


def _pdf_text(path):
    return path.read_bytes().decode("latin-1", errors="ignore")


def _row(uhf_id, name, period, rate, traffic, status="valid"):
    return {
        "uhf42_id": str(uhf_id),
        "uhf42_name": name,
        "period": str(period),
        "asthma_ed_rate_per_10000": rate,
        "traffic_mean_vehicles_per_hour": traffic,
        "coverage_status": status,
    }


def _frame():
    rows = [
        _row("107", "Hunts Point - Mott Haven", "2023", "347.2", "771.8"),
        _row("107", "Hunts Point - Mott Haven", "2016", "862", "318.3", "insufficient"),
        _row("107", "Hunts Point - Mott Haven", "2015", "876.5", "837.3"),
        _row("106", "High Bridge - Morrisania", "2015", "846.6", "237.8"),
        _row("105", "Crotona - Tremont", "2019", "433.8", "945.9"),
        _row("105", "Crotona - Tremont", "2015", "737.6", "380.6"),
        _row("999", "Highest Asthma", "2015", "9999", "10"),
        _row("999", "Highest Asthma", "2016", "9999", "10"),
        _row("999", "Highest Asthma", "2017", "9999", "10"),
    ]
    return pd.DataFrame(rows)


def test_paths_keep_valid_years_and_skip_other_neighborhoods(tmp_path):
    from lib.plots import NEIGHBORHOOD_PATHS, _neighborhood_series, neighborhood_path_figures

    assert [item[0] for item in NEIGHBORHOOD_PATHS] == ["107", "106", "105"]
    series = _neighborhood_series(_frame(), "107")
    assert [item["period"] for item in series] == ["2015", "2023"]
    paths = neighborhood_path_figures(_frame(), tmp_path)
    written = sorted(path.name for path in paths)
    expected = sorted(f"{stem}{suffix}" for stem in STEMS for suffix in (".png", ".pdf"))
    assert written == expected
    hunts = _pdf_text(tmp_path / "N107_hunts_point_mott_haven.pdf")
    assert "/Title (107 Hunts Point - Mott Haven; n=2; correlation skipped)" in hunts
    assert "r=" not in hunts
    assert not list(tmp_path.glob("*999*"))
    assert not list(tmp_path.glob("*highest*"))


def test_title_states_reading_and_sign():
    from lib.plots import _path_reading, _path_sign, _path_title

    assert _path_reading(0.2, 0.40) == "null"
    assert _path_reading(0.9, 0.01) == "positive"
    assert _path_reading(-0.9, 0.01) == "negative"
    assert _path_sign(0.0) == "zero"
    rising = [
        {"period": str(year), "traffic": float(year), "rate": float(year) * 3, "name": "Rise"}
        for year in (2015, 2016, 2017, 2018, 2019)
    ]
    rising[2]["rate"] = 8.0
    title = _path_title("105", "Crotona - Tremont", rising)
    assert "n=5" in title
    assert "r=" in title
    assert "p=" in title
    assert "reading=null" in title
    assert "sign=positive" in title
    detected = [
        {"period": str(2015 + index), "traffic": float(index + 1), "rate": float(index + 1), "name": "Rise"}
        for index in range(5)
    ]
    detected[-1]["rate"] = 4.5
    detected_title = _path_title("105", "Crotona - Tremont", detected)
    assert "reading=positive" in detected_title
    assert "sign=positive" in detected_title
    flat = [
        {"period": str(year), "traffic": 10.0, "rate": float(year), "name": "Flat"}
        for year in (2015, 2016, 2017)
    ]
    assert _path_title("106", "High Bridge - Morrisania", flat).endswith("correlation skipped")


def test_missing_neighborhood_is_annotated(tmp_path):
    from lib.plots import neighborhood_path_figures

    frame = _frame()
    frame = frame[frame["uhf42_id"] != "106"]
    neighborhood_path_figures(frame, tmp_path)
    text = _pdf_text(tmp_path / "N106_high_bridge_morrisania.pdf")
    assert EMPTY_NOTE in text


def test_script_prints_paths_and_missing_file_exits_2(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    missing = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 2
    assert "outputs/data/final_merged_dataset.csv" in f"{missing.stdout}{missing.stderr}"

    merged = tmp_path / "outputs" / "data" / "final_merged_dataset.csv"
    merged.parent.mkdir(parents=True)
    _frame().to_csv(merged, index=False)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for stem in STEMS:
        for suffix in (".png", ".pdf"):
            relative = f"outputs/figures/neighborhood/{stem}{suffix}"
            assert relative in completed.stdout
            assert (tmp_path / relative).is_file()
    assert "outputs/figures/sample/" not in completed.stdout
    assert "outputs/figures/results/" not in completed.stdout
