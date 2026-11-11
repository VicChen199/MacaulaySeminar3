"""Read Socrata schemas and year counts for the traffic APIs."""

import json
import time
from pathlib import Path

import pandas as pd
import requests

_SOURCES = ("automated", "historical", "aadt")
_SCHEMA_PATH = Path("outputs/audit/schema_snapshot.json")
_COUNT_COLUMNS = ["source", "year", "record_count", "schema_ok"]


class SocrataError(Exception):
    def __init__(self, message, exit_code):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class _Snapshot(dict):
    def __init__(self):
        super().__init__()
        self.samples = {}


def fetch_schema(config):
    """Request $limit=1 from each traffic API and write the live JSON keys."""
    snapshot = _Snapshot()
    for source in _SOURCES:
        url = config["api"][source]["base_url"]
        payload, final_url = _get_json(url, {"$limit": 1}, config)
        names, sample = _parse_limit1(payload, final_url)
        snapshot[source] = names
        snapshot.samples[source] = sample
    _SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SCHEMA_PATH.write_text(json.dumps(snapshot, indent=2) + "\n")
    return snapshot


def fetch_year_counts(config, schema):
    """Request count(*) for each target year and one grouped year list per API.

    No traffic volumes are paged. schema_ok is true when the year alias was found.
    """
    samples = getattr(schema, "samples", {})
    rows = []
    for source in _SOURCES:
        live = [str(name) for name in schema[source]]
        field = _year_field(live, config["fields"][source]["year"])
        if field is None:
            raise SocrataError(", ".join(live), 2)
        url = config["api"][source]["base_url"]
        sample = samples.get(source) or {}
        _request_grouped_years(url, field, config, live)
        for year in config["years"]["targets"]:
            count = _request_year_count(url, field, int(year), sample, config, live)
            rows.append(
                {
                    "source": source,
                    "year": int(year),
                    "record_count": count,
                    "schema_ok": True,
                }
            )
    frame = pd.DataFrame(rows, columns=_COUNT_COLUMNS)
    if len(frame.index):
        frame["year"] = frame["year"].astype(int)
        frame["record_count"] = frame["record_count"].astype(int)
        frame["schema_ok"] = frame["schema_ok"].astype(bool)
    return frame


def _year_field(live_keys, aliases):
    present = set(live_keys)
    for alias in aliases:
        if alias in present:
            return alias
    return None


def _parse_limit1(payload, url):
    if not isinstance(payload, list):
        raise SocrataError(url, 1)
    if not payload:
        return [], {}
    row = payload[0]
    if not isinstance(row, dict):
        raise SocrataError(url, 1)
    return list(row.keys()), row


def _request_grouped_years(url, field, config, live):
    payload, final_url = _get_json(
        url,
        {"$select": f"{field},count(*)", "$group": field},
        config,
        failed_query=", ".join(live),
    )
    if not isinstance(payload, list):
        raise SocrataError(final_url, 1)


def _request_year_count(url, field, year, sample, config, live):
    where = _year_where(field, year, sample)
    payload, _final_url = _get_json(
        url,
        {"$select": "count(*)", "$where": where},
        config,
        failed_query=", ".join(live),
    )
    return _parse_count(payload, live)


def _year_where(field, year, sample):
    value = sample.get(field) if isinstance(sample, dict) else None
    if isinstance(value, str):
        return f"{field}='{year}'"
    return f"{field}={year}"


def _parse_count(payload, live):
    message = ", ".join(live)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise SocrataError(message, 2)
    for key, value in payload[0].items():
        if str(key).lower().startswith("count"):
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise SocrataError(message, 2) from None
            if not number.is_integer():
                raise SocrataError(message, 2)
            return int(number)
    raise SocrataError(message, 2)


def _headers(config):
    api = config["api"]
    headers = {"User-Agent": api["user_agent"]}
    token = api.get("app_token")
    if token is not None and str(token).strip():
        headers["X-App-Token"] = str(token).strip()
    return headers


def _get_json(url, params, config, failed_query=None):
    headers = _headers(config)
    timeout = config["api"]["timeout_seconds"]
    retries = int(config["api"]["max_retries"])
    full_url = requests.Request("GET", url, params=params).prepare().url
    response = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException:
            if attempt >= retries:
                raise SocrataError(full_url, 1) from None
            _pause(None, config, attempt)
            continue
        full_url = response.url or full_url
        status = response.status_code
        if status == 429 or status >= 500:
            if attempt >= retries:
                raise SocrataError(full_url, 1)
            _pause(response, config, attempt)
            continue
        if status >= 400:
            if status == 400 and failed_query is not None:
                raise SocrataError(failed_query, 2)
            raise SocrataError(full_url, 1)
        try:
            return response.json(), full_url
        except ValueError:
            raise SocrataError(full_url, 1) from None
    raise SocrataError(full_url, 1)


def _pause(response, config, attempt):
    backoff = config["api"].get("retry_backoff_seconds") or 0
    delay = float(backoff) * (attempt + 1)
    if response is not None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                delay = max(delay, float(header))
            except ValueError:
                pass
    if delay > 0:
        time.sleep(delay)
