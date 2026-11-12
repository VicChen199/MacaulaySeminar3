"""Turn quarter-hour counts into neighborhood traffic exposure."""

import math
import numbers
from collections import defaultdict
from datetime import date as date_cls

import pandas as pd

_QUARTERS = (0, 15, 30, 45)
_QUARTER_SET = set(_QUARTERS)
_HOUR_COLUMNS = (
    "monitoring_location_id",
    "road_segment_id",
    "uhf42_id",
    "source",
    "year",
    "date",
    "hour",
    "direction_normalized",
    "session",
    "vol",
)
_LOCATION_COLUMNS = (
    "monitoring_location_id",
    "road_segment_id",
    "uhf42_id",
    "year",
    "mean_hourly",
    "hour_balanced_mean",
    "n_complete_hours",
    "n_days",
    "n_sessions",
    "months",
    "weekday_hours",
    "weekend_hours",
    "eligible",
    "exclusion_reason",
)
_SEGMENT_COLUMNS = (
    "road_segment_id",
    "uhf42_id",
    "year",
    "mean_hourly",
    "hour_balanced_mean",
    "n_locations",
    "n_complete_hours",
    "n_days",
    "eligible",
    "own_segment",
)
_NEIGHBORHOOD_COLUMNS = (
    "uhf42_id",
    "period",
    "traffic_mean_vehicles_per_hour",
    "traffic_mean_hour_balanced",
    "traffic_median_vehicles_per_hour",
    "traffic_sd_vehicles_per_hour",
    "n_road_segments",
    "n_monitoring_locations",
    "n_observation_days",
    "n_complete_hours",
    "n_sessions",
    "months_represented",
    "weekday_weekend_coverage",
    "traffic_source",
    "coverage_status",
)


class AggregateError(Exception):
    def __init__(self, message, exit_code=2):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def complete_hours(frame):
    """Return complete hours. Does not write files and does not use the network."""
    records = _records(frame)
    observed = _observed_directions(records)
    directional = _directional_hours(records)
    combined = _combine_directions(directional, observed)
    return _hours_frame(combined)


def location_years(hours):
    """Return one row per location per year. Loads config itself."""
    config = _load_config()
    minimum_dates = int(config["thresholds"]["min_observation_dates"])
    minimum_hours = int(config["thresholds"]["min_complete_hours"])
    grouped = defaultdict(list)
    for record in _records(hours):
        location = _text(record.get("monitoring_location_id"))
        year = _int_or_none(record.get("year"))
        grouped[(location, year)].append(record)
    rows = []
    for (location, year), items in grouped.items():
        rows.append(_location_row(location, year, items, minimum_dates, minimum_hours))
    rows.sort(key=lambda row: (_year_sort(row["year"]), row["monitoring_location_id"]))
    return _project(rows, _LOCATION_COLUMNS, _LOCATION_DTYPES, ("dates",))


def segment_years(location_years):
    """Return one row per road segment per year per UHF."""
    grouped = {}
    for record in _records(location_years):
        segment_id, own = _segment_identity(
            record.get("road_segment_id"),
            record.get("monitoring_location_id"),
        )
        uhf = _uhf(record.get("uhf42_id"))
        year = _int_or_none(record.get("year"))
        key = (segment_id, uhf, year)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {"own": [], "rows": []}
            grouped[key] = bucket
        bucket["own"].append(own)
        bucket["rows"].append(record)
    rows = []
    for (segment_id, uhf, year), bucket in grouped.items():
        rows.append(
            _segment_row(
                segment_id,
                uhf,
                year,
                bucket["rows"],
                all(bucket["own"]),
            )
        )
    rows.sort(key=lambda row: (_year_sort(row["year"]), _uhf_sort(row["uhf42_id"]), row["road_segment_id"]))
    return _project(
        rows,
        _SEGMENT_COLUMNS,
        _SEGMENT_DTYPES,
        ("n_sessions", "months", "dates", "weekday_hours", "weekend_hours"),
    )


