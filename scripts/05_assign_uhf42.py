"""Assign each monitor to one UHF42 neighborhood."""

import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.geo import BoundaryError, _package_outputs, assign_points, resolve_non_interior

_SHAPEFILE = Path("data/raw/geography/UHF_42_DOHMH_2009/UHF_42_DOHMH_2009_WGS84.shp")
_TRAFFIC = Path("outputs/data/cleaned_traffic.parquet")
_ASTHMA = Path("outputs/data/cleaned_asthma.csv")
_LOCATIONS = Path("outputs/data/traffic_monitoring_locations.geojson")
_WITH_UHF = Path("outputs/data/traffic_with_uhf42.parquet")
_AUDIT = Path("outputs/quality/spatial_join_audit.csv")
_EXCLUSIONS = Path("outputs/audit/exclusions_geo.csv")
_NON_INTERIOR = ("outside_nyc", "boundary", "borough_mismatch")
_GEOJSON_COLUMNS = (
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


def _load_config():
    from lib import config as config_lib

    return config_lib.load_config()


def _configured_path(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.casefold() in {"nan", "none", "<na>"}:
        return ""
    return text


def _null(value):
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _run(shapefile):
    traffic = pd.read_parquet(_TRAFFIC)
    asthma = pd.read_csv(_ASTHMA, dtype=str, keep_default_na=False)
    if "uhf42_id" not in asthma.columns:
        raise BoundaryError("uhf42_id", 2)
    locations, traffic_out, audit, exclusions = assign_points(
        traffic, shapefile, asthma["uhf42_id"].tolist()
    )
    lion_path = _configured_path(_load_config()["geography"].get("lion_path", ""))
    mask = locations["assignment"].isin(list(_NON_INTERIOR))
    if bool(mask.any()) and lion_path:
        prepared = locations.attrs.get("prepared_boundaries", shapefile)
        traffic_rows = locations.attrs["traffic_rows"]
        resolved = resolve_non_interior(
            locations.loc[mask].copy(), prepared, lion_path
        )
        updated = locations.copy()
        updated["uhf42_id"] = pd.Series(
            [_none(value) for value in updated["uhf42_id"].tolist()],
            index=updated.index,
            dtype="object",
        )
        updated.loc[resolved.index, "assignment"] = resolved["assignment"].tolist()
        updated.loc[resolved.index, "rule"] = resolved["rule"].tolist()
        updated.loc[resolved.index, "uhf42_id"] = [
            _none(value) for value in resolved["uhf42_id"].tolist()
        ]
        updated.attrs["traffic_rows"] = traffic_rows
        updated.attrs["prepared_boundaries"] = prepared
        traffic_out, audit, exclusions = _package_outputs(updated, traffic_rows)
        locations = updated
    return locations, traffic_out, audit, exclusions


def _none(value):
    if _null(value):
        return None
    return value


def _csv_value(value):
    if _null(value):
        return ""
    return str(value)


def _write_csv(path, columns, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(columns)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        series = [frame[column].tolist() for column in columns]
        for row in zip(*series):
            writer.writerow([_csv_value(value) for value in row])


def _write_locations(locations, path):
    import geopandas as gpd

    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    for column in _GEOJSON_COLUMNS:
        data[column] = [_none(value) for value in locations[column].tolist()]
    frame = gpd.GeoDataFrame(
        data,
        geometry=list(locations.geometry),
        crs=locations.crs or "EPSG:2263",
    )
    for column in _GEOJSON_COLUMNS:
        frame[column] = pd.Series(data[column], index=frame.index, dtype="object")
    frame.to_file(path, driver="GeoJSON")


def _write(locations, traffic_out, audit, exclusions):
    _write_locations(locations, _LOCATIONS)
    _WITH_UHF.parent.mkdir(parents=True, exist_ok=True)
    traffic_out.to_parquet(_WITH_UHF, index=False)
    _write_csv(_AUDIT, _AUDIT_COLUMNS, audit)
    _write_csv(_EXCLUSIONS, _EXCLUSION_COLUMNS, exclusions)


def _counts(locations):
    assignment = locations["assignment"]
    assigned = int(assignment.isin(["interior", "lion_majority"]).sum())
    unmatched = int((assignment == "outside_nyc").sum())
    boundary = int((assignment == "boundary").sum())
    mismatched = int((assignment == "borough_mismatch").sum())
    return assigned, unmatched, boundary, mismatched


def main():
    if not _SHAPEFILE.is_file():
        print(
            "missing shapefile data/raw/geography/UHF_42_DOHMH_2009/"
            "UHF_42_DOHMH_2009_WGS84.shp; run select-sources"
        )
        return 2
    if not _TRAFFIC.is_file():
        print(_TRAFFIC.as_posix())
        return 2
    if not _ASTHMA.is_file():
        print(_ASTHMA.as_posix())
        return 2
    try:
        locations, traffic_out, audit, exclusions = _run(_SHAPEFILE)
    except BoundaryError as exc:
        print(exc.message)
        return int(exc.exit_code)
    _write(locations, traffic_out, audit, exclusions)
    assigned, unmatched, boundary, mismatched = _counts(locations)
    print(f"locations assigned {assigned}")
    print(f"unmatched {unmatched}")
    print(f"on a boundary {boundary}")
    print(f"borough-mismatched {mismatched}")
    if unmatched or boundary or mismatched:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
