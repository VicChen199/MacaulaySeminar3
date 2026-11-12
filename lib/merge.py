"""Join neighborhood traffic means to asthma rates."""

import math
import numbers

import pandas as pd

from lib.config import load_config

_MERGED_COLUMNS = (
    "uhf42_id",
    "uhf42_name",
    "borough",
    "period",
    "asthma_ed_count",
    "asthma_ed_rate_per_10000",
    "traffic_mean_vehicles_per_hour",
    "traffic_median_vehicles_per_hour",
    "traffic_sd_vehicles_per_hour",
    "n_road_segments",
    "n_observation_days",
    "n_complete_hours",
    "traffic_source",
    "coverage_status",
    "exclusion_reason",
    "traffic_mean_hour_balanced",
)
_MISSING_COLUMNS = (
    "period",
    "uhf42_id",
    "missing_rate",
    "missing_traffic",
    "exclusion_reason",
)
_EXCLUSION_COLUMNS = ("stage", "key", "reason", "source")
_TEXT_COLUMNS = ("uhf42_name", "borough", "traffic_source", "exclusion_reason")
_MAX_NEIGHBORHOODS = 42


class MergeError(Exception):
    def __init__(self, message, exit_code=2):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def merge_uhf_period(asthma, traffic, comparison):
    """Outer-join asthma and traffic on neighborhood and period.

    Does not write files and does not use the network. A null rate stays
    null. A null traffic mean stays null. recommended_source is not changed.
    """
    config = load_config()
    threshold = int(config["thresholds"]["min_segments_per_uhf"])
    asthma_frame = _as_frame(asthma)
    traffic_frame = _as_frame(traffic)
    comparison_frame = _as_frame(comparison).copy()
    _require_columns(asthma_frame, ("uhf42_id", "period"))
    _require_columns(traffic_frame, ("uhf42_id", "period"))
    _require_columns(comparison_frame, ("period", "n_matched", "n_usable", "recommended_source"))
    _reject_duplicates(asthma_frame)
    _reject_duplicates(traffic_frame)

    merged = _join(asthma_frame, traffic_frame)
    _reject_over_cap(merged)
    classified = _classify(merged, threshold)
    updated = _fill_counts(comparison_frame, asthma_frame, traffic_frame, classified)
    missing = _missing_report(classified)
    exclusions = _exclusions(classified)
    return (
        classified.loc[:, list(_MERGED_COLUMNS)].reset_index(drop=True),
        updated.reset_index(drop=True),
        missing.loc[:, list(_MISSING_COLUMNS)].reset_index(drop=True),
        exclusions.loc[:, list(_EXCLUSION_COLUMNS)].reset_index(drop=True),
    )


def _as_frame(value):
    if isinstance(value, pd.DataFrame):
        return value.copy()
    return pd.DataFrame(value)


def _require_columns(frame, names):
    missing = [name for name in names if name not in frame.columns]
    if missing:
        raise MergeError(", ".join(str(column) for column in frame.columns), 2)


def _reject_duplicates(frame):
    seen = set()
    for uhf_id, period in zip(
        frame["uhf42_id"].tolist(),
        frame["period"].tolist(),
    ):
        key = (_identity(uhf_id), _label(period))
        if key in seen:
            raise MergeError(f"{key[0]} {key[1]}", 2)
        seen.add(key)


def _reject_over_cap(frame):
    if len(frame.index) == 0:
        return
    for period, group in frame.groupby("period", sort=False):
        count = len({_identity(value) for value in group["uhf42_id"].tolist()})
        if count > _MAX_NEIGHBORHOODS:
            raise MergeError(f"{_label(period)} {count}", 2)


def _join(asthma, traffic):
    left = asthma.copy()
    right = traffic.copy()
    left["uhf42_id"] = [_identity(value) for value in left["uhf42_id"].tolist()]
    left["period"] = [_label(value) for value in left["period"].tolist()]
    right["uhf42_id"] = [_identity(value) for value in right["uhf42_id"].tolist()]
    right["period"] = [_label(value) for value in right["period"].tolist()]
    keep_right = [
        column
        for column in right.columns
        if column not in {"uhf42_id", "period", "coverage_status"}
    ]
    joined = left.merge(
        right.loc[:, ["uhf42_id", "period", *keep_right]],
        how="outer",
        on=["uhf42_id", "period"],
    )
    return joined