def neighborhood_periods(segment_years, periods):
    """Return one row per neighborhood per period. Loads config itself."""
    config = _load_config()
    minimum_segments = int(config["thresholds"]["min_segments_per_uhf"])
    require_all = bool(config["pool"]["require_all_years"])
    by_key = _segment_index(_records(segment_years))
    rows = []
    for period in _included_periods(periods):
        years = period["years"]
        uhfs = sorted(
            {uhf for (uhf, year) in by_key if year in years},
            key=_uhf_sort,
        )
        for uhf in uhfs:
            rows.append(
                _neighborhood_row(
                    uhf,
                    period,
                    years,
                    by_key,
                    minimum_segments,
                    require_all,
                )
            )
    return _typed_frame(rows, _NEIGHBORHOOD_COLUMNS, _NEIGHBORHOOD_DTYPES)


def _load_config():
    from lib.config import load_config

    return load_config()


def _records(frame):
    if frame is None:
        return []
    carried = None
    if isinstance(frame, pd.DataFrame):
        table = frame
        carried = frame.attrs.get("aggregate_fields")
    else:
        table = pd.DataFrame(frame)
    if len(table.index) == 0:
        return []
    records = table.to_dict(orient="records")
    if not isinstance(carried, dict):
        return records
    for field, values in carried.items():
        if not isinstance(values, (list, tuple)) or len(values) != len(records):
            continue
        for record, value in zip(records, values):
            if field not in record:
                record[field] = value
    return records


def _observed_directions(records):
    observed = defaultdict(set)
    for record in records:
        location = _text(record.get("monitoring_location_id"))
        direction = _direction(record)
        if direction == "":
            raise AggregateError(location, 2)
        observed[location].add(direction)
    for location, labels in observed.items():
        if len(labels) > 2:
            raise AggregateError(location, 2)
    return observed


def _directional_hours(records):
    automated = {}
    historical = {}
    aadt = []
    for record in records:
        source = _text(record.get("source"))
        if source == "automated":
            _add_automated(automated, record)
        elif source == "historical":
            _add_historical(historical, record)
        elif source == "aadt":
            item = _aadt_hour(record)
            if item is not None:
                aadt.append(item)
        else:
            raise AggregateError(source or "source", 2)
    hours = []
    hours.extend(_automated_hours(automated))
    hours.extend(historical.values())
    hours.extend(aadt)
    return hours


def _add_automated(buckets, record):
    minute, token = _quarter_minute(record)
    if minute is None and token is None:
        return
    if minute not in _QUARTER_SET:
        raise AggregateError(token, 2)
    volume = _volume(record.get("vol", record.get("volume")))
    if volume is None:
        return
    located = _located(record)
    if located is None:
        raise AggregateError("ts_local", 2)
    date, hour, year = located
    direction = _direction(record)
    location = _text(record.get("monitoring_location_id"))
    request = _request(record)
    session = _session(location, date, direction, request)
    identity = (location, date, hour, direction, session, minute)
    current = buckets.get(identity)
    if current is None:
        buckets[identity] = {
            "volume": volume,
            "meta": _meta(record, location, date, hour, year, direction, request, session),
        }
        return
    current["volume"] += volume


def _automated_hours(buckets):
    grouped = defaultdict(dict)
    metas = {}
    for (location, date, hour, direction, session, minute), payload in buckets.items():
        key = (location, date, hour, direction, session)
        grouped[key][minute] = payload["volume"]
        metas.setdefault(key, payload["meta"])
    hours = []
    for key, minutes in grouped.items():
        if set(minutes) < _QUARTER_SET:
            continue
        meta = dict(metas[key])
        meta["vol"] = sum(minutes[minute] for minute in _QUARTERS)
        hours.append(meta)
    return hours


def _add_historical(buckets, record):
    if not _hour_complete(record):
        return
    volume = _volume(record.get("vol", record.get("volume")))
    if volume is None:
        return
    located = _located(record)
    if located is None:
        raise AggregateError("ts_local", 2)
    date, hour, year = located
    direction = _direction(record)
    location = _text(record.get("monitoring_location_id"))
    request = _request(record)
    session = _session(location, date, direction, request)
    key = (location, date, hour, direction, session)
    current = buckets.get(key)
    if current is None:
        item = _meta(record, location, date, hour, year, direction, request, session)
        item["vol"] = volume
        buckets[key] = item
        return
    current["vol"] += volume


def _aadt_hour(record):
    volume = _volume(record.get("vol", record.get("volume")))
    if volume is None:
        return None
    located = _located(record)
    if located is None:
        date = ""
        hour = _int_or_none(record.get("hour"))
        year = _int_or_none(record.get("year"))
    else:
        date, hour, year = located
    direction = _direction(record)
    location = _text(record.get("monitoring_location_id"))
    request = _request(record)
    session = _session(location, date, direction, request)
    item = _meta(record, location, date, hour, year, direction, request, session)
    item["vol"] = volume
    return item


