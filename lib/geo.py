"""Local UHF42 boundaries and monitoring-location assignment."""

import hashlib
import json
import math
import numbers
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
from shapely import union_all
from shapely import wkt as shapely_wkt

from lib.socrata import SocrataError

_ROOT = Path("data/raw/geography")
_SHAPEFILE = Path("UHF_42_DOHMH_2009") / "UHF_42_DOHMH_2009_WGS84.shp"
_SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
_ID_COLUMNS = ("UHFCODE", "uhfcode", "UHF_CODE", "GeoID", "geoid")
_INVALID = "the shapefile does not have 42 valid unique ids"


class BoundaryError(Exception):
    def __init__(self, message, exit_code=2):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def ensure_boundaries(config):
    """Download pinned UHF42 files when SOURCE.txt is absent, then validate.

    SOURCE.txt is written only after the shapefile has 42 neighborhood polygons.
    A SOURCE.txt that was already present is left in place.
    """
    source_txt = _ROOT / "SOURCE.txt"
    shapefile = _ROOT / _SHAPEFILE
    downloaded_sha = None
    if not source_txt.is_file():
        downloaded_sha = _download_boundaries(config)
    _validate_shapefile(shapefile)
    if downloaded_sha is not None:
        source_txt.parent.mkdir(parents=True, exist_ok=True)
        source_txt.write_text(downloaded_sha + "\n", encoding="utf-8")
    return shapefile


def _download_boundaries(config):
    ref = str(config["geography"]["git_ref"])
    commits_url = (
        "https://api.github.com/repos/nycehs/NYC_geography/commits/"
        + quote(ref, safe="")
    )
    body = _get_bytes(commits_url, config)
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        raise SocrataError(commits_url, 1) from None
    sha = payload.get("sha") if isinstance(payload, dict) else None
    if not isinstance(sha, str) or not sha.strip():
        raise SocrataError(commits_url, 1)
    sha = sha.strip()
    root = (
        "https://raw.githubusercontent.com/nycehs/NYC_geography/"
        + quote(sha, safe="")
    )
    relative_paths = [
        "UHF_42_DOHMH_2009/UHF_42_DOHMH_2009_WGS84" + suffix for suffix in _SIDECARS
    ]
    relative_paths.append("UHF42.geo.json")
    for relative in relative_paths:
        url = root + "/" + quote(relative, safe="/")
        target = _ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_get_bytes(url, config))
    return sha


def _get_bytes(url, config):
    headers = {"User-Agent": config["api"]["user_agent"]}
    timeout = config["api"]["timeout_seconds"]
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException:
        raise SocrataError(url, 1) from None
    if response.status_code != 200:
        raise SocrataError(url, 1)
    body = response.content
    if not isinstance(body, (bytes, bytearray)):
        raise SocrataError(url, 1)
    return bytes(body)


def _validate_shapefile(shapefile):
    import geopandas as gpd

    try:
        frame = gpd.read_file(shapefile)
    except Exception:
        raise BoundaryError(_INVALID, 2) from None
    id_name = _first_present(frame.columns, _ID_COLUMNS)
    if id_name is None:
        raise BoundaryError(_INVALID, 2)
    neighborhood_ids = []
    for value, geom in zip(frame[id_name].tolist(), list(frame.geometry)):
        uhf_id = _normalize_uhf_id(value)
        # The pinned shapefile includes a code-0 outline that is not a neighborhood.
        if uhf_id == "0":
            continue
        if uhf_id is None or not _geometry_ok(geom):
            raise BoundaryError(_INVALID, 2)
        neighborhood_ids.append(uhf_id)
    if len(neighborhood_ids) != 42 or len(set(neighborhood_ids)) != 42:
        raise BoundaryError(_INVALID, 2)


def _geometry_ok(geom):
    if geom is None:
        return False
    try:
        return (not geom.is_empty) and bool(geom.is_valid)
    except Exception:
        return False


def _first_present(columns, names):
    present = {str(column) for column in columns}
    for name in names:
        if name in present:
            return name
    return None