def _classify(frame, threshold):
    rows = []
    for record in frame.to_dict(orient="records"):
        rate = _number(record.get("asthma_ed_rate_per_10000"))
        traffic = _number(record.get("traffic_mean_vehicles_per_hour"))
        segments = _number(record.get("n_road_segments"))
        missing_rate = rate is None
        missing_traffic = traffic is None
        if missing_rate and missing_traffic:
            reason = "missing_rate;missing_traffic"
        elif missing_rate:
            reason = "missing_rate"
        elif missing_traffic:
            reason = "missing_traffic"
        elif segments is None or segments < threshold:
            reason = "segment_threshold"
        else:
            reason = ""
        status = "valid" if reason == "" else "insufficient"
        rows.append(
            {
                "uhf42_id": _identity(record.get("uhf42_id")),
                "uhf42_name": _text(record.get("uhf42_name")),
                "borough": _text(record.get("borough")),
                "period": _label(record.get("period")),
                "asthma_ed_count": _number(record.get("asthma_ed_count")),
                "asthma_ed_rate_per_10000": rate,
                "traffic_mean_vehicles_per_hour": traffic,
                "traffic_median_vehicles_per_hour": _number(
                    record.get("traffic_median_vehicles_per_hour")
                ),
                "traffic_sd_vehicles_per_hour": _number(
                    record.get("traffic_sd_vehicles_per_hour")
                ),
                "n_road_segments": _whole(segments),
                "n_observation_days": _whole(_number(record.get("n_observation_days"))),
                "n_complete_hours": _whole(_number(record.get("n_complete_hours"))),
                "traffic_source": _text(record.get("traffic_source")),
                "coverage_status": status,
                "exclusion_reason": reason,
                "traffic_mean_hour_balanced": _number(
                    record.get("traffic_mean_hour_balanced")
                ),
                "missing_rate": missing_rate,
                "missing_traffic": missing_traffic,
            }
        )
    return pd.DataFrame(rows)


def _fill_counts(comparison, asthma, traffic, classified):
    asthma_ids = _ids_by_period(asthma)
    traffic_ids = _ids_by_period(traffic)
    usable = {}
    for record in classified.to_dict(orient="records"):
        period = record["period"]
        usable.setdefault(period, 0)
        if record["coverage_status"] == "valid":
            usable[period] += 1
    matched = []
    usable_values = []
    for period in comparison["period"].tolist():
        label = _label(period)
        both = asthma_ids.get(label, set()) & traffic_ids.get(label, set())
        matched.append(len(both))
        usable_values.append(int(usable.get(label, 0)))
    comparison["n_matched"] = matched
    comparison["n_usable"] = usable_values
    return comparison


def _ids_by_period(frame):
    found = {}
    if "uhf42_id" not in frame.columns or "period" not in frame.columns:
        return found
    for uhf_id, period in zip(frame["uhf42_id"].tolist(), frame["period"].tolist()):
        found.setdefault(_label(period), set()).add(_identity(uhf_id))
    return found


def _missing_report(classified):
    rows = []
    for record in classified.to_dict(orient="records"):
        rows.append(
            {
                "period": record["period"],
                "uhf42_id": record["uhf42_id"],
                "missing_rate": bool(record["missing_rate"]),
                "missing_traffic": bool(record["missing_traffic"]),
                "exclusion_reason": record["exclusion_reason"],
            }
        )
    return pd.DataFrame(rows, columns=list(_MISSING_COLUMNS))


def _exclusions(classified):
    rows = []
    for record in classified.to_dict(orient="records"):
        if record["coverage_status"] == "valid":
            continue
        rows.append(
            {
                "stage": "merge",
                "key": f"{record['uhf42_id']}|{record['period']}",
                "reason": record["exclusion_reason"],
                "source": record["traffic_source"],
            }
        )
    return pd.DataFrame(rows, columns=list(_EXCLUSION_COLUMNS))


def _identity(value):
    if _missing(value):
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if math.isfinite(number) and number.is_integer():
            return str(int(number))
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _label(value):
    if _missing(value):
        return ""
    if isinstance(value, numbers.Integral):
        return str(int(value))
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _text(value):
    if _missing(value):
        return None
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "none", "<na>"}:
        return None
    return text


def _number(value):
    if isinstance(value, bool) or _missing(value):
        return None
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    text = str(value).strip()
    if text == "" or text.casefold() in {"nan", "none", "<na>"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def _whole(value):
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return value


def _missing(value):
    if value is None:
        return True
    if isinstance(value, str):
        return False
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(missing)