def _meta(record, location, date, hour, year, direction, request, session):
    return {
        "monitoring_location_id": location,
        "road_segment_id": _text(record.get("road_segment_id", record.get("segment"))),
        "uhf42_id": _uhf(record.get("uhf42_id")),
        "source": _text(record.get("source")),
        "year": year,
        "date": date,
        "hour": hour,
        "direction_normalized": direction,
        "session": session,
        "request": request,
        "vol": 0.0,
    }


def _combine_directions(hours, observed):
    kept = []
    grouped = defaultdict(list)
    for item in hours:
        location = item["monitoring_location_id"]
        labels = observed.get(location, set())
        if len(labels) <= 1:
            kept.append(_public_hour(item))
            continue
        pair = item["request"] if item["request"] else ""
        grouped[(location, item["date"], item["hour"], pair)].append(item)
    for (location, date, hour, _pair), items in grouped.items():
        labels = observed.get(location, set())
        by_direction = defaultdict(list)
        for item in items:
            by_direction[item["direction_normalized"]].append(item)
        if not labels or not labels <= set(by_direction):
            continue
        ordered = sorted(labels)
        total = 0.0
        for label in ordered:
            total += sum(item["vol"] for item in by_direction[label])
        base = dict(items[0])
        direction = "|".join(ordered)
        base["direction_normalized"] = direction
        base["vol"] = total
        base["session"] = (
            base["request"] if base["request"] else _session(location, date, direction, "")
        )
        base["hour"] = hour
        base["date"] = date
        kept.append(_public_hour(base))
    kept.sort(key=_hour_sort)
    return kept


def _public_hour(item):
    return {column: item.get(column) for column in _HOUR_COLUMNS}


def _hour_sort(item):
    return (
        _year_sort(item.get("year")),
        item.get("monitoring_location_id") or "",
        item.get("date") or "",
        -1 if item.get("hour") is None else int(item["hour"]),
        item.get("session") or "",
        item.get("direction_normalized") or "",
    )


def _location_row(location, year, items, minimum_dates, minimum_hours):
    by_date = defaultdict(list)
    by_hour = defaultdict(list)
    weekday_rows = []
    weekend_rows = []
    sessions = set()
    months = set()
    weekday_hours = 0
    weekend_hours = 0
    for item in items:
        volume = _volume(item.get("vol"))
        if volume is None:
            continue
        stamp = _parse_date(item.get("date"))
        by_date[item.get("date") or ""].append(volume)
        clock = _int_or_none(item.get("hour"))
        if clock is not None:
            by_hour[clock].append(volume)
        if stamp is not None and stamp.weekday() <= 4:
            weekday_hours += 1
            weekday_rows.append((clock, volume))
        elif stamp is not None:
            weekend_hours += 1
            weekend_rows.append((clock, volume))
        if stamp is not None:
            months.add(stamp.month)
        session = _text(item.get("session"))
        if session:
            sessions.add(session)
    daily = [sum(values) / len(values) for values in by_date.values() if values]
    n_days = len(daily)
    n_hours = int(sum(len(values) for values in by_date.values()))
    eligible = n_days >= minimum_dates and n_hours >= minimum_hours
    return {
        "monitoring_location_id": location,
        "road_segment_id": _first_text(items, "road_segment_id"),
        "uhf42_id": _first_uhf(items),
        "source": _first_text(items, "source"),
        "year": year,
        "mean_hourly": _mean(daily),
        "hour_balanced_mean": _hour_balanced(by_hour, weekday_rows, weekend_rows),
        "n_complete_hours": n_hours,
        "n_days": n_days,
        "n_sessions": len(sessions),
        "months": _join_numbers(months),
        "dates": _join_dates(items),
        "weekday_hours": weekday_hours,
        "weekend_hours": weekend_hours,
        "eligible": eligible,
        "exclusion_reason": "" if eligible else "below_threshold",
    }


def _hour_balanced(by_hour, weekday_rows, weekend_rows):
    weekday_mean = _clock_mean(weekday_rows)
    weekend_mean = _clock_mean(weekend_rows)
    if weekday_mean is not None and weekend_mean is not None:
        return (weekday_mean + weekend_mean) / 2.0
    return _clock_mean([(hour, volume) for hour, values in by_hour.items() for volume in values])


