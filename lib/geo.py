"""Local UHF42 boundaries for source selection."""

import json
import numbers
from pathlib import Path
from urllib.parse import quote

import requests

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
    """Download pinned UHF42 files when SOURCE.txt is absent, then validate."""
    source_txt = _ROOT / "SOURCE.txt"
    shapefile = _ROOT / _SHAPEFILE
    if not source_txt.is_file():
        _download_boundaries(config, source_txt)
    _validate_shapefile(shapefile)
    return shapefile


def _download_boundaries(config, source_txt):
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
    source_txt.parent.mkdir(parents=True, exist_ok=True)
    source_txt.write_text(sha + "\n", encoding="utf-8")


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
    ids = []
    if id_name is not None:
        ids = [_normalize_uhf_id(value) for value in frame[id_name]]
    geometries_ok = len(frame.index) == 42 and all(
        _geometry_ok(geom) for geom in frame.geometry
    )
    ids_ok = (
        len(ids) == 42
        and all(item is not None for item in ids)
        and len(set(ids)) == 42
    )
    if not (geometries_ok and ids_ok):
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
