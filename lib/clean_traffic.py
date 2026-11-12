"""Clean one traffic year to local timestamps and typed volumes."""

import math
import numbers
from collections import defaultdict

import numpy as np
import pandas as pd

from lib.clean_asthma import CleanError
from lib.config import load_config

_QUARTERS = {0, 15, 30, 45}
_OUTPUT = [
    ":id",
    "source",
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "vol",
    "segment",
    "wkt",
    "direction_normalized",
    "borough",
    "request",
    "ts_local",
    "monitoring_location_id",
    "hour_complete",
    "high_count_flag",
]
def clean_traffic(frame, config, source=None):
    """Return cleaned rows, duplicates removed, and high-count flags.

    Does not write files and does not use the network. Exact duplicate
    rows are removed. Zero volumes stay. Negative volumes are excluded.
    An automated integer minute outside {0, 15, 30, 45} is excluded.
    """
    if source is None and isinstance(config, str):
        source = config
        config = load_config()
    frame = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
    _reject_utc(frame)
    fields = _fields(config, source)
    resolved = _resolve_fields(frame.columns, fields)
    _require_fields(frame, resolved, source)

    ids = [_id_text(value) for value in _values(frame, ":id")]
    directions = [
        _direction(value, ids[index])
        for index, value in enumerate(_values(frame, resolved["direction"]))
    ]
    boroughs = _borough_lookup(config)
    borough_values = [
        _borough(value, boroughs) for value in _values(frame, resolved["borough"])
    ]
    minute_mode = _minute_mode(frame, resolved, source, ids)
    years = _integers(_values(frame, resolved["year"]), ids, required=True)
    if source == "aadt":
        months = _optional_integers(_values(frame, resolved.get("month")), ids)
        days = _optional_integers(_values(frame, resolved.get("day")), ids)
        hours = [None] * len(frame.index)
        minutes = [None] * len(frame.index)
        volumes = _volumes(_values(frame, _aadt_column(resolved)))
        volumes = [None if value is None else value / 24.0 for value in volumes]
        hour_complete = False
    else:
        months = _integers(_values(frame, resolved["month"]), ids, required=True)
        days = _integers(_values(frame, resolved["day"]), ids, required=True)
        hours = _integers(_values(frame, resolved["hour"]), ids, required=True)
        minutes = minute_mode
        volume_column = resolved.get("volume")
        volumes = _volumes(_values(frame, volume_column))
        hour_complete = source == "historical"

    segments = [_text(value) for value in _values(frame, resolved.get("segment"))]
    wkts = [_text(value) for value in _values(frame, resolved.get("wkt"))]
    requests = [_text(value) for value in _values(frame, resolved.get("request"))]
    duplicate = frame.duplicated(keep="first").tolist()

    kept = []
    exclusions = []
    removed = 0
    for index, is_duplicate in enumerate(duplicate):
        if is_duplicate:
            removed += 1
            exclusions.append(
                {
                    "stage": "clean",
                    "key": ids[index],
                    "reason": "duplicate_row",
                    "source": source,
                }
            )
            continue
        volume = volumes[index]
        if volume is not None and volume < 0:
            exclusions.append(
                {
                    "stage": "clean",
                    "key": ids[index],
                    "reason": "negative_volume",
                    "source": source,
                }
            )
            continue
        minute = minutes[index]
        if (
            source == "automated"
            and minute is not None
            and minute not in _QUARTERS
        ):
            exclusions.append(
                {
                    "stage": "clean",
                    "key": ids[index],
                    "reason": "minute_not_quarter",
                    "source": source,
                }
            )
            continue
        kept.append(
            {
                ":id": ids[index],
                "source": source,
                "year": years[index],
                "month": months[index],
                "day": days[index],
                "hour": hours[index],
                "minute": minutes[index],
                "vol": volume,
                "segment": segments[index],
                "wkt": wkts[index],
                "direction_normalized": directions[index],
                "borough": borough_values[index],
                "request": requests[index],
                "monitoring_location_id": "",
                "hour_complete": hour_complete,
            }
        )

    flags = _high_flags(kept, config)
    for row, flag in zip(kept, flags):
        row["high_count_flag"] = flag
    result = _assemble(kept, source)
    result.attrs["exclusions"] = exclusions
    return result, removed, int(sum(flags))