def _clock_mean(pairs):
    buckets = defaultdict(list)
    for hour, volume in pairs:
        if hour is None or volume is None:
            continue
        buckets[int(hour)].append(volume)
    if not buckets:
        return None
    means = [sum(values) / len(values) for values in buckets.values()]
    return sum(means) / len(means)


def _segment_identity(road_segment_id, monitoring_location_id):
    segment = _text(road_segment_id)
    if segment:
        return segment, False
    return _text(monitoring_location_id), True


def _segment_row(segment_id, uhf, year, items, own_segment):
    voters = [item for item in items if _is_true(item.get("eligible"))]
    means = [_finite_number(item.get("mean_hourly")) for item in voters]
    means = [value for value in means if value is not None]
    balanced = [_finite_number(item.get("hour_balanced_mean")) for item in voters]
    balanced = [value for value in balanced if value is not None]
    return {
        "road_segment_id": segment_id,
        "uhf42_id": uhf,
        "year": year,
        "mean_hourly": _mean(means),
        "hour_balanced_mean": _mean(balanced),
        "n_locations": len(items),
        "n_complete_hours": int(sum(_int_or_none(item.get("n_complete_hours")) or 0 for item in items)),
        "n_days": _distinct_days(items),
        "n_sessions": int(sum(_int_or_none(item.get("n_sessions")) or 0 for item in items)),
        "months": _union_months(items),
        "dates": _union_dates(items),
        "weekday_hours": int(sum(_int_or_none(item.get("weekday_hours")) or 0 for item in items)),
        "weekend_hours": int(sum(_int_or_none(item.get("weekend_hours")) or 0 for item in items)),
        "eligible": bool(means),
        "own_segment": bool(own_segment),
    }


def _segment_index(records):
    indexed = defaultdict(list)
    for record in records:
        uhf = _uhf(record.get("uhf42_id"))
        year = _int_or_none(record.get("year"))
        indexed[(uhf, year)].append(record)
    return indexed


def _neighborhood_row(uhf, period, years, by_key, minimum_segments, require_all):
    present = []
    missing = False
    for year in years:
        items = by_key.get((uhf, year))
        if not items:
            missing = True
            continue
        present.append(_year_exposure(items))
    pooled = len(years) > 1
    years_ok = [info["n_eligible_segments"] >= minimum_segments for info in present]
    every_year = (not missing) and len(present) == len(years) and all(years_ok)
    if not present:
        valid = False
        zeroed = True
    elif not pooled:
        valid = bool(years_ok and years_ok[0])
        zeroed = False
    elif require_all and not every_year:
        valid = False
        zeroed = missing
    else:
        valid = bool(present) and all(years_ok)
        zeroed = False
    if zeroed or not present:
        counts = _zero_counts()
        described = _zero_counts()
    else:
        counts = _min_counts(present)
        described = _sum_descriptions(present)
    exposure = _pool_exposure(present) if valid else _empty_exposure()
    coverage = _coverage_text(described["weekday_hours"], described["weekend_hours"])
    return {
        "uhf42_id": uhf,
        "period": period["period"],
        "traffic_mean_vehicles_per_hour": exposure["mean"],
        "traffic_mean_hour_balanced": exposure["balanced"],
        "traffic_median_vehicles_per_hour": exposure["median"],
        "traffic_sd_vehicles_per_hour": exposure["sd"],
        "n_road_segments": counts["n_eligible_segments"],
        "n_monitoring_locations": counts["n_locations"],
        "n_observation_days": described["n_days"],
        "n_complete_hours": counts["n_complete_hours"],
        "n_sessions": described["n_sessions"],
        "months_represented": described["months"],
        "weekday_weekend_coverage": coverage,
        "traffic_source": period["source"],
        "coverage_status": "valid" if valid else "insufficient",
    }


