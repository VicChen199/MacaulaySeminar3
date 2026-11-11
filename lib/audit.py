"""Classify asthma export TimePeriod labels."""

import re
from pathlib import Path

import pandas as pd

from lib.config import load_config

_EXPORT_SUFFIXES = {".csv", ".xlsx"}
_RANGE_RE = re.compile(
    r"^(\d{4})\s*(?:-|\u2013|\u2014|\u2212|to)\s*(\d{4})$",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"^(\d{4})$")
_PERIOD_COLUMNS = ["period", "period_kind", "constituent_years", "n_rows"]


class AuditError(Exception):
    def __init__(self, message, exit_code=2):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def inventory_asthma(source):
    """Classify TimePeriod labels from a directory or a table.

    Kept periods are returned as a table. A dropped label is not a row.
    This function does not write files and does not call Socrata.
    """
    frame = _load_export(source)
    if len(frame.index) == 0:
        raise AuditError("the export has no rows")
    if "TimePeriod" not in frame.columns:
        names = ", ".join(str(column) for column in frame.columns)
        raise AuditError(names)

    config = load_config()
    targets = {int(year) for year in config["years"]["targets"]}
    excluded = {int(year) for year in config["years"]["excluded"]}

    counts = {}
    order = []
    for raw in frame["TimePeriod"]:
        label = _as_label(raw)
        if label is None:
            continue
        if label not in counts:
            counts[label] = 0
            order.append(label)
        counts[label] += 1

    rows = []
    for label in order:
        classified = _classify(label, targets, excluded)
        if classified is None:
            continue
        kind, years = classified
        rows.append(
            {
                "period": label,
                "period_kind": kind,
                "constituent_years": ",".join(str(year) for year in years),
                "n_rows": counts[label],
            }
        )
    result = pd.DataFrame(rows, columns=_PERIOD_COLUMNS)
    if len(result.index):
        result["n_rows"] = result["n_rows"].astype(int)
    return result


def _load_export(source):
    if isinstance(source, pd.DataFrame):
        return source
    path = Path(source)
    if path.is_dir():
        try:
            files = sorted(
                item
                for item in path.iterdir()
                if item.is_file() and item.suffix.lower() in _EXPORT_SUFFIXES
            )
        except FileNotFoundError:
            raise AuditError(str(path)) from None
        if len(files) != 1:
            raise AuditError(str(path))
        return _read_export(files[0])
    if path.is_file() and path.suffix.lower() in _EXPORT_SUFFIXES:
        return _read_export(path)
    raise AuditError(str(path))


def _read_export(path):
    try:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path, dtype=str)
        return pd.read_excel(path, dtype=str, engine="openpyxl")
    except pd.errors.EmptyDataError:
        raise AuditError("the export has no rows") from None
    except AuditError:
        raise
    except Exception:
        raise AuditError(str(path)) from None


def _as_label(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if pd.isna(value):
            return None
        if value.is_integer():
            return str(int(value))
        return str(value).strip() or None
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.lower() == "nan":
            return None
        return text
    try:
        if pd.isna(value) is True or bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    return text


def _classify(label, targets, excluded):
    ranged = _RANGE_RE.match(label)
    if ranged:
        start = int(ranged.group(1))
        end = int(ranged.group(2))
        if end < start:
            return None
        years = list(range(start, end + 1))
        if any(year in excluded for year in years):
            return None
        if all(year in targets for year in years):
            return "pooled", years
        return None
    single = _YEAR_RE.match(label)
    if single:
        year = int(single.group(1))
        if year in targets:
            return "year", [year]
        return None
    return None