def _fields(config, source):
    try:
        fields = config["fields"][source]
    except (KeyError, TypeError):
        raise CleanError(str(source), 2) from None
    if not isinstance(fields, dict):
        raise CleanError(str(source), 2)
    return fields


def _reject_utc(frame):
    names = [str(column) for column in frame.columns if "utc" in str(column).lower()]
    if names:
        raise CleanError(", ".join(names), 2)


def _resolve_fields(columns, fields):
    resolved = {":id": _resolve(columns, [":id"])}
    for name, aliases in fields.items():
        resolved[name] = _resolve(columns, aliases or [])
    return resolved


def _resolve(columns, aliases):
    lookup = {str(column): column for column in columns}
    for alias in aliases:
        found = lookup.get(str(alias))
        if found is not None:
            return found
    return None


def _require_fields(frame, resolved, source):
    needed = ["year", "direction", "borough"]
    if source == "aadt":
        if resolved.get("aadt") is None and resolved.get("volume") is None:
            raise CleanError(_present_columns(frame), 2)
    else:
        needed.extend(["month", "day", "hour", "volume"])
    for name in needed:
        if resolved.get(name) is None:
            raise CleanError(_present_columns(frame), 2)


def _aadt_column(resolved):
    if resolved.get("aadt") is not None:
        return resolved["aadt"]
    return resolved.get("volume")


def _present_columns(frame):
    return ", ".join(str(column) for column in frame.columns)


def _values(frame, column):
    if column is None or column not in getattr(frame, "columns", []):
        return [None] * len(frame.index)
    return frame[column].tolist()


def _minute_mode(frame, resolved, source, ids):
    column = resolved.get("minute")
    if source == "aadt":
        return [None] * len(frame.index)
    if source == "historical":
        if column is None:
            return [None] * len(frame.index)
        parsed = [_clock_piece(value) for value in _values(frame, column)]
        bad = []
        first_id = ""
        first_year = ""
        years = _values(frame, resolved.get("year"))
        for index, (kind, payload) in enumerate(parsed):
            if kind == "missing":
                continue
            if kind == "int" and payload == 0:
                continue
            token = payload if kind == "bad" else str(payload)
            if token not in bad:
                bad.append(token)
            if not first_id:
                first_id = ids[index]
                first_year = _year_token(years[index])
        if bad:
            raise CleanError(
                " ".join(bad + [str(source), first_year, first_id]).strip(),
                2,
            )
        return [None if kind == "missing" else payload for kind, payload in parsed]
    parsed = []
    if column is None:
        parsed = [("missing", None)] * len(frame.index)
    else:
        parsed = [_clock_piece(value) for value in _values(frame, column)]
    bad = []
    first_id = ""
    first_year = ""
    years = _values(frame, resolved.get("year"))
    for index, (kind, payload) in enumerate(parsed):
        if kind == "missing":
            continue
        if kind == "int" and (payload in _QUARTERS or source == "automated"):
            continue
        token = payload if kind == "bad" else str(payload)
        if token not in bad:
            bad.append(token)
        if not first_id:
            first_id = ids[index]
            first_year = _year_token(years[index])
    if bad:
        raise CleanError(
            " ".join(bad + [str(source), first_year, first_id]).strip(),
            2,
        )
    return [None if kind == "missing" else payload for kind, payload in parsed]


def _year_token(value):
    kind, payload = _clock_piece(value)
    if kind == "int":
        return str(payload)
    if kind == "bad":
        return str(payload)
    return ""


def _integers(values, ids, required):
    parsed = []
    for index, value in enumerate(values):
        kind, payload = _clock_piece(value)
        if kind == "missing":
            if required:
                raise CleanError(ids[index], 2)
            parsed.append(None)
            continue
        if kind == "bad":
            raise CleanError(str(payload), 2)
        parsed.append(payload)
    return parsed


def _optional_integers(values, ids):
    return _integers(values, ids, required=False)


def _volumes(values):
    parsed = []
    for value in values:
        kind, payload = _number(value)
        if kind == "bad":
            raise CleanError(str(payload), 2)
        parsed.append(None if kind == "missing" else payload)
    return parsed


