"""Classify asthma export TimePeriod labels and choose a traffic source."""

import math
import numbers
import re
import warnings
from collections import defaultdict
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


_COVERAGE_COLUMNS = [
    "period",
    "uhf42_id",
    "n_eligible_segments",
    "n_locations",
    "n_complete_hours",
    "adequate",
    "source",
]
_MEANS_COLUMNS = ["source", "period", "year", "uhf42_id", "mean_hourly"]
_QUARTERS = {0, 15, 30, 45}
_ID_COLUMNS = ("UHFCODE", "uhfcode", "UHF_CODE", "GeoID", "geoid")
_LON_MIN = -74.3
_LON_MAX = -73.6
_LAT_MIN = 40.4
_LAT_MAX = 40.95


def score_coverage(config, source, periods, year_frames, shapefile_path):
    """Score one source against asthma periods. Does not write or download."""
    frames = {
        int(year): _as_frame(frame).copy()
        for year, frame in dict(year_frames).items()
    }
    period_items = list(_iter_periods(periods))
    if source == "aadt":
        frames, blocked = _prepare_aadt(config, frames)
        if blocked:
            coverage = _coverage_frame([])
            coverage.attrs["reason"] = blocked
            return coverage, _means_frame([])
    modes = {}
    for year in _ordered_years(period_items, frames):
        frame = frames.get(year)
        if frame is None:
            continue
        modes[year] = _hour_mode(config, source, frame, year)
    polygons = _read_polygons(shapefile_path)
    year_stats = {}
    for year, mode in modes.items():
        year_stats[year] = _score_year(
            config, source, frames[year], year, mode, polygons
        )
    coverage_rows, means_rows = _period_tables(
        config, source, period_items, year_stats
    )
    return _coverage_frame(coverage_rows), _means_frame(means_rows)


def select_source(config, periods, coverage_by_source, means_by_source):
    """Pick a source per period. Asthma rates are not an input."""
    coverage_by_source = coverage_by_source or {}
    means_by_source = means_by_source or {}
    period_items = list(_iter_periods(periods))
    minimum = int(config["thresholds"]["min_uhf_per_period"])
    names = [item["period"] for item in period_items]
    auto_cover = {
        period: _source_covers(coverage_by_source, "automated", period, minimum)
        for period in names
    }
    hist_cover = {
        period: _source_covers(coverage_by_source, "historical", period, minimum)
        for period in names
    }
    any_auto = any(auto_cover.values())
    all_auto = bool(names) and all(auto_cover.values())
    any_hist = any(hist_cover.values())
    comparability_pass = None
    comparability_r = None
    comparability_n = None
    choices = {}
    if all_auto:
        scenario = "A"
        for period in names:
            choices[period] = ("automated", True, "")
    elif any_auto:
        scenario = "2"
        gaps = [period for period in names if not auto_cover[period]]
        hist_gaps = [period for period in gaps if hist_cover[period]]
        can_fill = False
        if hist_gaps:
            comparability_pass, comparability_r, comparability_n = _overlap_check(
                config, means_by_source
            )
            can_fill = comparability_pass is True
        for period in names:
            if auto_cover[period]:
                choices[period] = ("automated", True, "")
            elif can_fill and hist_cover[period]:
                choices[period] = ("historical", True, "")
            elif hist_gaps and not can_fill:
                choices[period] = ("", False, "comparability_failed")
            else:
                choices[period] = ("", False, "below_min_uhf")
    elif any_hist:
        scenario = "3"
        for period in names:
            if hist_cover[period]:
                choices[period] = ("historical", True, "")
            else:
                choices[period] = ("", False, "below_min_uhf")
    else:
        scenario = "4"
        blocked = _aadt_reason(coverage_by_source.get("aadt"))
        for period in names:
            if _source_covers(coverage_by_source, "aadt", period, minimum):
                choices[period] = ("aadt", True, "")
            elif blocked:
                choices[period] = ("", False, blocked)
            else:
                choices[period] = ("", False, "below_min_uhf")
    selected = []
    for item in period_items:
        source_name, included, reason = choices[item["period"]]
        selected.append(
            {
                "period": item["period"],
                "source": source_name if included else "",
                "years": list(item["years"]),
                "included": bool(included),
                "reason": "" if included else reason,
            }
        )
    if not any(item["included"] for item in selected):
        scenario = "D"
    included_sources = {item["source"] for item in selected if item["included"]}
    if "automated" in included_sources or "historical" in included_sources:
        for item in selected:
            if item["included"] and item["source"] == "aadt":
                item["source"] = ""
                item["included"] = False
                item["reason"] = item["reason"] or "aadt_does_not_mix"
        included_sources = {item["source"] for item in selected if item["included"]}
        if not included_sources:
            scenario = "D"
    mix_used = "automated" in included_sources and "historical" in included_sources
    return {
        "scenario": scenario,
        "mix_used": bool(mix_used),
        "comparability_pass": None
        if comparability_pass is None
        else bool(comparability_pass),
        "comparability_r": _json_float(comparability_r),
        "comparability_n": None
        if comparability_n is None
        else int(comparability_n),
        "periods": selected,
    }


