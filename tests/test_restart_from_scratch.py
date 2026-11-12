"""The restart caller deletes generated files, then runs run_all.sh."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "restart_from_scratch.sh"


def _plant(project: Path) -> None:
    asthma = project / "data" / "raw" / "asthma" / "export.csv"
    asthma.parent.mkdir(parents=True)
    asthma.write_text("uhf,rate\n101,1.5\n", encoding="utf-8")

    geography = project / "data" / "raw" / "geography"
    geography.mkdir(parents=True)
    (geography / "SOURCE.txt").write_text("abc123\n", encoding="utf-8")
    (geography / "UHF42.geo.json").write_text("{}\n", encoding="utf-8")

    year = project / "data" / "raw" / "traffic" / "automated" / "year=2019"
    year.mkdir(parents=True)
    (year / "pages.jsonl").write_text('{"id":1}\n', encoding="utf-8")
    (year / "checkpoint.json").write_text(
        '{"next_offset": 1, "row_count": 1, "page_hash": "abc"}\n',
        encoding="utf-8",
    )
    (year / "coverage.parquet").write_bytes(b"coverage")
    (year / "records.parquet").write_bytes(b"records")
    (year / "manifest.json").write_text("{}\n", encoding="utf-8")

    outputs = project / "outputs"
    (outputs / "audit").mkdir(parents=True)
    (outputs / "audit" / "schema_snapshot.json").write_text("{}\n", encoding="utf-8")
    (outputs / "results_summary.md").write_text("old\n", encoding="utf-8")

    (project / "config").mkdir()
    (project / "config" / "pipeline.toml").write_text("keep\n", encoding="utf-8")
    (project / "restart_from_scratch.sh").write_bytes(SCRIPT.read_bytes())
    (project / "restart_from_scratch.sh").chmod(0o755)


def _run(project: Path):
    return subprocess.run(
        ["sh", str(project / "restart_from_scratch.sh")],
        cwd="/tmp",
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_script_text_removes_generated_trees_then_runs_run_all():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "rm -rf outputs" in text
    assert "rm -rf data/raw/traffic" in text
    assert "exec ./run_all.sh" in text
    assert "data/raw/asthma" not in text
    assert "data/raw/geography" not in text


def test_restart_deletes_generated_files_and_keeps_inputs(tmp_path):
    project = tmp_path / "project"
    _plant(project)
    (project / "run_all.sh").write_text(
        "#!/bin/sh\necho run_all\nexit 0\n",
        encoding="utf-8",
    )
    (project / "run_all.sh").chmod(0o755)

    completed = _run(project)
    terminal = f"{completed.stdout}{completed.stderr}"

    assert completed.returncode == 0, terminal
    assert "removed outputs" in terminal
    assert "removed data/raw/traffic" in terminal
    assert "restarting from scratch" in terminal
    assert "run_all" in terminal
    assert not (project / "outputs").exists()
    assert not (project / "data" / "raw" / "traffic").exists()
    assert (project / "data" / "raw" / "asthma" / "export.csv").read_text() == (
        "uhf,rate\n101,1.5\n"
    )
    assert (project / "data" / "raw" / "geography" / "SOURCE.txt").read_text() == (
        "abc123\n"
    )
    assert (project / "data" / "raw" / "geography" / "UHF42.geo.json").is_file()
    assert (project / "config" / "pipeline.toml").read_text() == "keep\n"


def test_restart_returns_run_all_status(tmp_path):
    project = tmp_path / "project"
    _plant(project)
    (project / "run_all.sh").write_text(
        "#!/bin/sh\necho stopped\nexit 4\n",
        encoding="utf-8",
    )
    (project / "run_all.sh").chmod(0o755)

    completed = _run(project)

    assert completed.returncode == 4
    assert "stopped" in completed.stdout
    assert not (project / "outputs").exists()
    assert (project / "data" / "raw" / "asthma" / "export.csv").is_file()


def test_restart_refuses_symlink_and_deletes_nothing(tmp_path):
    project = tmp_path / "project"
    _plant(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("keep-me\n", encoding="utf-8")
    traffic = project / "data" / "raw" / "traffic"
    traffic_link_target = tmp_path / "traffic-target"
    traffic.rename(traffic_link_target)
    traffic.symlink_to(traffic_link_target)
    marker = project / "ran.txt"
    (project / "run_all.sh").write_text(
        "#!/bin/sh\necho ran > ran.txt\nexit 0\n",
        encoding="utf-8",
    )
    (project / "run_all.sh").chmod(0o755)

    completed = _run(project)

    assert completed.returncode == 2
    assert "data/raw/traffic is a symlink" in completed.stderr
    assert not marker.exists()
    assert secret.read_text() == "keep-me\n"
    assert (project / "outputs" / "results_summary.md").read_text() == "old\n"
    assert (traffic_link_target / "automated" / "year=2019" / "checkpoint.json").is_file()
    assert (project / "data" / "raw" / "asthma" / "export.csv").is_file()


def test_restart_with_nothing_generated_still_runs(tmp_path):
    project = tmp_path / "empty"
    project.mkdir()
    asthma = project / "data" / "raw" / "asthma" / "export.csv"
    asthma.parent.mkdir(parents=True)
    asthma.write_text("only-input\n", encoding="utf-8")
    (project / "restart_from_scratch.sh").write_bytes(SCRIPT.read_bytes())
    (project / "restart_from_scratch.sh").chmod(0o755)
    (project / "run_all.sh").write_text("#!/bin/sh\necho run_all\nexit 0\n", encoding="utf-8")
    (project / "run_all.sh").chmod(0o755)

    completed = _run(project)

    assert completed.returncode == 0
    assert "removed outputs" not in completed.stdout
    assert "removed data/raw/traffic" not in completed.stdout
    assert "restarting from scratch" in completed.stdout
    assert asthma.read_text() == "only-input\n"
    assert os.listdir(project / "data" / "raw") == ["asthma"]
