import os
import subprocess
import sys
import tomllib
from pathlib import Path

from lib.config import load_config

ROOT = Path(__file__).resolve().parents[1]

SCRIPT_ROUTE = (
    "scripts/01_audit_sources.py",
    "scripts/02_select_sources.py",
    "scripts/03_download_traffic.py",
    "scripts/04_clean.py",
    "scripts/05_assign_uhf42.py",
    "scripts/06_aggregate_traffic.py",
    "scripts/07_merge.py",
    "scripts/08_describe.py",
    "scripts/09_test_hypotheses.py",
    "scripts/10_report.py",
)

PINNED_MINIMUMS = (
    "pandas>=2.2",
    "numpy>=1.26",
    "requests>=2.32",
    "geopandas>=1.0",
    "shapely>=2.0",
    "pyproj>=3.6",
    "scipy>=1.12",
    "matplotlib>=3.8",
    "seaborn>=0.13",
    "openpyxl>=3.1",
    "pyarrow>=15",
    "pytest>=8.0",
)

_CANONICAL_ALIASES = (
    ("year", "yr"),
    ("month", "m"),
    ("day", "d"),
    ("hour", "hh"),
    ("minute", "mm"),
    ("volume", "vol"),
    ("segment", "segmentid"),
    ("wkt", "wktgeom"),
    ("direction", "direction"),
    ("borough", "boro"),
    ("request", "requestid"),
)

_BOROUGHS = ("Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island")


def _alias_list(canonical, alias):
    if canonical == alias:
        return [canonical]
    return [canonical, alias]


def _field_table():
    return {
        canonical: _alias_list(canonical, alias)
        for canonical, alias in _CANONICAL_ALIASES
    }


def _expected_config():
    automated = _field_table()
    historical = _field_table()
    aadt = _field_table()
    aadt["aadt"] = ["aadt"]
    aadt["year"] = ["year", "yr", "aadt_year"]
    historical["date"] = ["date"]
    return {
        "api": {
            "app_token": "",
            "page_size": 10000,
            "timeout_seconds": 120,
            "max_retries": 5,
            "retry_backoff_seconds": 2.0,
            "order_field": ":id",
            "user_agent": "macaulay-seminar3-traffic-asthma",
            "automated": {
                "base_url": "https://data.cityofnewyork.us/resource/7ym2-wayt.json",
            },
            "historical": {
                "base_url": "https://data.cityofnewyork.us/resource/btm5-ppia.json",
            },
            "aadt": {
                "base_url": "https://data.ny.gov/resource/6amx-2pbv.json",
            },
        },
        "years": {
            "targets": [2015, 2016, 2017, 2018, 2019, 2022, 2023],
            "excluded": [2020, 2021],
            "primary_preference": 2023,
        },
        "thresholds": {
            "min_observation_dates": 2,
            "min_complete_hours": 1,
            "min_segments_per_uhf": 1,
            "min_uhf_per_period": 30,
            "sensitivity_min_segments": [5, 8, 10],
            "sensitivity_min_hours": [24, 48],
            "high_count_percentile": 99.9,
        },
        "sources": {
            "mix_min_traffic_r": 0.8,
            "aadt_xy_csv": "",
            "preference": ["automated", "historical", "aadt"],
        },
        "asthma": {"raw_dir": "data/raw/asthma"},
        "geography": {
            "git_ref": "master",
            "boundary_tolerance_meters": 1.0,
            "coordinate_round_meters": 1.0,
            "id_crosswalk_csv": "",
            "lion_path": "",
        },
        "pool": {"require_all_years": True},
        "stats": {"alpha": 0.05, "bootstrap_comparisons": False},
        "borough_aliases": {name: [name] for name in _BOROUGHS},
        "fields": {
            "automated": automated,
            "historical": historical,
            "aadt": aadt,
        },
    }


def _assert_contract_value(actual, expected):
    if type(expected) is dict:
        assert type(actual) is dict
        assert set(actual) == set(expected)
        for key, item in expected.items():
            _assert_contract_value(actual[key], item)
        return
    if type(expected) is list:
        assert type(actual) is list
        assert len(actual) == len(expected)
        for got, want in zip(actual, expected):
            _assert_contract_value(got, want)
        return
    assert type(actual) is type(expected)
    assert actual == expected


