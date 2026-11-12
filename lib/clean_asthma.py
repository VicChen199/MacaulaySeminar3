"""Clean UHF42 asthma ED rows for the study periods."""

import math
import numbers
import re

import pandas as pd

_REQUIRED = (
    "TimePeriod",
    "GeoID",
    "uhf42_name",
    "borough",
    "asthma_ed_count",
    "asthma_ed_rate_per_10000",
)
_COLUMNS = [
    "uhf42_id",
    "uhf42_name",
    "borough",
    "period",
    "period_kind",
    "constituent_years",
    "asthma_ed_count",
    "asthma_ed_rate_per_10000",
]
_SUPPRESSED = {"*", "s", "suppressed"}
_RANGE_RE = re.compile(
    r"^(\d{4})\s*(?:-|\u2013|\u2014|\u2212|to)\s*(\d{4})$",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"^(\d{4})$")


class CleanError(Exception):
    def __init__(self, message, exit_code=2):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def clean_asthma(frame, config):
    """Return one row per neighborhood and kept period.

    Does not write files and does not use the network. Age and measure
    columns are ignored. A dropped period label is not a row.
    """
    frame = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
    missing = [name for name in _REQUIRED if name not in frame.columns]
    if missing:
        raise CleanError(_present_columns(frame), 2)

    targets = {int(year) for year in config["years"]["targets"]}
    excluded = {int(year) for year in config["years"]["excluded"]}
    boroughs = _borough_lookup(config)
    kept = []
    seen_keys = set()
    years_by_id = {}
    for record in frame.to_dict(orient="records"):
        label = _label(record.get("TimePeriod"))
        if label is None:
            continue
        classified = _classify(label, targets, excluded)
        if classified is None:
            continue
        kind, years = classified
        uhf_id = _uhf_id(record.get("GeoID"))
        count = _measure(record.get("asthma_ed_count"))
        rate = _measure(record.get("asthma_ed_rate_per_10000"))
        borough = _borough(record.get("borough"), boroughs)
        key = (uhf_id, label)
        if key in seen_keys:
            raise CleanError(f"{uhf_id} {label}", 2)
        year_set = set(years)
        previous = years_by_id.setdefault(uhf_id, [])
        for other_label, other_years in previous:
            if year_set & other_years:
                raise CleanError(f"{uhf_id} {other_label} {label}", 2)
        previous.append((label, year_set))
        seen_keys.add(key)
        kept.append(
            {
                "uhf42_id": uhf_id,
                "uhf42_name": _text(record.get("uhf42_name")),
                "borough": borough,
                "period": label,
                "period_kind": kind,
                "constituent_years": ",".join(str(year) for year in years),
                "asthma_ed_count": count,
                "asthma_ed_rate_per_10000": rate,
            }
        )

    if not kept:
        raise CleanError("zero asthma rows", 2)

    result = pd.DataFrame(kept, columns=_COLUMNS)
    result["asthma_ed_count"] = pd.to_numeric(result["asthma_ed_count"])
    result["asthma_ed_rate_per_10000"] = pd.to_numeric(
        result["asthma_ed_rate_per_10000"]
    )
    return result


def _present_columns(frame):
    return ", ".join(str(column) for column in frame.columns)


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


def _borough_lookup(config):
    lookup = {}
    for canonical, aliases in config["borough_aliases"].items():
        name = str(canonical).strip()
        lookup[name.casefold()] = name
        for alias in aliases:
            lookup[str(alias).strip().casefold()] = name
    return lookup


def _borough(value, lookup):
    token = _token(value)
    canonical = lookup.get(token.casefold())
    if canonical is None:
        raise CleanError(token, 2)
    return canonical


def _measure(value):
    if _missing(value):
        return None
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        raise CleanError(str(value), 2)
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            raise CleanError(_token(value) or str(value), 2)
        return number
    text = str(value).strip()
    if text == "":
        return None
    if text.casefold() in _SUPPRESSED:
        return None
    try:
        number = float(text)
    except ValueError:
        raise CleanError(text, 2) from None
    if not math.isfinite(number):
        raise CleanError(text, 2)
    return number


def _uhf_id(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        raise CleanError(str(value), 2)
    if _missing(value):
        raise CleanError("GeoID", 2)
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            raise CleanError(_token(value) or "GeoID", 2)
        return _decimal_id(number)
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "none", "<na>"}:
        raise CleanError(text or "GeoID", 2)
    try:
        number = float(text)
    except ValueError:
        raise CleanError(text, 2) from None
    if not math.isfinite(number):
        raise CleanError(text, 2)
    return _decimal_id(number)


def _decimal_id(number):
    if float(number).is_integer():
        return str(int(number))
    return format(float(number), "f").rstrip("0").rstrip(".")


def _label(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return None
    if _missing(value):
        return None
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            return None
        if number.is_integer():
            return str(int(number))
        return str(value).strip() or None
    text = str(value).strip()
    if text == "" or text.casefold() == "nan":
        return None
    return text


def _text(value):
    if _missing(value):
        return ""
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return str(value)
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if math.isfinite(number) and number.is_integer():
            return str(int(number))
    return str(value).strip()


def _token(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return str(value)
    if _missing(value):
        return ""
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if math.isfinite(number) and number.is_integer():
            return str(int(number))
        return str(value).strip()
    return str(value).strip()


def _missing(value):
    if value is None:
        return True
    if isinstance(value, str):
        return False
    if isinstance(value, (bytes, bytearray)):
        return False
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return missing is True or missing is pd.NA
