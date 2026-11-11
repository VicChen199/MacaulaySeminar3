"""Save a verified parquet for each selected traffic year."""

import csv
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.config import load_config
from lib.socrata import SocrataError, paginate

_SELECTION = Path("outputs/audit/source_selection.json")
_SCHEMA = Path("outputs/audit/schema_snapshot.json")
_LOG = Path("outputs/audit/download_log.csv")
_LOG_COLUMNS = [
    "source",
    "year",
    "expected_count",
    "downloaded_count",
    "duplicate_ids",
    "parquet_sha256",
]


def _print_missing(path):
    print(path.as_posix())
    return 2


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _native_id(value):
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            native = item()
        except (TypeError, ValueError):
            return str(value)
        if isinstance(native, bool):
            return str(native)
        if isinstance(native, int):
            return int(native)
        if isinstance(native, str):
            return native
    return str(value)


def _id_bounds(frame):
    if ":id" not in getattr(frame, "columns", []) or len(frame.index) == 0:
        return None, None
    values = [_native_id(value) for value in frame[":id"].tolist()]
    if all(type(value) is int for value in values):
        return min(values), max(values)
    texts = [str(value) for value in values]
    return min(texts), max(texts)


def _write_manifest(directory, row_count, min_id, max_id, digest):
    payload = {
        "row_count": int(row_count),
        "min_id": min_id,
        "max_id": max_id,
        "parquet_sha256": digest,
    }
    (directory / "manifest.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def _append_log(source, year, expected_count, downloaded_count, digest):
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    new_file = (not _LOG.exists()) or _LOG.stat().st_size == 0
    with _LOG.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        if new_file:
            writer.writerow(_LOG_COLUMNS)
        writer.writerow(
            [
                source,
                int(year),
                int(expected_count),
                int(downloaded_count),
                0,
                digest,
            ]
        )


def _clear_failed_check(output):
    if output.is_file():
        output.unlink()
    manifest = output.parent / "manifest.json"
    if manifest.is_file():
        manifest.unlink()


def _selected_years(selection):
    periods = selection.get("periods") if isinstance(selection, dict) else None
    if not isinstance(periods, list):
        return None
    chosen = []
    seen = set()
    for item in periods:
        if not isinstance(item, dict) or not item.get("included"):
            continue
        source = item.get("source")
        years = item.get("years")
        if not isinstance(source, str) or not isinstance(years, list):
            return None
        for year in years:
            try:
                year_number = int(year)
            except (TypeError, ValueError):
                return None
            key = (source, year_number)
            if key in seen:
                continue
            seen.add(key)
            chosen.append(key)
    return chosen


def main():
    config = load_config()
    for path in (_SELECTION, _SCHEMA):
        if not path.is_file():
            return _print_missing(path)
    selection = _load_json(_SELECTION)
    schema = _load_json(_SCHEMA)
    if not isinstance(selection, dict):
        return _print_missing(_SELECTION)
    if not isinstance(schema, dict):
        return _print_missing(_SCHEMA)
    selected = _selected_years(selection)
    if selected is None:
        return _print_missing(_SELECTION)
    for source, year in selected:
        output = Path("data/raw/traffic") / source / f"year={year}" / "records.parquet"
        try:
            expected = paginate(config, source, year, schema, output)
        except SocrataError as exc:
            print(exc.message)
            if exc.exit_code == 2 and not getattr(exc, "leave_folder", False):
                _clear_failed_check(output)
            return int(exc.exit_code)
        frame = pd.read_parquet(output)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        parquet_count = int(len(frame.index))
        min_id, max_id = _id_bounds(frame)
        _write_manifest(output.parent, parquet_count, min_id, max_id, digest)
        _append_log(source, year, expected, parquet_count, digest)
        print(f"{source} {year} {int(expected)} {parquet_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