def test_load_config_returns_architecture_table():
    path = ROOT / "config" / "pipeline.toml"
    assert path.is_file()
    with path.open("rb") as handle:
        parsed = tomllib.load(handle)
    loaded = load_config()
    alias_gaps = []
    if loaded["fields"]["aadt"]["year"] != ["year", "yr", "aadt_year"]:
        alias_gaps.append(
            "fields.aadt.year must be ['year', 'yr', 'aadt_year'] "
            f"(got {loaded['fields']['aadt']['year']!r})"
        )
    if loaded["fields"]["historical"].get("date") != ["date"]:
        alias_gaps.append(
            "fields.historical.date must be ['date'] "
            f"(got {loaded['fields']['historical'].get('date')!r})"
        )
    assert not alias_gaps, "; ".join(alias_gaps)
    assert loaded["fields"]["automated"]["year"] == ["year", "yr"]
    assert loaded["fields"]["historical"]["year"] == ["year", "yr"]
    assert "date" not in loaded["fields"]["automated"]
    assert "date" not in loaded["fields"]["aadt"]
    _assert_contract_value(loaded, _expected_config())
    assert loaded == parsed
    assert loaded["api"]["order_field"] == ":id"
    assert loaded["sources"]["preference"] == ["automated", "historical", "aadt"]
    for canonical, alias in _CANONICAL_ALIASES:
        for section in ("automated", "historical", "aadt"):
            values = loaded["fields"][section][canonical]
            assert canonical in values
            assert alias in values
    assert loaded["fields"]["aadt"]["aadt"] == ["aadt"]
    assert set(loaded["borough_aliases"]) == set(_BOROUGHS)
    for name in _BOROUGHS:
        assert loaded["borough_aliases"][name] == [name]


def test_requirements_pin_minimums():
    lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip()
    ]
    assert lines == list(PINNED_MINIMUMS)


def test_interpreter_is_python_311():
    assert sys.version_info[:2] == (3, 11)


def test_run_all_script_route_has_no_health_route():
    text = (ROOT / "run_all.sh").read_text()
    assert "health" not in text.lower()
    exported = [
        line.strip()
        for line in text.splitlines()
        if line.strip() == "export PYTHONPATH=."
    ]
    assert exported == ["export PYTHONPATH=."]
    commands = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("set ") or line == "export PYTHONPATH=.":
            continue
        commands.append(line)
    assert commands == [f"python {script}" for script in SCRIPT_ROUTE]
    listening = subprocess.run(
        ["lsof", "-nP", "-a", "-p", str(os.getpid()), "-iTCP", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert listening.stdout.strip() == ""


def _run_all_commands(text):
    commands = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("set ") or line == "export PYTHONPATH=.":
            continue
        commands.append(line)
    return commands


def test_run_all_terminal_stops_at_first_nonzero(tmp_path):
    text = (ROOT / "run_all.sh").read_text()
    assert _run_all_commands(text) == [f"python {script}" for script in SCRIPT_ROUTE]
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "run_all.sh").write_text(text)
    (project / SCRIPT_ROUTE[0]).write_text(
        "import sys\n"
        "sys.stderr.write('scripts/01_audit_sources.py\\n')\n"
        "raise SystemExit(1)\n"
    )
    for script in SCRIPT_ROUTE[1:]:
        (project / script).write_text(
            "import sys\n"
            f"sys.stderr.write({script!r} + '\\n')\n"
            "raise SystemExit(0)\n"
        )
    env = os.environ.copy()
    env["PATH"] = str(ROOT / ".venv" / "bin") + os.pathsep + env.get("PATH", "")
    try:
        completed = subprocess.run(
            ["sh", str(project / "run_all.sh")],
            cwd=str(project),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            "run_all.sh did not exit; it must stop at the first non-zero status "
            "and must not listen on a port"
        ) from exc
    terminal = f"{completed.stdout}{completed.stderr}"
    assert completed.returncode != 0
    assert terminal.strip() != ""
    assert "scripts/01_audit_sources.py" in terminal
    for later in SCRIPT_ROUTE[1:]:
        assert later not in terminal
