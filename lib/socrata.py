"""Read Socrata schemas, year counts, and one paged source-year."""

import hashlib
import json
import time
from pathlib import Path

import pandas as pd
import requests

_SOURCES = ("automated", "historical", "aadt")
_SCHEMA_PATH = Path("outputs/audit/schema_snapshot.json")
_COUNT_COLUMNS = ["source", "year", "record_count", "schema_ok"]


class SocrataError(Exception):
    def __init__(self, message, exit_code, leave_folder=False):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.leave_folder = leave_folder


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
    _SCHEMA_PATH.write_text(json.dumps(_schema_document(snapshot), indent=2) + "\n")
    return snapshot


def fetch_year_counts(config, schema):
    """Request count(*) for each target year and one grouped year list per API.

    No traffic volumes are paged. schema_ok is true when a year alias, or that
    source's date alias, was found. date_extract_y is used when no configured
    year alias is live and the field table has a date alias. paginate uses the
    same rule for its year filter.
    """
    samples = getattr(schema, "samples", {})
    rows = []
    for source in _SOURCES:
        raw = schema[source]
        live = _live_names(raw)
        fields = config["fields"][source]
        field, extract = _year_source(live, fields)
        if field is None:
            raise SocrataError(", ".join(live), 2)
        url = config["api"][source]["base_url"]
        sample = samples.get(source) or _embedded_sample(raw)
        grouped = f"date_extract_y({field})" if extract else field
        _request_grouped_years(url, grouped, config, live)
        for year in config["years"]["targets"]:
            year_number = int(year)
            where = f"date_extract_y({field})={year_number}" if extract else None
            count = _request_year_count(
                url, field, year_number, sample, config, live, where
            )
            rows.append(
                {
                    "source": source,
                    "year": year_number,
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


def _schema_document(snapshot):
    samples = getattr(snapshot, "samples", {}) or {}
    document = {}
    for source in _SOURCES:
        sample = samples.get(source) if isinstance(samples, dict) else None
        if not isinstance(sample, dict):
            sample = {}
        document[source] = {
            "columns": [str(name) for name in snapshot[source]],
            "sample": sample,
        }
    return document


def _live_names(raw):
    if isinstance(raw, dict) and isinstance(raw.get("columns"), list):
        return [str(name) for name in raw["columns"]]
    if isinstance(raw, dict):
        return [str(name) for name in raw]
    return [str(name) for name in raw]


def _embedded_sample(raw):
    """Return the $limit=1 row stored for one source.

    fetch_schema writes {"columns": [...], "sample": {field: value}}.
    A snapshot entry may also be that row itself, {field: value}.
    """
    if not isinstance(raw, dict):
        return {}
    sample = raw.get("sample")
    if isinstance(sample, dict):
        return sample
    if isinstance(raw.get("columns"), list):
        return {}
    return raw


def _year_field(live_keys, aliases):
    present = set(live_keys)
    for alias in aliases:
        if alias in present:
            return alias
    return None


def _year_source(live, fields):
    """Return the live year field and whether the filter extracts it from a date.

    A configured year alias wins. When none is live, the first live date alias
    is filtered with date_extract_y and the year is not quoted.
    """
    field = _year_field(live, fields.get("year") or [])
    if field is not None:
        return field, False
    field = _year_field(live, fields.get("date") or [])
    if field is not None:
        return field, True
    return None, False


def _year_clause(field, year, sample, extract):
    if extract:
        return f"date_extract_y({field})={int(year)}"
    return _year_where(field, year, sample)


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


def _request_year_count(url, field, year, sample, config, live, where=None):
    if where is None:
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
    _content, payload, final_url = _exchange(url, params, config, failed_query)
    return payload, final_url


def _exchange(url, params, config, failed_query=None):
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
            payload = response.json()
        except ValueError:
            raise SocrataError(full_url, 1) from None
        body = response.content
        if not isinstance(body, (bytes, bytearray)):
            body = b""
        return bytes(body), payload, full_url
    raise SocrataError(full_url, 1)


_PAGE_FIELDS = (
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
)


def paginate(config, source, year, schema, output_path):
    """Page one source-year into output_path and return the row count.

    Writes pages.jsonl and checkpoint.json beside the parquet. pages.jsonl is
    removed only after records.parquet is written. coverage.parquet leaves
    pages.jsonl and the checkpoint in place. Does not create outputs/.
    """
    if config["api"]["order_field"] != ":id":
        raise SocrataError("order_field must be :id", 2)
    year_number = int(year)
    output_path = Path(output_path)
    directory = output_path.parent
    jsonl_path = directory / "pages.jsonl"
    checkpoint_path = directory / "checkpoint.json"
    offset, rows_so_far, fresh = _resume_state(
        jsonl_path, checkpoint_path, source, year_number
    )
    live, sample = _schema_live_and_sample(schema, source)
    fields = config["fields"][source]
    year_name, extract = _year_source(live, fields)
    if year_name is None:
        raise SocrataError(f"{source} {year_number} {', '.join(live)}", 2)
    selected = _select_fields(config, source, live)
    if extract and year_name not in selected:
        selected.append(year_name)
    where = _year_clause(year_name, year_number, sample, extract)
    base_url = config["api"][source]["base_url"]
    _content, payload, _final_url = _exchange(
        base_url,
        {"$select": "count(*)", "$where": where},
        config,
    )
    try:
        expected = _parse_count(payload, live)
    except SocrataError as exc:
        raise SocrataError(
            f"{source} {year_number} {exc.message}", exc.exit_code
        ) from None
    page_size = int(config["api"]["page_size"])
    directory.mkdir(parents=True, exist_ok=True)
    if output_path.name == "records.parquet" and output_path.exists():
        output_path.unlink()
    appending = not fresh
    while rows_so_far < expected:
        _body, page, page_url = _exchange(
            base_url,
            {
                "$select": ",".join(selected),
                "$where": where,
                "$order": ":id",
                "$limit": page_size,
                "$offset": offset,
            },
            config,
        )
        if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
            raise SocrataError(page_url, 1)
        page_count = len(page)
        if page_count < page_size and rows_so_far + page_count != expected:
            raise SocrataError(
                f"{source} {year_number} {expected} {rows_so_far + page_count}",
                2,
            )
        _append_jsonl(jsonl_path, page, "a" if appending else "w")
        appending = True
        rows_so_far += page_count
        offset += page_count
        _write_checkpoint(
            checkpoint_path,
            offset,
            rows_so_far,
            hashlib.sha256(_body).hexdigest(),
        )
        if page_count < page_size:
            break
    if rows_so_far != expected:
        raise SocrataError(f"{source} {year_number} {expected} {rows_so_far}", 2)
    if fresh and expected == 0:
        records = []
    else:
        records = _read_jsonl(jsonl_path)
    if len(records) != expected:
        raise SocrataError(f"{source} {year_number} {expected} {len(records)}", 2)
    identities = [row.get(":id") for row in records]
    unique_ids = {_identity(value) for value in identities}
    if len(identities) != len(unique_ids):
        duplicate_count = len(identities) - len(unique_ids)
        raise SocrataError(
            f"{source} {year_number} {len(identities)} {duplicate_count}",
            2,
        )
    frame = _page_frame(records, selected)
    frame.to_parquet(output_path, index=False)
    if expected == 0:
        _write_checkpoint(
            checkpoint_path,
            0,
            0,
            hashlib.sha256(b"").hexdigest(),
        )
    if output_path.name == "records.parquet":
        if jsonl_path.exists():
            jsonl_path.unlink()
    elif output_path.name == "coverage.parquet" and not jsonl_path.exists():
        jsonl_path.write_text("", encoding="utf-8")
    return int(len(records))


def _schema_live_and_sample(schema, source):
    raw = schema[source]
    live = _live_names(raw)
    samples = getattr(schema, "samples", None)
    if not isinstance(samples, dict) and isinstance(schema, dict):
        candidate = schema.get("samples")
        if isinstance(candidate, dict):
            samples = candidate
    if not isinstance(samples, dict):
        samples = {}
    sample = samples.get(source) or _embedded_sample(raw)
    if not isinstance(sample, dict):
        sample = {}
    return live, sample


def _select_fields(config, source, live):
    names = [":id"]
    canonical = list(_PAGE_FIELDS)
    if source == "aadt":
        canonical.append("aadt")
    fields = config["fields"][source]
    for name in canonical:
        found = _year_field(live, fields.get(name) or [])
        if found is not None and found not in names:
            names.append(found)
    return names


def _resume_state(jsonl_path, checkpoint_path, source, year_number):
    jsonl_exists = jsonl_path.exists()
    checkpoint_exists = checkpoint_path.exists()
    if jsonl_exists and checkpoint_exists:
        lines = _jsonl_line_count(jsonl_path)
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            recorded = int(checkpoint["row_count"])
            offset = int(checkpoint["next_offset"])
        except (OSError, ValueError, KeyError, TypeError):
            raise SocrataError(
                f"{source} {year_number} checkpoint mismatch",
                2,
                leave_folder=True,
            ) from None
        if lines != recorded:
            raise SocrataError(
                f"{source} {year_number} {lines} {recorded}",
                2,
                leave_folder=True,
            )
        return offset, lines, False
    return 0, 0, True


def _jsonl_line_count(path):
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def _append_jsonl(path, rows, mode):
    with path.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        handle.flush()


def _write_checkpoint(path, next_offset, row_count, page_hash):
    payload = {
        "next_offset": int(next_offset),
        "row_count": int(row_count),
        "page_hash": page_hash,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            rows.append(json.loads(text))
    return rows


def _identity(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def _page_frame(records, selected):
    frame = pd.DataFrame(records)
    if len(frame.index) == 0:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in selected})
    ordered = [name for name in selected if name in frame.columns]
    extras = [name for name in frame.columns if name not in ordered]
    return frame.loc[:, ordered + extras]


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