def _year_exposure(items):
    voters = [item for item in items if _is_true(item.get("eligible"))]
    means = [value for value in (_finite_number(item.get("mean_hourly")) for item in voters) if value is not None]
    balanced = [
        value
        for value in (_finite_number(item.get("hour_balanced_mean")) for item in voters)
        if value is not None
    ]
    return {
        "n_eligible_segments": len(voters),
        "n_locations": int(sum(_int_or_none(item.get("n_locations")) or 0 for item in items)),
        "n_complete_hours": int(sum(_int_or_none(item.get("n_complete_hours")) or 0 for item in items)),
        "n_days": _distinct_days(items) if any(_date_values(item.get("dates")) for item in items) else int(sum(_int_or_none(item.get("n_days")) or 0 for item in items)),
        "n_sessions": int(sum(_int_or_none(item.get("n_sessions")) or 0 for item in items)),
        "dates": set().union(*(_date_values(item.get("dates")) for item in items)) if items else set(),
        "weekday_hours": int(sum(_int_or_none(item.get("weekday_hours")) or 0 for item in items)),
        "weekend_hours": int(sum(_int_or_none(item.get("weekend_hours")) or 0 for item in items)),
        "months": _union_months(items),
        "means": means,
        "balanced": balanced,
        "mean": _mean(means),
        "balanced_mean": _mean(balanced),
    }


def _pool_exposure(infos):
    means = [info["mean"] for info in infos if info["mean"] is not None]
    balanced = [info["balanced_mean"] for info in infos if info["balanced_mean"] is not None]
    if len(infos) == 1:
        values = infos[0]["means"]
        balanced_values = infos[0]["balanced"]
        return {
            "mean": _mean(values),
            "balanced": _mean(balanced_values),
            "median": _median(values),
            "sd": _std(values),
        }
    return {
        "mean": _mean(means),
        "balanced": _mean(balanced),
        "median": _median(means),
        "sd": _std(means),
    }


def _empty_exposure():
    return {"mean": None, "balanced": None, "median": None, "sd": None}


def _sum_descriptions(infos):
    months = set()
    dates = set()
    for info in infos:
        months.update(_parse_months(info["months"]))
        dates.update(info.get("dates") or ())
    return {
        "n_days": len(dates) if dates else int(sum(info["n_days"] for info in infos)),
        "n_sessions": int(sum(info["n_sessions"] for info in infos)),
        "weekday_hours": int(sum(info["weekday_hours"] for info in infos)),
        "weekend_hours": int(sum(info["weekend_hours"] for info in infos)),
        "months": _join_numbers(months),
    }


def _zero_counts():
    return {
        "n_eligible_segments": 0,
        "n_locations": 0,
        "n_complete_hours": 0,
        "n_days": 0,
        "n_sessions": 0,
        "weekday_hours": 0,
        "weekend_hours": 0,
        "months": "",
    }


def _min_counts(infos):
    months = set()
    for info in infos:
        months.update(_parse_months(info["months"]))
    return {
        "n_eligible_segments": min(info["n_eligible_segments"] for info in infos),
        "n_locations": min(info["n_locations"] for info in infos),
        "n_complete_hours": min(info["n_complete_hours"] for info in infos),
        "n_days": min(info["n_days"] for info in infos),
        "n_sessions": min(info["n_sessions"] for info in infos),
        "weekday_hours": min(info["weekday_hours"] for info in infos),
        "weekend_hours": min(info["weekend_hours"] for info in infos),
        "months": _join_numbers(months),
    }


def _coverage_text(weekday_hours, weekend_hours):
    if weekday_hours and weekend_hours:
        return "both"
    if weekday_hours:
        return "weekday"
    if weekend_hours:
        return "weekend"
    return ""


def _included_periods(periods):
    if isinstance(periods, pd.DataFrame):
        records = periods.to_dict(orient="records")
    elif periods is None:
        records = []
    else:
        records = list(periods)
    included = []
    for record in records:
        if not isinstance(record, dict) or not _is_true(record.get("included")):
            continue
        included.append(
            {
                "period": _label(record.get("period")),
                "source": _text(record.get("source")),
                "years": _year_list(record.get("years")),
            }
        )
    return included


def _year_list(value):
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return []
    years = []
    for part in parts:
        number = _int_or_none(part)
        if number is not None and number not in years:
            years.append(number)
    return years


def _located(record):
    stamp = _local_stamp(record.get("ts_local"))
    if stamp is not None:
        return stamp.date().isoformat(), int(stamp.hour), int(stamp.year)
    year = _int_or_none(record.get("year"))
    month = _int_or_none(record.get("month"))
    day = _int_or_none(record.get("day"))
    hour = _int_or_none(record.get("hour"))
    if None in (year, month, day, hour):
        return None
    try:
        local = date_cls(year, month, day)
    except ValueError:
        return None
    return local.isoformat(), hour, year


