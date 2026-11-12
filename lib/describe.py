"""Summarize the merged neighborhood sample and choose the primary period."""

import math
import re

import pandas as pd

from lib.config import load_config

YEAR_COLUMNS = (
    "period",
    "variable",
    "n",
    "mean",
    "median",
    "std",
    "min",
    "max",
    "range",
    "q1",
    "q3",
    "iqr",
    "n_missing",
    "pct_missing",
)
BOROUGH_COLUMNS = ("period", "borough", "coverage_status", "n", "pct")
VARIABLES = (
    "asthma_ed_rate_per_10000",
    "traffic_mean_vehicles_per_hour",
    "n_road_segments",
)
USABLE_PERIOD = "usable"
_YEAR = re.compile(r"\d{4}")


class DescribeError(Exception):
    def __init__(self, message, exit_code=2):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def primary_period(merged, selection):
    """Preference year when it is included and usable; else the latest qualifier.

    Usable n is the count of valid neighborhood rows. The choice reads the
    config threshold and the selection list. It does not read a p-value column.
    """

    config = load_config()
    preference = str(config["years"]["primary_preference"]).strip()
    threshold = int(config["thresholds"]["min_uhf_per_period"])
    labels = _included_labels(selection)
    qualifying = [label for label in labels if _usable_count(merged, label) >= threshold]
    if preference in qualifying:
        return preference
    if not qualifying:
        return ""
    return max(qualifying, key=_recency)


def descriptive_tables(merged):
    """Return period summaries and borough frequencies for the merged sample."""

    frame = _as_frame(merged)
    year_rows = []
    borough_rows = []
    periods = list(dict.fromkeys(frame["period"].map(_label))) if "period" in frame.columns else []
    for period in periods:
        subset = _period_rows(frame, period)
        for variable in VARIABLES:
            year_rows.append(_summarize(subset, period, variable))
        borough_rows.extend(_borough_counts(subset, period))
    usable = frame.loc[_valid_mask(frame)].copy() if len(frame) else frame
    for variable in VARIABLES:
        year_rows.append(_summarize(usable, USABLE_PERIOD, variable))
    borough_rows.extend(_borough_counts(usable, USABLE_PERIOD))
    by_year = pd.DataFrame(year_rows, columns=YEAR_COLUMNS)
    by_borough = pd.DataFrame(borough_rows, columns=BOROUGH_COLUMNS)
    return by_year, by_borough


def distribution_subtitle(frame, period, variable):
    """Subtitle for S1 or S2: n, mean, median, and sample standard deviation."""

    subset = _period_rows(_as_frame(frame), period)
    observed = _observed(subset, variable)
    count = int(observed.shape[0])
    if count == 0:
        return "n=0, mean=none, median=none, sd=none"
    mean = float(observed.mean())
    median = float(observed.median())
    deviation = float(observed.std(ddof=1)) if count > 1 else 0.0
    return (
        f"n={count}, mean={_format_stat(mean)}, "
        f"median={_format_stat(median)}, sd={_format_stat(deviation)}"
    )


def _as_frame(merged):
    if isinstance(merged, pd.DataFrame):
        return merged.copy()
    return pd.DataFrame(merged)


def _included_labels(selection):
    if isinstance(selection, dict):
        items = selection.get("periods", [])
    else:
        items = selection or []
    labels = []
    for item in items:
        if isinstance(item, dict):
            if not _flag(item.get("included", True)):
                continue
            labels.append(_label(item.get("period", "")))
        else:
            labels.append(_label(item))
    return labels


def _flag(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _label(value):
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _period_rows(frame, period):
    if frame.empty or "period" not in frame.columns:
        return frame.iloc[0:0]
    return frame.loc[frame["period"].map(_label) == _label(period)].copy()


def _valid_mask(frame):
    if frame.empty:
        return pd.Series(dtype=bool)
    if "coverage_status" in frame.columns:
        return frame["coverage_status"].map(_label).str.lower().eq("valid")
    rate = _observed_mask(frame, "asthma_ed_rate_per_10000")
    traffic = _observed_mask(frame, "traffic_mean_vehicles_per_hour")
    return rate & traffic


def _usable_count(merged, period):
    rows = _period_rows(_as_frame(merged), period)
    if rows.empty:
        return 0
    return int(_valid_mask(rows).sum())


def _recency(period):
    years = [int(token) for token in _YEAR.findall(_label(period))]
    return (max(years) if years else -1, _label(period))


def _numeric(series):
    return pd.to_numeric(series, errors="coerce")


def _observed(frame, variable):
    if frame.empty or variable not in frame.columns:
        return pd.Series(dtype=float)
    return _numeric(frame[variable]).dropna()


def _observed_mask(frame, variable):
    if variable not in frame.columns:
        return pd.Series(False, index=frame.index)
    return _numeric(frame[variable]).notna()


def _format_stat(value):
    if value is None:
        return "none"
    number = float(value)
    if math.isnan(number):
        return "none"
    if number.is_integer():
        return str(int(number))
    return format(number, "f").rstrip("0").rstrip(".")


def _summarize(frame, period, variable):
    if frame.empty or variable not in frame.columns:
        values = pd.Series(dtype=float)
    else:
        values = _numeric(frame[variable])
    observed = values.dropna()
    count = int(observed.shape[0])
    missing = int(values.shape[0] - count)
    denominator = count + missing
    summary = {
        "period": _label(period),
        "variable": variable,
        "n": count,
        "mean": None,
        "median": None,
        "std": None,
        "min": None,
        "max": None,
        "range": None,
        "q1": None,
        "q3": None,
        "iqr": None,
        "n_missing": missing,
        "pct_missing": (100.0 * missing / denominator) if denominator else 0.0,
    }
    if count == 0:
        return summary
    mean = float(observed.mean())
    median = float(observed.median())
    deviation = float(observed.std(ddof=1)) if count > 1 else 0.0
    low = float(observed.min())
    high = float(observed.max())
    q1 = float(observed.quantile(0.25))
    q3 = float(observed.quantile(0.75))
    summary.update(
        {
            "mean": mean,
            "median": median,
            "std": deviation,
            "min": low,
            "max": high,
            "range": high - low,
            "q1": q1,
            "q3": q3,
            "iqr": q3 - q1,
        }
    )
    return summary


def _borough_counts(frame, period):
    rows = []
    total = int(len(frame))
    if total == 0 or "borough" not in frame.columns:
        return rows
    status_column = (
        frame["coverage_status"].map(_label)
        if "coverage_status" in frame.columns
        else pd.Series("", index=frame.index)
    )
    grouped = (
        pd.DataFrame(
            {
                "borough": frame["borough"].map(_label),
                "coverage_status": status_column,
            }
        )
        .groupby(["borough", "coverage_status"], dropna=False)
        .size()
    )
    for (borough, status), count in grouped.items():
        rows.append(
            {
                "period": _label(period),
                "borough": borough,
                "coverage_status": status,
                "n": int(count),
                "pct": 100.0 * int(count) / total,
            }
        )
    return rows