def _clock_piece(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return "bad", str(value)
    if _missing(value):
        return "missing", None
    if isinstance(value, numbers.Integral):
        return "int", int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        if math.isfinite(number) and number.is_integer():
            return "int", int(number)
        return "bad", _token(value) or str(value)
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "none", "<na>"}:
        return "missing", None
    try:
        number = float(text)
    except ValueError:
        return "bad", text
    if not math.isfinite(number) or not number.is_integer():
        return "bad", text
    return "int", int(number)


def _number(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return "bad", str(value)
    if _missing(value):
        return "missing", None
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            return "bad", _token(value) or str(value)
        return "float", number
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "none", "<na>"}:
        return "missing", None
    try:
        number = float(text)
    except ValueError:
        return "bad", text
    if not math.isfinite(number):
        return "bad", text
    return "float", number


def _direction(value, row_id):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        text = str(value).strip()
    elif _missing(value):
        text = ""
    elif isinstance(value, numbers.Integral):
        text = str(int(value))
    elif isinstance(value, numbers.Real):
        number = float(value)
        if math.isfinite(number) and number.is_integer():
            text = str(int(number))
        else:
            text = str(value).strip()
    else:
        text = str(value).strip()
        if text.casefold() in {"nan", "none", "<na>"}:
            text = ""
    if text == "":
        raise CleanError(row_id, 2)
    return text


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
    if token.casefold() in {"nan", "none", "<na>"}:
        token = ""
    canonical = lookup.get(token.casefold()) if token else None
    if canonical is None:
        raise CleanError(token, 2)
    return canonical


def _high_flags(rows, config):
    flags = [False] * len(rows)
    if not rows:
        return flags
    percentile = float(config["thresholds"]["high_count_percentile"])
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        if row["vol"] is not None:
            groups[row["year"]].append(index)
    for indexes in groups.values():
        values = np.asarray([rows[index]["vol"] for index in indexes], dtype=float)
        if values.size == 0:
            continue
        threshold = float(np.quantile(values, percentile / 100.0))
        for index in indexes:
            if rows[index]["vol"] > threshold:
                flags[index] = True
    return flags


def _assemble(rows, source):
    frame = pd.DataFrame(rows, columns=[column for column in _OUTPUT if column != "ts_local"])
    for column in ("year", "month", "day", "hour", "minute"):
        frame[column] = pd.array(
            [None if _missing(value) else int(value) for value in frame[column]],
            dtype="Int64",
        )
    frame["vol"] = pd.to_numeric(frame["vol"], errors="coerce")
    frame["hour_complete"] = pd.Series(
        [bool(value) for value in frame["hour_complete"]] if len(frame.index) else [],
        index=frame.index,
        dtype=bool,
    )
    frame["high_count_flag"] = pd.Series(
        [bool(value) for value in frame["high_count_flag"]] if len(frame.index) else [],
        index=frame.index,
        dtype=bool,
    )
    frame["monitoring_location_id"] = pd.Series(
        [""] * len(frame.index),
        index=frame.index,
        dtype="object",
    )
    frame["ts_local"] = _timestamps(frame, source)
    return frame[_OUTPUT]


def _timestamps(frame, source):
    empty = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[us]")
    if source == "aadt" or len(frame.index) == 0:
        return empty.dt.tz_localize("America/New_York")
    minute = frame["minute"].astype("float64").fillna(0)
    parts = {
        "year": frame["year"].astype("float64"),
        "month": frame["month"].astype("float64"),
        "day": frame["day"].astype("float64"),
        "hour": frame["hour"].astype("float64"),
        "minute": minute,
    }
    naive = pd.to_datetime(parts, errors="coerce")
    return naive.dt.tz_localize(
        "America/New_York",
        ambiguous="NaT",
        nonexistent="NaT",
    )


def _id_text(value):
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


def _text(value):
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
        text = str(value).strip()
        if text.casefold() == "nan":
            return ""
        return text
    text = str(value).strip()
    if text.casefold() in {"nan", "none", "<na>"}:
        return ""
    return text


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
        return format(float(number), "f").rstrip("0").rstrip(".")
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