def _normalize_uhf_id(value):
    """Decimal string of the numeric id with leading zeros removed."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if not _finite(number):
            return None
        if number.is_integer():
            return str(int(number))
        return _decimal(number)
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "<na>"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not _finite(number):
        return None
    if number.is_integer():
        return str(int(number))
    return _decimal(number)


def _finite(number):
    return number == number and number not in (float("inf"), float("-inf"))


def _decimal(number):
    return format(number, "f").rstrip("0").rstrip(".")


METERS_PER_US_SURVEY_FOOT = 1200 / 3937
_BOROUGH_COLUMNS = ("BOROUGH", "borough", "BORO", "boro")
_LION_COLUMNS = ("segment_id", "SegmentID", "segmentid", "SEGMENTID")
_EXCLUSION_ASSIGNMENTS = ("outside_nyc", "boundary", "borough_mismatch")
_LOCATION_COLUMNS = (
    "monitoring_location_id",
    "road_segment_id",
    "uhf42_id",
    "assignment",
    "source_crs",
)
_AUDIT_COLUMNS = (
    "monitoring_location_id",
    "assignment",
    "uhf42_id",
    "borough_traffic",
    "borough_polygon",
    "rule",
)
_EXCLUSION_COLUMNS = ("stage", "key", "reason", "source")


def rounded_axis(feet, step):
    meters = feet * METERS_PER_US_SURVEY_FOOT
    snapped = round(meters / step) * step
    return int(round(snapped))


def load_config():
    """Return the pipeline config from lib.config at call time."""
    from lib.config import load_config as read_config

    return read_config()


def assign_points(traffic, boundaries, asthma_ids):
    """Return (locations, traffic_with_uhf, audit, exclusions).

    Loads config with load_config(). Does not write files and does not use the network.
    """
    config = load_config()
    frame = traffic.copy() if isinstance(traffic, pd.DataFrame) else pd.DataFrame(traffic)
    records = _location_records(frame, config)
    prepared = _prepare_boundaries(boundaries, asthma_ids, config)
    locations = _classify_locations(records, prepared, config)
    traffic_rows = _traffic_rows(frame, records)
    traffic_with_uhf, audit, exclusions = _package_outputs(locations, traffic_rows)
    locations.attrs["traffic_rows"] = traffic_rows
    locations.attrs["prepared_boundaries"] = prepared
    return locations, traffic_with_uhf, audit, exclusions


def resolve_non_interior(locations, boundaries, lion):
    """Return locations. Does not write files and does not use the network."""
    locations = locations.copy()
    if "uhf42_id" in locations.columns:
        locations["uhf42_id"] = pd.Series(
            [_none_if_null(value) for value in locations["uhf42_id"].tolist()],
            index=locations.index,
            dtype="object",
        )
    if "assignment" in locations.columns:
        mask = locations["assignment"].isin(_EXCLUSION_ASSIGNMENTS)
    else:
        mask = pd.Series(True, index=locations.index)
    if len(locations.index) == 0 or not bool(mask.any()):
        return locations
    polygons = _polygons_for_lion(boundaries)
    lines = _lion_lines(lion)
    if "road_segment_id" not in locations.columns or not lines:
        return locations
    for idx in locations.index[mask]:
        key = _segment_key(locations.at[idx, "road_segment_id"])
        line = lines.get(key)
        if line is None:
            continue
        outcome, uhf_id = _lion_winner(line, polygons)
        if outcome == "majority":
            locations.at[idx, "assignment"] = "lion_majority"
            locations.at[idx, "uhf42_id"] = uhf_id
            _remember_rule(locations, idx, "lion_majority")
        elif outcome == "tie":
            locations.at[idx, "assignment"] = "boundary"
            locations.at[idx, "uhf42_id"] = None
            _remember_rule(locations, idx, "boundary")
    if "uhf42_id" in locations.columns:
        locations["uhf42_id"] = pd.Series(
            [_none_if_null(value) for value in locations["uhf42_id"].tolist()],
            index=locations.index,
            dtype="object",
        )
    return locations


def _package_outputs(locations, traffic_rows):
    uhf_by_id = {
        loc_id: _none_if_null(uhf)
        for loc_id, uhf in zip(
            locations["monitoring_location_id"].tolist(),
            locations["uhf42_id"].tolist() if "uhf42_id" in locations.columns else [],
        )
    }
    rows = traffic_rows.reset_index(drop=True)
    chosen = [uhf_by_id.get(loc_id) for loc_id in rows["monitoring_location_id"].tolist()]
    keep = pd.Series([_has_uhf(value) for value in chosen], index=rows.index)
    base_columns = [
        column
        for column in rows.columns
        if column not in {"road_segment_id", "uhf42_id"}
    ]
    kept = rows.loc[keep, base_columns].copy()
    kept["road_segment_id"] = [
        value for value, flag in zip(rows["road_segment_id"].tolist(), keep.tolist()) if flag
    ]
    kept["uhf42_id"] = pd.Series(
        [value for value, flag in zip(chosen, keep.tolist()) if flag],
        index=kept.index,
        dtype="object",
    )
    audit = pd.DataFrame(
        {
            "monitoring_location_id": locations["monitoring_location_id"].tolist(),
            "assignment": locations["assignment"].tolist(),
            "uhf42_id": [
                _none_if_null(value) for value in locations["uhf42_id"].tolist()
            ],
            "borough_traffic": _side_values(locations, "borough_traffic"),
            "borough_polygon": _side_values(locations, "borough_polygon"),
            "rule": _side_values(locations, "rule"),
        },
        columns=list(_AUDIT_COLUMNS),
    )
    audit["uhf42_id"] = pd.Series(audit["uhf42_id"].tolist(), dtype="object")
    excluded = locations["assignment"].isin(_EXCLUSION_ASSIGNMENTS)
    sources = _side_values(locations, "source")
    exclusions = pd.DataFrame(
        {
            "stage": ["assign-uhf"] * int(excluded.sum()),
            "key": locations.loc[excluded, "monitoring_location_id"].tolist(),
            "reason": locations.loc[excluded, "assignment"].tolist(),
            "source": [
                source
                for source, flag in zip(sources, excluded.tolist())
                if flag
            ],
        },
        columns=list(_EXCLUSION_COLUMNS),
    )
    return kept, audit, exclusions


def _location_records(frame, config):
    if len(frame.index) == 0:
        return []
    if "wkt" not in frame.columns:
        raise BoundaryError("missing wkt", 2)
    segments = _column(frame, "segment")
    boroughs = _column(frame, "borough")
    sources = _column(frame, "source")
    parsed = []
    for wkt, segment, borough, source in zip(
        frame["wkt"].tolist(), segments, boroughs, sources
    ):
        x_coord, y_coord = _load_point(wkt)
        epsg = _source_epsg(x_coord, y_coord)
        parsed.append(
            {
                "x": x_coord,
                "y": y_coord,
                "epsg": epsg,
                "segment": _segment_text(segment),
                "borough": borough,
                "source": _plain(source),
            }
        )
    projected = _project_records(parsed)
    step = float(config["geography"]["coordinate_round_meters"])
    records = []
    for item, geom in zip(parsed, projected):
        rounded_x = rounded_axis(float(geom.x), step)
        rounded_y = rounded_axis(float(geom.y), step)
        segment_id = item["segment"]
        digest = hashlib.sha1(
            f"{segment_id}|{rounded_x}|{rounded_y}".encode("utf-8")
        ).hexdigest()
        records.append(
            {
                "monitoring_location_id": digest,
                "road_segment_id": segment_id,
                "source_crs": f"EPSG:{item['epsg']}",
                "borough": item["borough"],
                "source": item["source"],
                "geometry": geom,
            }
        )
    return records


def _project_records(parsed):
    import geopandas as gpd

    projected = [None] * len(parsed)
    groups = {}
    for index, item in enumerate(parsed):
        groups.setdefault(item["epsg"], []).append(index)
    for epsg, indexes in groups.items():
        frame = gpd.GeoDataFrame(
            {"pos": indexes},
            geometry=gpd.points_from_xy(
                [parsed[index]["x"] for index in indexes],
                [parsed[index]["y"] for index in indexes],
            ),
            crs=f"EPSG:{epsg}",
        )
        if epsg != 2263:
            frame = frame.to_crs("EPSG:2263")
        for pos, geom in zip(indexes, frame.geometry):
            projected[pos] = geom
    return projected


def _traffic_rows(frame, records):
    rows = frame.reset_index(drop=True).copy()
    rows["monitoring_location_id"] = [item["monitoring_location_id"] for item in records]
    rows["road_segment_id"] = [item["road_segment_id"] for item in records]
    if len(records) == 0:
        rows["monitoring_location_id"] = pd.Series(dtype="object")
        rows["road_segment_id"] = pd.Series(dtype="object")
    return rows


def _classify_locations(records, prepared, config):
    import geopandas as gpd

    lookup = _borough_lookup(config)
    tolerance_feet = float(config["geography"]["boundary_tolerance_meters"]) * (
        3937 / 1200
    )
    has_borough = "polygon_borough" in prepared.columns
    first = {}
    order = []
    for record in records:
        loc_id = record["monitoring_location_id"]
        if loc_id not in first:
            first[loc_id] = record
            order.append(loc_id)
    if not order:
        return _empty_locations()
    points = gpd.GeoDataFrame(
        {
            "monitoring_location_id": order,
            "traffic_borough": [first[loc_id]["borough"] for loc_id in order],
        },
        geometry=[first[loc_id]["geometry"] for loc_id in order],
        crs="EPSG:2263",
    )
    hits, distances = _within_and_distance(points, prepared)
    uhf_ids = []
    assignments = []
    rules = []
    polygon_boroughs = []
    for loc_id, traffic_borough in zip(order, points["traffic_borough"].tolist()):
        contained = hits.get(loc_id, [])
        distance = distances.get(loc_id, math.inf)
        near_edge = distance <= tolerance_feet
        if near_edge or len(contained) > 1:
            assignments.append("boundary")
            uhf_ids.append(None)
            rules.append("boundary")
            if len(contained) == 1 and has_borough:
                polygon_boroughs.append(_borough_label(contained[0][1], lookup))
            else:
                polygon_boroughs.append("")
            continue
        if len(contained) == 1:
            uhf_id, polygon_borough = contained[0]
            label = _borough_label(polygon_borough, lookup) if has_borough else ""
            polygon_boroughs.append(label)
            if has_borough and not _boroughs_agree(
                traffic_borough, polygon_borough, lookup
            ):
                assignments.append("borough_mismatch")
                uhf_ids.append(None)
                rules.append("borough")
            else:
                assignments.append("interior")
                uhf_ids.append(uhf_id)
                rules.append("within")
            continue
        assignments.append("outside_nyc")
        uhf_ids.append(None)
        rules.append("outside")
        polygon_boroughs.append("")
    borough_traffic = [
        _borough_label(first[loc_id]["borough"], lookup) for loc_id in order
    ]
    sources = [first[loc_id]["source"] for loc_id in order]
    table = {
        "monitoring_location_id": order,
        "road_segment_id": [first[loc_id]["road_segment_id"] for loc_id in order],
        "assignment": assignments,
        "source_crs": [first[loc_id]["source_crs"] for loc_id in order],
    }
    locations = gpd.GeoDataFrame(
        table,
        geometry=[first[loc_id]["geometry"] for loc_id in order],
        crs="EPSG:2263",
    )
    locations["uhf42_id"] = pd.Series(uhf_ids, index=locations.index, dtype="object")
    locations = locations[_location_frame_columns()]
    locations.attrs["borough_traffic"] = borough_traffic
    locations.attrs["borough_polygon"] = polygon_boroughs
    locations.attrs["rule"] = rules
    locations.attrs["source"] = sources
    return locations


def _within_and_distance(points, prepared):
    import geopandas as gpd

    loc_ids = points["monitoring_location_id"].tolist()
    hits = {loc_id: [] for loc_id in loc_ids}
    if len(prepared.index) == 0 or len(points.index) == 0:
        return hits, {loc_id: math.inf for loc_id in loc_ids}
    # Reproject before the within join. Both frames are already EPSG:2263
    # when assignment built them; repeat the conversion for any other caller.
    points = _to_2263(points)
    polygons = _to_2263(prepared)
    edges = [
        geom
        for geom in polygons.geometry.boundary
        if geom is not None and not geom.is_empty
    ]
    if edges:
        edge_union = edges[0] if len(edges) == 1 else union_all(edges)
        distances = dict(
            zip(loc_ids, points.geometry.distance(edge_union).tolist())
        )
    else:
        distances = {loc_id: math.inf for loc_id in loc_ids}
    columns = ["uhf42_id"]
    if "polygon_borough" in polygons.columns:
        columns.append("polygon_borough")
    right = polygons[columns + ["geometry"]]
    joined = gpd.sjoin(points, right, predicate="within", how="left")
    if "polygon_borough" in joined.columns:
        boroughs = joined["polygon_borough"].tolist()
    else:
        boroughs = [None] * len(joined.index)
    for loc_id, uhf, borough in zip(
        joined["monitoring_location_id"].tolist(),
        joined["uhf42_id"].tolist(),
        boroughs,
    ):
        uhf_id = _normalize_uhf_id(uhf)
        if uhf_id is None or uhf_id == "0":
            continue
        hits.setdefault(loc_id, []).append((uhf_id, borough))
    return hits, distances


def _prepare_boundaries(boundaries, asthma_ids, config):
    import geopandas as gpd

    frame = _read_spatial(boundaries)
    matched = _matching_id_column(frame, asthma_ids)
    if matched is not None:
        uhf_ids = _direct_ids(frame[matched].tolist())
    else:
        uhf_ids = _crosswalk_ids(frame, config)
    borough_name = _first_present(frame.columns, _BOROUGH_COLUMNS)
    data = {"uhf42_id": uhf_ids}
    if borough_name is not None:
        data["polygon_borough"] = frame[borough_name].tolist()
    prepared = gpd.GeoDataFrame(data, geometry=list(frame.geometry), crs=frame.crs)
    keep = pd.Series(
        [
            _has_uhf(uhf) and _geom_ok(geom)
            for uhf, geom in zip(prepared["uhf42_id"].tolist(), prepared.geometry)
        ],
        index=prepared.index,
    )
    prepared = prepared.loc[keep].copy()
    if len(prepared.index) == 0:
        empty = {"uhf42_id": pd.Series(dtype="object")}
        if borough_name is not None:
            empty["polygon_borough"] = pd.Series(dtype="object")
        prepared = gpd.GeoDataFrame(empty, geometry=[], crs=frame.crs or "EPSG:2263")
    prepared["uhf42_id"] = pd.Series(
        prepared["uhf42_id"].tolist(), index=prepared.index, dtype="object"
    )
    return _to_2263(prepared)


def _polygons_for_lion(boundaries):
    import geopandas as gpd

    frame = _read_spatial(boundaries)
    if "uhf42_id" in frame.columns:
        uhf_ids = _direct_ids(frame["uhf42_id"].tolist())
        data = {"uhf42_id": uhf_ids}
        prepared = gpd.GeoDataFrame(
            data, geometry=list(frame.geometry), crs=frame.crs
        )
    else:
        config = load_config()
        path = _configured_path(config["geography"].get("id_crosswalk_csv", ""))
        if path:
            uhf_ids = _crosswalk_ids(frame, config)
        else:
            column = _first_present(frame.columns, _ID_COLUMNS)
            if column is None:
                raise BoundaryError("id_crosswalk requires a boundary id column", 2)
            uhf_ids = _direct_ids(frame[column].tolist())
        prepared = gpd.GeoDataFrame(
            {"uhf42_id": uhf_ids},
            geometry=list(frame.geometry),
            crs=frame.crs,
        )
    keep = pd.Series(
        [
            _has_uhf(uhf) and _geom_ok(geom)
            for uhf, geom in zip(prepared["uhf42_id"].tolist(), prepared.geometry)
        ],
        index=prepared.index,
    )
    prepared = prepared.loc[keep].copy()
    prepared["uhf42_id"] = pd.Series(
        prepared["uhf42_id"].tolist(), index=prepared.index, dtype="object"
    )
    if len(prepared.index) == 0:
        prepared = gpd.GeoDataFrame(
            {"uhf42_id": pd.Series(dtype="object")},
            geometry=[],
            crs="EPSG:2263",
        )
    return _to_2263(prepared)


def _lion_lines(lion):
    frame = _to_2263(_read_spatial(lion))
    column = _first_present(frame.columns, _LION_COLUMNS)
    if column is None:
        return {}
    groups = {}
    for value, geom in zip(frame[column].tolist(), frame.geometry):
        if not _geom_ok(geom):
            continue
        key = _segment_key(value)
        if key == "":
            continue
        groups.setdefault(key, []).append(geom)
    lines = {}
    for key, geoms in groups.items():
        lines[key] = geoms[0] if len(geoms) == 1 else union_all(geoms)
    return lines


def _lion_winner(line, polygons):
    totals = {}
    for uhf, geom in zip(polygons["uhf42_id"].tolist(), polygons.geometry):
        uhf_id = _normalize_uhf_id(uhf)
        if uhf_id is None or uhf_id == "0" or not _geom_ok(geom):
            continue
        try:
            piece = line.intersection(geom)
        except Exception:
            continue
        if piece is None or piece.is_empty:
            continue
        length = float(piece.length)
        if length > 0:
            totals[uhf_id] = totals.get(uhf_id, 0.0) + length
    if not totals:
        return None, None
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) >= 2 and ranked[0][1] - ranked[1][1] <= 0.001:
        return "tie", None
    return "majority", ranked[0][0]


def _matching_id_column(frame, asthma_ids):
    target = _id_set(asthma_ids, drop_zero=False)
    columns = list(frame.columns)
    for name in _ID_COLUMNS:
        if name not in columns:
            continue
        if _id_set(frame[name].tolist(), drop_zero=True) == target:
            return name
    return None


def _crosswalk_ids(frame, config):
    path = _configured_path(config["geography"].get("id_crosswalk_csv", ""))
    if path == "":
        raise BoundaryError("geography.id_crosswalk_csv is required", 2)
    column = _first_present(frame.columns, _ID_COLUMNS)
    if column is None:
        raise BoundaryError("id_crosswalk requires a boundary id column", 2)
    mapping = _read_crosswalk(path)
    mapped = []
    for value in frame[column].tolist():
        key = _normalize_uhf_id(value)
        if key is None or key == "0":
            mapped.append(None)
            continue
        mapped.append(mapping.get(key))
    return mapped


def _read_crosswalk(path):
    file_path = Path(path)
    if not file_path.is_file():
        raise BoundaryError(f"id_crosswalk file is missing: {file_path.as_posix()}", 2)
    try:
        table = pd.read_csv(file_path, dtype=str, keep_default_na=False)
    except Exception:
        raise BoundaryError(
            f"id_crosswalk could not be read: {file_path.as_posix()}", 2
        ) from None
    if "boundary_id" not in table.columns or "uhf42_id" not in table.columns:
        raise BoundaryError("id_crosswalk requires boundary_id and uhf42_id", 2)
    mapping = {}
    for boundary_id, uhf_id in zip(table["boundary_id"].tolist(), table["uhf42_id"].tolist()):
        key = _normalize_uhf_id(boundary_id)
        value = _normalize_uhf_id(uhf_id)
        if key is None or key == "0" or value is None or value == "0":
            continue
        mapping.setdefault(key, value)
    if not mapping:
        raise BoundaryError("id_crosswalk has no rows", 2)
    return mapping


def _direct_ids(values):
    mapped = []
    for value in values:
        uhf = _normalize_uhf_id(value)
        if uhf is None or uhf == "0":
            mapped.append(None)
        else:
            mapped.append(uhf)
    return mapped


def _id_set(values, drop_zero):
    found = set()
    for value in values:
        uhf = _normalize_uhf_id(value)
        if uhf is None:
            continue
        if drop_zero and uhf == "0":
            continue
        found.add(uhf)
    return found


def _read_spatial(value):
    import geopandas as gpd

    if isinstance(value, gpd.GeoDataFrame):
        return value.copy()
    try:
        return gpd.read_file(value)
    except Exception as exc:
        detail = str(exc).strip() or str(value)
        raise BoundaryError(detail, 2) from None


def _to_2263(frame):
    if len(frame.index) == 0 and frame.crs is None:
        return frame.set_crs("EPSG:2263")
    if frame.crs is None:
        x_coord, y_coord = _sample_xy(frame)
        frame = frame.set_crs(f"EPSG:{_source_epsg(x_coord, y_coord)}")
    try:
        epsg = frame.crs.to_epsg()
    except Exception:
        epsg = None
    if epsg == 2263:
        return frame
    return frame.to_crs("EPSG:2263")


def _sample_xy(frame):
    for geom in frame.geometry:
        if not _geom_ok(geom):
            continue
        point = geom.representative_point()
        return float(point.x), float(point.y)
    raise BoundaryError("empty geometry", 2)


def _source_epsg(x_coord, y_coord):
    try:
        x_coord = float(x_coord)
        y_coord = float(y_coord)
    except (TypeError, ValueError):
        raise BoundaryError(
            f"unrecognized coordinate range {x_coord} {y_coord}", 2
        ) from None
    if -74.3 <= x_coord <= -73.6 and 40.4 <= y_coord <= 40.95:
        return 4326
    if x_coord > 1000 and y_coord > 1000:
        return 2263
    raise BoundaryError(f"unrecognized coordinate range {x_coord} {y_coord}", 2)


def _load_point(value):
    if isinstance(value, bool) or _missing(value):
        raise BoundaryError("missing wkt", 2)
    raw = str(value).strip()
    if raw == "" or raw.casefold() in {"nan", "none", "<na>"}:
        raise BoundaryError("missing wkt", 2)
    body = raw
    if body.upper().startswith("SRID="):
        parts = body.split(";", 1)
        body = parts[1] if len(parts) == 2 else body
    try:
        geom = shapely_wkt.loads(body)
    except Exception:
        raise BoundaryError(f"unrecognized wkt {raw}", 2) from None
    if geom is None or geom.is_empty or geom.geom_type != "Point":
        raise BoundaryError(f"unrecognized wkt {raw}", 2)
    return float(geom.x), float(geom.y)


def _empty_locations():
    import geopandas as gpd

    table = {name: pd.Series(dtype="object") for name in _LOCATION_COLUMNS}
    frame = gpd.GeoDataFrame(table, geometry=[], crs="EPSG:2263")
    frame.attrs["borough_traffic"] = []
    frame.attrs["borough_polygon"] = []
    frame.attrs["rule"] = []
    frame.attrs["source"] = []
    return frame


def _side_values(locations, name):
    """Borough, rule, and source stay beside the location columns."""
    if name in locations.columns:
        return locations[name].tolist()
    stored = locations.attrs.get(name)
    if isinstance(stored, list) and len(stored) == len(locations.index):
        return list(stored)
    return [""] * len(locations.index)


def _remember_rule(locations, idx, rule):
    """Record a LION rule without adding it to the location columns."""
    if "rule" in locations.columns:
        locations.at[idx, "rule"] = rule
    loc_id = locations.at[idx, "monitoring_location_id"]
    updates = locations.attrs.get("rule_updates")
    if not isinstance(updates, dict):
        updates = {}
        locations.attrs["rule_updates"] = updates
    updates[loc_id] = rule
    rules = locations.attrs.get("rule")
    if not isinstance(rules, list):
        return
    ids = locations["monitoring_location_id"].tolist()
    if len(rules) != len(ids):
        return
    for pos, current in enumerate(ids):
        if current == loc_id:
            rules[pos] = rule
            break


def _location_frame_columns():
    return list(_LOCATION_COLUMNS) + ["geometry"]


def _borough_lookup(config):
    lookup = {}
    aliases = config.get("borough_aliases") or {}
    for canonical, names in aliases.items():
        label = str(canonical).strip()
        lookup[label.casefold()] = label
        for alias in names or []:
            lookup[str(alias).strip().casefold()] = label
    return lookup


def _borough_label(value, lookup):
    canonical = _canonical_borough(value, lookup)
    if canonical is not None:
        return canonical
    if _missing(value):
        return ""
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "none", "<na>"}:
        return ""
    return text


def _canonical_borough(value, lookup):
    if _missing(value):
        return None
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "none", "<na>"}:
        return None
    return lookup.get(text.casefold())


def _boroughs_agree(traffic_borough, polygon_borough, lookup):
    traffic_name = _canonical_borough(traffic_borough, lookup)
    polygon_name = _canonical_borough(polygon_borough, lookup)
    return traffic_name is not None and traffic_name == polygon_name


def _column(frame, name):
    if name not in frame.columns:
        return [None] * len(frame.index)
    return frame[name].tolist()


def _segment_text(value):
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


def _segment_key(value):
    text = _segment_text(value)
    if text == "":
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    if math.isfinite(number) and number.is_integer():
        return str(int(number))
    return text


def _plain(value):
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return str(value)
    if _missing(value):
        return ""
    text = str(value).strip()
    if text.casefold() in {"nan", "none", "<na>"}:
        return ""
    return text


def _configured_path(value):
    if value is None or _missing(value):
        return ""
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


def _none_if_null(value):
    if _missing(value):
        return None
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "none", "<na>"}:
        return None
    return text


def _has_uhf(value):
    return _none_if_null(value) is not None


def _geom_ok(geom):
    if geom is None:
        return False
    try:
        return not geom.is_empty
    except Exception:
        return False