def _iter_periods(periods):
    frame = periods if isinstance(periods, pd.DataFrame) else pd.DataFrame(list(periods))
    for record in frame.to_dict(orient="records"):
        raw_years = record.get("constituent_years")
        text = "" if raw_years is None else str(raw_years)
        years = []
        for part in text.split(","):
            part = part.strip()
            if not part or part.lower() == "nan":
                continue
            years.append(int(float(part)))
        period = record.get("period")
        label = "" if period is None else str(period).strip()
        yield {"period": label, "years": years}


def _ordered_years(period_items, frames):
    ordered = []
    seen = set()
    for item in period_items:
        for year in item["years"]:
            if year not in seen:
                seen.add(year)
                ordered.append(year)
    for year in sorted(frames):
        if year not in seen:
            ordered.append(year)
    return ordered


def _as_frame(value):
    if isinstance(value, pd.DataFrame):
        return value
    if value is None:
        return pd.DataFrame()
    return pd.DataFrame(list(value))


def _coverage_frame(rows):
    frame = pd.DataFrame(rows, columns=_COVERAGE_COLUMNS)
    frame["n_eligible_segments"] = frame["n_eligible_segments"].astype(int)
    frame["n_locations"] = frame["n_locations"].astype(int)
    frame["n_complete_hours"] = frame["n_complete_hours"].astype(int)
    frame["adequate"] = frame["adequate"].astype(bool)
    return frame


def _means_frame(rows):
    frame = pd.DataFrame(rows, columns=_MEANS_COLUMNS)
    if len(frame.index):
        frame["year"] = frame["year"].astype(int)
        frame["mean_hourly"] = frame["mean_hourly"].astype(float)
    return frame


def _prepare_aadt(config, frames):
    aliases = config["fields"]["aadt"]
    has_wkt = any(
        _resolve(frame.columns, aliases["wkt"]) is not None for frame in frames.values()
    )
    if has_wkt:
        return frames, None
    mapping = _xy_mapping(config)
    if not mapping:
        return frames, "aadt_no_point"
    joined = False
    updated = {}
    for year, frame in frames.items():
        frame = frame.copy()
        segment_col = _resolve(frame.columns, aliases["segment"])
        wkts = []
        if segment_col is None:
            frame["wkt"] = pd.Series([None] * len(frame.index), index=frame.index)
        else:
            for value in frame[segment_col]:
                wkts.append(mapping.get(_segment_token(value)))
            frame["wkt"] = wkts
            if any(item for item in wkts):
                joined = True
        updated[year] = frame
    if not joined:
        return frames, "aadt_no_point"
    return updated, None