def _local_stamp(value):
    if _missing(value):
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if stamp is pd.NaT:
        return None
    try:
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(
                "America/New_York",
                ambiguous="NaT",
                nonexistent="NaT",
            )
        else:
            stamp = stamp.tz_convert("America/New_York")
    except (TypeError, ValueError):
        return None
    if stamp is pd.NaT or _missing(stamp):
        return None
    return stamp


def _quarter_minute(record):
    if "minute" in record:
        kind, payload, token = _minute_piece(record.get("minute"))
        if kind == "missing":
            stamp = _local_stamp(record.get("ts_local"))
            if stamp is None:
                return None, None
            return int(stamp.minute), str(int(stamp.minute))
        if kind == "bad":
            return None, token
        return payload, token
    stamp = _local_stamp(record.get("ts_local"))
    if stamp is None:
        return None, None
    minute = int(stamp.minute)
    return minute, str(minute)


def _minute_piece(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return "bad", None, str(value)
    if _missing(value):
        return "missing", None, ""
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.casefold() in {"nan", "none", "<na>"}:
            return "missing", None, ""
        try:
            number = float(text)
        except ValueError:
            return "bad", None, text
        if not math.isfinite(number) or not number.is_integer():
            return "bad", None, text
        return "int", int(number), text
    if isinstance(value, numbers.Integral):
        return "int", int(value), str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            return "bad", None, _number_token(number)
        return "int", int(number), str(int(number))
    text = str(value).strip()
    return "bad", None, text


def _direction(record):
    if "direction_normalized" in record:
        return _text(record.get("direction_normalized"))
    return _text(record.get("direction"))


def _request(record):
    request = _text(record.get("request")) if "request" in record else ""
    if request:
        return request
    if "requestid" in record:
        return _text(record.get("requestid"))
    return ""


def _session(location, date, direction, request):
    if request:
        return request
    return f"{location}|{date}|{direction}"


def _hour_complete(record):
    if "hour_complete" not in record:
        return True
    return _is_true(record.get("hour_complete"))


def _volume(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        raise AggregateError(str(value), 2)
    if _missing(value):
        return None
    if isinstance(value, numbers.Real):
        number = float(value)
    else:
        text = str(value).strip()
        if text == "" or text.casefold() in {"nan", "none", "<na>"}:
            return None
        try:
            number = float(text)
        except ValueError:
            raise AggregateError(text, 2) from None
    if not math.isfinite(number):
        raise AggregateError(_number_token(value), 2)
    return number


def _first_text(items, column):
    for item in items:
        text = _text(item.get(column))
        if text:
            return text
    return ""


def _first_uhf(items):
    for item in items:
        uhf = _uhf(item.get("uhf42_id"))
        if uhf:
            return uhf
    return ""


def _union_months(items):
    months = set()
    for item in items:
        months.update(_parse_months(item.get("months")))
    return _join_numbers(months)


def _join_dates(items):
    dates = set()
    for item in items:
        parsed = _parse_date(item.get("date"))
        if parsed is not None:
            dates.add(parsed.isoformat())
    return ",".join(sorted(dates))


def _date_values(value):
    dates = set()
    for part in _text(value).split(","):
        parsed = _parse_date(part)
        if parsed is not None:
            dates.add(parsed.isoformat())
    return dates


def _union_dates(items):
    dates = set()
    for item in items:
        dates.update(_date_values(item.get("dates")))
    return ",".join(sorted(dates))


def _distinct_days(items):
    dates = set()
    for item in items:
        dates.update(_date_values(item.get("dates")))
    if dates:
        return len(dates)
    return int(sum(_int_or_none(item.get("n_days")) or 0 for item in items))


def _parse_months(value):
    months = set()
    for part in _text(value).split(","):
        number = _int_or_none(part)
        if number is not None:
            months.add(number)
    return months


def _join_numbers(values):
    return ",".join(str(value) for value in sorted(values))


def _parse_date(value):
    if isinstance(value, date_cls):
        return value
    text = _text(value)
    if len(text) < 10:
        return None
    try:
        return date_cls.fromisoformat(text[:10])
    except ValueError:
        return None


def _mean(values):
    if not values:
        return None
    return sum(values) / len(values)


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _std(values):
    count = len(values)
    if count < 2:
        return None
    center = sum(values) / count
    variance = sum((value - center) ** 2 for value in values) / (count - 1)
    return math.sqrt(variance)


def _finite_number(value):
    number = None if _missing(value) else value
    if number is None or isinstance(number, bool):
        return None
    try:
        number = float(number)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _hours_frame(rows):
    return _typed_frame(rows, _HOUR_COLUMNS, _HOUR_DTYPES)


def _project(rows, columns, dtypes, fields):
    frame = _typed_frame(rows, columns, dtypes)
    frame.attrs["aggregate_fields"] = {
        field: [row.get(field) for row in rows] for field in fields
    }
    return frame


def _typed_frame(rows, columns, dtypes):
    frame = pd.DataFrame(list(rows), columns=list(columns))
    for column, kind in dtypes.items():
        if kind == "int":
            frame[column] = pd.array(
                [_int_or_none(value) for value in frame[column].tolist()],
                dtype="Int64",
            )
        elif kind == "float":
            frame[column] = pd.Series(
                [_float_or_none(value) for value in frame[column].tolist()],
                index=frame.index,
                dtype="float64",
            )
        elif kind == "bool":
            frame[column] = pd.Series(
                [_is_true(value) for value in frame[column].tolist()] if len(frame.index) else [],
                index=frame.index,
                dtype=bool,
            )
        else:
            frame[column] = pd.Series(
                [_text(value) for value in frame[column].tolist()],
                index=frame.index,
                dtype="object",
            )
    return frame[list(columns)]


_HOUR_DTYPES = {
    "monitoring_location_id": "str",
    "road_segment_id": "str",
    "uhf42_id": "str",
    "source": "str",
    "year": "int",
    "date": "str",
    "hour": "int",
    "direction_normalized": "str",
    "session": "str",
    "vol": "float",
}
_LOCATION_DTYPES = {
    "monitoring_location_id": "str",
    "road_segment_id": "str",
    "uhf42_id": "str",
    "year": "int",
    "mean_hourly": "float",
    "hour_balanced_mean": "float",
    "n_complete_hours": "int",
    "n_days": "int",
    "n_sessions": "int",
    "months": "str",
    "weekday_hours": "int",
    "weekend_hours": "int",
    "eligible": "bool",
    "exclusion_reason": "str",
}
_SEGMENT_DTYPES = {
    "road_segment_id": "str",
    "uhf42_id": "str",
    "year": "int",
    "mean_hourly": "float",
    "hour_balanced_mean": "float",
    "n_locations": "int",
    "n_complete_hours": "int",
    "n_days": "int",
    "eligible": "bool",
    "own_segment": "bool",
}
_NEIGHBORHOOD_DTYPES = {
    "uhf42_id": "str",
    "period": "str",
    "traffic_mean_vehicles_per_hour": "float",
    "traffic_mean_hour_balanced": "float",
    "traffic_median_vehicles_per_hour": "float",
    "traffic_sd_vehicles_per_hour": "float",
    "n_road_segments": "int",
    "n_monitoring_locations": "int",
    "n_observation_days": "int",
    "n_complete_hours": "int",
    "n_sessions": "int",
    "months_represented": "str",
    "weekday_weekend_coverage": "str",
    "traffic_source": "str",
    "coverage_status": "str",
}


def _label(value):
    if _missing(value):
        return ""
    return str(value).strip()


def _uhf(value):
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
        return _number_token(number)
    text = str(value).strip()
    if text.casefold() in {"nan", "none", "<na>"}:
        return ""
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


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


def _int_or_none(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return None
    if _missing(value):
        return None
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        if math.isfinite(number) and number.is_integer():
            return int(number)
        return None
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "none", "<na>"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number) or not number.is_integer():
        return None
    return int(number)


def _float_or_none(value):
    return _finite_number(value)


def _number_token(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    if number.is_integer():
        return str(int(number))
    return format(number, "f").rstrip("0").rstrip(".")


def _is_true(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return bool(value)
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes"}
    return False


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
    if missing is True or missing is pd.NA:
        return True
    try:
        return bool(missing)
    except ValueError:
        return False


def _year_sort(value):
    number = _int_or_none(value)
    if number is None:
        return (1, 0)
    return (0, number)


def _uhf_sort(value):
    text = "" if value is None else str(value)
    try:
        return (0, int(text), text)
    except ValueError:
        return (1, 0, text)