def _xy_mapping(config):
    raw_path = str(config["sources"].get("aadt_xy_csv") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file():
        return None
    try:
        table = pd.read_csv(path, dtype=str)
    except Exception:
        return None
    headers = [str(column) for column in table.columns]
    segment_col = _resolve(headers, config["fields"]["aadt"]["segment"])
    if segment_col is None:
        return None
    wkt_col = _header_name(headers, "wkt")
    lon_col = _header_name(headers, "longitude")
    lat_col = _header_name(headers, "latitude")
    if wkt_col is None and not (lon_col and lat_col):
        return None
    mapping = {}
    for record in table.to_dict(orient="records"):
        token = _segment_token(record.get(segment_col))
        if token is None or token in mapping:
            continue
        if wkt_col is not None:
            text = record.get(wkt_col)
            wkt = "" if text is None else str(text).strip()
            if not wkt or wkt.lower() == "nan":
                wkt = ""
        else:
            wkt = ""
        if not wkt and lon_col and lat_col:
            lon = record.get(lon_col)
            lat = record.get(lat_col)
            if lon not in (None, "") and lat not in (None, "") and str(lon).lower() != "nan":
                wkt = f"POINT({str(lon).strip()} {str(lat).strip()})"
        if wkt:
            mapping[token] = wkt
    return mapping or None


def _header_name(headers, name):
    if name in headers:
        return name
    for header in headers:
        if header.lower() == name:
            return header
    return None


def _hour_mode(config, source, frame, year):
    if source == "aadt":
        return "aadt"
    minute_col = _resolve(frame.columns, config["fields"][source]["minute"])
    if source == "historical":
        if minute_col is None:
            return "hour"
        values = [_parse_minute(value) for value in frame[minute_col]]
        if all(value is None or value == 0 for value in values):
            return "hour"
        if any(value not in (None, 0, 15, 30, 45) for value in values):
            raise AuditError(
                f"hourly_definition_unresolved {source} {int(year)}",
                2,
            )
        if any(value not in (None, 0) for value in values):
            return "quarter"
        raise AuditError(f"hourly_definition_unresolved {source} {int(year)}", 2)
    if minute_col is not None:
        for value in frame[minute_col]:
            parsed = _parse_minute(value)
            if parsed is not None and parsed not in _QUARTERS:
                raise AuditError(
                    f"hourly_definition_unresolved {source} {int(year)}",
                    2,
                )
    return "quarter"


def _parse_minute(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            return "bad"
        return int(number)
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    try:
        number = float(text)
    except ValueError:
        return "bad"
    if not math.isfinite(number) or not number.is_integer():
        return "bad"
    return int(number)


def _read_polygons(shapefile_path):
    import geopandas as gpd

    polygons = gpd.read_file(shapefile_path)
    id_name = _resolve(polygons.columns, _ID_COLUMNS)
    if id_name is not None:
        polygons = polygons.copy()
        polygons["uhf42_id"] = polygons[id_name].map(_normalize_uhf_id)
        keep = polygons["uhf42_id"].map(_is_neighborhood_id) & polygons.geometry.apply(
            _geometry_ok
        )
        polygons = polygons.loc[keep]
    if polygons.crs is None:
        polygons = polygons.set_crs("EPSG:4326")
    polygons = polygons.to_crs("EPSG:2263")
    if id_name is None:
        polygons = polygons.assign(uhf42_id=pd.Series(dtype="object"))
    return polygons[["uhf42_id", "geometry"]]


def _is_neighborhood_id(value):
    """Normalized ids other than the code-0 outline can be coverage neighborhoods."""
    return isinstance(value, str) and value != "0"


def _geometry_ok(geom):
    if geom is None:
        return False
    try:
        return (not geom.is_empty) and bool(geom.is_valid)
    except Exception:
        return False


def _score_year(config, source, frame, year, mode, polygons):
    columns = _resolved_columns(config, source, frame.columns)
    rows = _normalized_rows(frame, columns, year, mode)
    assigned = _assign_locations(rows, polygons)
    located = [row for row in rows if row["key"] in assigned]
    min_dates = int(config["thresholds"]["min_observation_dates"])
    min_hours = int(config["thresholds"]["min_complete_hours"])
    combined = [] if mode == "aadt" else _combined_hours(located, mode)
    hours = defaultdict(int)
    days = defaultdict(lambda: defaultdict(list))
    for item in combined:
        key = item["key"]
        hours[key] += 1
        days[key][item["date"]].append(item["volume"])
    aadt_values = defaultdict(list)
    if mode == "aadt":
        for row in located:
            if row["value"] is not None:
                aadt_values[row["key"]].append(row["value"])
    segment_values = defaultdict(list)
    location_counts = defaultdict(int)
    hour_counts = defaultdict(int)
    for key, uhf in assigned.items():
        location_counts[uhf] += 1
        if mode == "aadt":
            values = aadt_values.get(key) or []
            loc_mean = sum(values) / len(values) if values else None
            eligible = loc_mean is not None
        else:
            day_map = days.get(key) or {}
            n_hours = hours.get(key, 0)
            hour_counts[uhf] += n_hours
            if day_map:
                daily = [sum(bucket) / len(bucket) for bucket in day_map.values()]
                loc_mean = sum(daily) / len(daily)
            else:
                loc_mean = None
            eligible = (
                loc_mean is not None
                and len(day_map) >= min_dates
                and n_hours >= min_hours
            )
        if eligible:
            segment_values[(uhf, _vote_segment(key))].append(loc_mean)
    segment_means = defaultdict(list)
    for (uhf, _segment), values in segment_values.items():
        segment_means[uhf].append(sum(values) / len(values))
    stats = {}
    for uhf, n_locations in location_counts.items():
        means = segment_means.get(uhf, [])
        stats[uhf] = {
            "n_eligible_segments": len(means),
            "n_locations": int(n_locations),
            "n_complete_hours": 0 if mode == "aadt" else int(hour_counts.get(uhf, 0)),
            "mean_hourly": (sum(means) / len(means)) if means else None,
        }
    return stats


def _resolved_columns(config, source, columns):
    fields = config["fields"][source]
    resolved = {}
    for name in (
        "year",
        "month",
        "day",
        "hour",
        "minute",
        "volume",
        "segment",
        "wkt",
        "direction",
        "borough",
        "request",
        "aadt",
    ):
        resolved[name] = _resolve(columns, fields.get(name) or [])
    return resolved


def _normalized_rows(frame, columns, year, mode):
    if len(frame.index) == 0:
        return []
    names = list(frame.columns)
    index = {name: position for position, name in enumerate(names)}
    rows = []
    for record in frame.itertuples(index=False, name=None):
        wkt = _cell(record, index, columns["wkt"])
        wkt_text = _plain_text(wkt)
        if not wkt_text:
            continue
        segment = _segment_token(_cell(record, index, columns["segment"]))
        direction = _plain_text(_cell(record, index, columns["direction"])) or ""
        request = _plain_text(_cell(record, index, columns["request"]))
        row_year = _parse_int(_cell(record, index, columns["year"]))
        if row_year is None:
            row_year = int(year)
        month = _parse_int(_cell(record, index, columns["month"]))
        day = _parse_int(_cell(record, index, columns["day"]))
        hour = _parse_int(_cell(record, index, columns["hour"]))
        date = None if None in (month, day, hour) else (row_year, month, day)
        if mode == "aadt":
            raw_value = _parse_float(_cell(record, index, columns["aadt"]))
            value = None if raw_value is None else raw_value / 24.0
            volume = value
            minute = None
        else:
            value = None
            volume = _parse_float(_cell(record, index, columns["volume"]))
            minute = _parse_minute(_cell(record, index, columns["minute"]))
            if minute == "bad":
                minute = None
        rows.append(
            {
                "key": (segment, wkt_text),
                "date": date,
                "hour": hour,
                "minute": minute,
                "direction": direction,
                "request": request,
                "volume": volume,
                "value": value,
                "wkt": wkt_text,
            }
        )
    return rows


def _cell(record, index, column):
    if column is None or column not in index:
        return None
    return record[index[column]]


def _assign_locations(rows, polygons):
    unique = {}
    for row in rows:
        unique.setdefault(row["key"], row["wkt"])
    if not unique or polygons is None or len(polygons.index) == 0:
        return {}
    keys = []
    geometries = []
    geographic = []
    for key, text in unique.items():
        geom = _parse_point(text)
        if geom is None:
            continue
        keys.append(key)
        geometries.append(geom)
        geographic.append(_is_geographic(geom))
    if not keys:
        return {}
    import geopandas as gpd

    assigned = {}
    groups = (
        ([index for index, flag in enumerate(geographic) if flag], 4326),
        ([index for index, flag in enumerate(geographic) if not flag], 2263),
    )
    for indexes, epsg in groups:
        if not indexes:
            continue
        points = gpd.GeoDataFrame(
            {"loc_index": indexes},
            geometry=[geometries[index] for index in indexes],
            crs=f"EPSG:{epsg}",
        ).to_crs("EPSG:2263")
        joined = gpd.sjoin(points, polygons, predicate="within", how="inner")
        if len(joined.index) == 0:
            continue
        counts = joined.groupby("loc_index").size()
        singles = set(counts[counts == 1].index)
        joined = joined[joined["loc_index"].isin(singles)]
        for loc_index, uhf in zip(joined["loc_index"], joined["uhf42_id"]):
            if uhf is None or (isinstance(uhf, float) and math.isnan(uhf)):
                continue
            assigned[keys[int(loc_index)]] = str(uhf)
    return assigned


def _parse_point(text):
    from shapely import wkt as shapely_wkt

    raw = "" if text is None else str(text).strip()
    if not raw or raw.lower() == "nan":
        return None
    if raw.upper().startswith("SRID="):
        parts = raw.split(";", 1)
        raw = parts[1] if len(parts) == 2 else raw
    try:
        geom = shapely_wkt.loads(raw)
    except Exception:
        return None
    if geom is None or geom.is_empty or geom.geom_type != "Point":
        return None
    return geom


def _is_geographic(geom):
    return _LON_MIN <= float(geom.x) <= _LON_MAX and _LAT_MIN <= float(geom.y) <= _LAT_MAX


def _combined_hours(rows, mode):
    observed = defaultdict(set)
    for row in rows:
        if row["direction"]:
            observed[row["key"]].add(row["direction"])
    directional = _directional_hours(rows, mode)
    groups = defaultdict(dict)
    for item in directional:
        if item["session"][0] == "req":
            group_id = (item["key"], item["date"], item["hour"], item["session"])
        else:
            group_id = (item["key"], item["date"], item["hour"], None)
        groups[group_id][item["direction"]] = item["volume"]
    combined = []
    for group_id, dir_map in groups.items():
        key = group_id[0]
        date = group_id[1]
        directions = observed.get(key, set())
        if len(directions) <= 1:
            only = next(iter(directions)) if directions else ""
            if only in dir_map:
                combined.append({"key": key, "date": date, "volume": dir_map[only]})
        elif directions <= set(dir_map):
            combined.append(
                {
                    "key": key,
                    "date": date,
                    "volume": sum(dir_map[name] for name in directions),
                }
            )
    return combined


def _directional_hours(rows, mode):
    if mode == "quarter":
        per_minute = {}
        for row in rows:
            minute = row["minute"]
            if (
                row["volume"] is None
                or row["date"] is None
                or row["hour"] is None
                or minute not in _QUARTERS
            ):
                continue
            session = _session(row)
            token = (
                row["key"],
                row["date"],
                row["hour"],
                minute,
                row["direction"],
                session,
            )
            per_minute[token] = per_minute.get(token, 0.0) + float(row["volume"])
        grouped = {}
        for (key, date, hour, minute, direction, session), volume in per_minute.items():
            bucket = (key, date, hour, direction, session)
            grouped.setdefault(bucket, {})[minute] = volume
        hours = []
        for (key, date, hour, direction, session), minutes in grouped.items():
            if _QUARTERS <= set(minutes):
                hours.append(
                    {
                        "key": key,
                        "date": date,
                        "hour": hour,
                        "direction": direction,
                        "session": session,
                        "volume": sum(minutes[minute] for minute in _QUARTERS),
                    }
                )
        return hours
    grouped = {}
    for row in rows:
        if row["volume"] is None or row["date"] is None or row["hour"] is None:
            continue
        session = _session(row)
        token = (row["key"], row["date"], row["hour"], row["direction"], session)
        grouped[token] = grouped.get(token, 0.0) + float(row["volume"])
    return [
        {
            "key": key,
            "date": date,
            "hour": hour,
            "direction": direction,
            "session": session,
            "volume": volume,
        }
        for (key, date, hour, direction, session), volume in grouped.items()
    ]


def _session(row):
    if row["request"]:
        return ("req", row["request"])
    return ("fallback", row["key"], row["date"], row["direction"])


def _vote_segment(key):
    segment, wkt = key
    if segment:
        return segment
    return f"__missing_segment__:{wkt}"


def _period_tables(config, source, period_items, year_stats):
    minimum = int(config["thresholds"]["min_segments_per_uhf"])
    require_all = bool(config["pool"]["require_all_years"])
    coverage_rows = []
    means_rows = []
    for item in period_items:
        years = item["years"]
        for year in years:
            for uhf, info in year_stats.get(year, {}).items():
                if info["mean_hourly"] is None:
                    continue
                means_rows.append(
                    {
                        "source": source,
                        "period": item["period"],
                        "year": int(year),
                        "uhf42_id": uhf,
                        "mean_hourly": float(info["mean_hourly"]),
                    }
                )
        uhfs = set()
        for year in years:
            uhfs.update(year_stats.get(year, {}))
        pooled = len(years) > 1
        for uhf in sorted(uhfs, key=_uhf_sort_key):
            present = []
            missing = False
            for year in years:
                info = year_stats.get(year, {}).get(uhf)
                if info is None:
                    missing = True
                else:
                    present.append(info)
            if not present:
                continue
            if pooled and require_all and missing:
                n_eligible = 0
                n_locations = 0
                n_hours = 0
            else:
                n_eligible = min(info["n_eligible_segments"] for info in present)
                n_locations = min(info["n_locations"] for info in present)
                n_hours = min(info["n_complete_hours"] for info in present)
            coverage_rows.append(
                {
                    "period": item["period"],
                    "uhf42_id": uhf,
                    "n_eligible_segments": int(n_eligible),
                    "n_locations": int(n_locations),
                    "n_complete_hours": int(n_hours),
                    "adequate": int(n_eligible) >= minimum,
                    "source": source,
                }
            )
    return coverage_rows, means_rows


def _uhf_sort_key(value):
    text = str(value)
    try:
        return (0, int(text), text)
    except ValueError:
        return (1, 0, text)


def _source_covers(coverage_by_source, source, period, minimum):
    frame = coverage_by_source.get(source)
    if frame is None or len(getattr(frame, "index", [])) == 0:
        return False
    count = 0
    for label, flag in zip(frame["period"], frame["adequate"]):
        if str(label).strip() != period:
            continue
        if flag is True or str(flag).strip().lower() == "true":
            count += 1
    return count >= minimum


def _aadt_reason(frame):
    if frame is None:
        return None
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        return None
    reason = attrs.get("reason")
    if reason:
        return str(reason)
    return None


def _overlap_check(config, means_by_source):
    auto = means_by_source.get("automated")
    hist = means_by_source.get("historical")
    years = _mean_years(auto) | _mean_years(hist)
    candidates = []
    for year in sorted(years):
        auto_map = _mean_map(auto, year)
        hist_map = _mean_map(hist, year)
        if auto_map and hist_map:
            candidates.append((year, auto_map, hist_map))
    if not candidates:
        return False, None, None
    _year, auto_map, hist_map = candidates[-1]
    shared = sorted(set(auto_map) & set(hist_map))
    n = len(shared)
    r_value = _pearson(
        [auto_map[uhf] for uhf in shared],
        [hist_map[uhf] for uhf in shared],
    ) if n >= 2 else None
    minimum = int(config["thresholds"]["min_uhf_per_period"])
    mix_r = float(config["sources"]["mix_min_traffic_r"])
    passed = n >= 3 and n >= minimum and r_value is not None and r_value >= mix_r
    return passed, r_value, n


def _mean_years(frame):
    if frame is None or len(frame.index) == 0 or "year" not in frame.columns:
        return set()
    years = set()
    for value in frame["year"]:
        try:
            years.add(int(value))
        except (TypeError, ValueError):
            continue
    return years


def _mean_map(frame, year):
    if frame is None or len(frame.index) == 0:
        return {}
    grouped = defaultdict(list)
    for row_year, uhf, value in zip(frame["year"], frame["uhf42_id"], frame["mean_hourly"]):
        try:
            if int(row_year) != int(year):
                continue
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        grouped[str(uhf)].append(number)
    return {uhf: sum(values) / len(values) for uhf, values in grouped.items()}


def _pearson(xs, ys):
    from scipy.stats import pearsonr

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            statistic = float(pearsonr(xs, ys).statistic)
    except Exception:
        return None
    if not math.isfinite(statistic):
        return None
    return statistic


def _json_float(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _resolve(columns, aliases):
    present = {str(column) for column in columns}
    for alias in aliases:
        if alias in present:
            return alias
    return None


def _segment_token(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer():
            return str(int(value))
        return str(value).strip()
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    return text


def _plain_text(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    return text


def _parse_float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def _parse_int(value):
    number = _parse_float(value)
    if number is None or not float(number).is_integer():
        return None
    return int(number)


def _normalize_uhf_id(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            return None
        if number.is_integer():
            return str(int(number))
        text = format(number, "f").rstrip("0").rstrip(".")
        return text
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "<na>"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return str(int(number))
    return format(number, "f").rstrip("0").rstrip(".")
