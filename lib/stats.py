"""Test the neighborhood correlation and compare included periods."""

import math
import re

import numpy as np
import pandas as pd
from scipy import stats

from lib.config import load_config

PEARSON_COLUMNS = (
    "period",
    "role",
    "n",
    "r",
    "t",
    "df",
    "p_raw",
    "p_holm",
    "ci_low",
    "ci_high",
    "alpha",
    "p_below_alpha",
    "sign",
    "primary_reason",
    "test_status",
    "skip_reason",
    "traffic_source",
)
SPEARMAN_COLUMNS = ("period", "n", "rho", "p_raw", "test_status", "skip_reason")
SENSITIVITY_COLUMNS = ("analysis_id", "period", "n", "r", "p_raw", "note")
COMPARISON_COLUMNS = (
    "period",
    "n",
    "r",
    "p_raw",
    "p_holm",
    "ci_low",
    "ci_high",
    "sign",
    "traffic_source",
    "n_segments_median",
    "measurement_consistency",
)


class StatsError(Exception):
    def __init__(self, message, exit_code=2):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def primary_period(merged, selection):
    """Return the preference period when it qualifies, otherwise the latest qualifier.

    The choice uses usable neighborhood counts. It does not read a p-value column.
    """

    config = load_config()
    preference = str(config["years"]["primary_preference"]).strip()
    threshold = int(config["thresholds"]["min_uhf_per_period"])
    qualifying = [
        _period_label(item)
        for item in _included_items(selection)
        if _valid_count(merged, _period_label(item)) >= threshold
    ]
    if preference in qualifying:
        return preference, "primary_preference"
    if not qualifying:
        return "", ""
    return max(qualifying, key=_recency), "most_recent_included"


def pearson_by_period(merged, selection):
    """One two-sided Pearson row per included period. Holm adjusts secondary rows only."""

    config = load_config()
    alpha = float(config["stats"]["alpha"])
    threshold = int(config["thresholds"]["min_uhf_per_period"])
    chosen, reason = primary_period(merged, selection)
    rows = []
    for item in _included_items(selection):
        period = _period_label(item)
        role = "primary" if chosen and period == chosen else "secondary"
        rows.append(_pearson_row(merged, period, role, reason, alpha, threshold))
    frame = pd.DataFrame(rows, columns=PEARSON_COLUMNS)
    _apply_holm(frame)
    return frame


def spearman_by_period(merged, selection):
    """Repeat the included periods with Spearman's rank correlation."""

    config = load_config()
    threshold = int(config["thresholds"]["min_uhf_per_period"])
    rows = []
    for item in _included_items(selection):
        period = _period_label(item)
        rows.append(_spearman_row(merged, period, threshold))
    return pd.DataFrame(rows, columns=SPEARMAN_COLUMNS)


def sensitivity(merged, locations, selection):
    """Rerun Pearson for leave-one-out, thresholds, intersections, and paired bootstrap."""

    config = load_config()
    chosen, _reason = primary_period(merged, selection)
    if not chosen:
        return pd.DataFrame(columns=SENSITIVITY_COLUMNS)
    rows = []
    rows.extend(_leave_one_out(merged, chosen))
    rows.extend(_threshold_grid(merged, locations, selection, chosen, config))
    rows.append(_neighborhood_intersection(merged, selection, chosen))
    rows.append(_segment_intersection(merged, locations, selection, chosen, config))
    if bool(config["stats"]["bootstrap_comparisons"]):
        rows.extend(_bootstrap_rows(merged, selection, chosen))
    return pd.DataFrame(rows, columns=SENSITIVITY_COLUMNS)


def compare_periods(merged, pearson, selection):
    """One comparison row per included period. Neighborhood-years are not pooled."""

    chosen = ""
    if "role" in pearson.columns:
        primary_rows = pearson[pearson["role"].map(_label) == "primary"]
        if len(primary_rows):
            chosen = _label(primary_rows.iloc[0]["period"])
    signs = [
        _label(row["sign"])
        for row in pearson.to_dict(orient="records")
        if _label(row.get("test_status")) == "ok" and _label(row.get("sign"))
    ]
    consistency = "consistent_sign" if signs and len(set(signs)) == 1 else "mixed_sign"
    included = {_period_label(item) for item in _included_items(selection)}
    rows = []
    for record in pearson.to_dict(orient="records"):
        period = _label(record.get("period"))
        if included and period not in included:
            continue
        rows.append(_comparison_row(merged, record, chosen, consistency))
    return pd.DataFrame(rows, columns=COMPARISON_COLUMNS)


def _pearson_row(merged, period, role, reason, alpha, threshold):
    valid_count = _valid_count(merged, period)
    source = _traffic_source(merged, period)
    if valid_count < threshold:
        return _skipped_pearson(
            period, role, reason, valid_count, alpha, source, "below_min_uhf"
        )
    pairs = _analysis_pairs(merged, period)
    count = len(pairs)
    if count < 3:
        return _skipped_pearson(period, role, reason, count, alpha, source, "n_below_3")
    x_values = [pair[1] for pair in pairs]
    y_values = [pair[2] for pair in pairs]
    if _zero_variance(x_values) or _zero_variance(y_values):
        return _skipped_pearson(
            period, role, reason, count, alpha, source, "zero_variance"
        )
    result = stats.pearsonr(x_values, y_values, alternative="two-sided")
    correlation = float(result.statistic)
    if not math.isfinite(correlation) or math.isclose(abs(correlation), 1.0, rel_tol=0.0, abs_tol=1e-8):
        return _skipped_pearson(
            period, role, reason, count, alpha, source, "perfect_correlation"
        )
    formula_t = _formula_t(correlation, count)
    fit = stats.linregress(x_values, y_values)
    scipy_t = float(fit.slope / fit.stderr)
    gap = abs(formula_t - scipy_t) / abs(scipy_t) if scipy_t else abs(formula_t - scipy_t)
    if gap > 1e-4:
        raise StatsError(f"{period} formula t {formula_t} scipy t {scipy_t}", 2)
    interval = result.confidence_interval(confidence_level=0.95)
    p_value = float(result.pvalue)
    return {
        "period": period,
        "role": role,
        "n": count,
        "r": correlation,
        "t": formula_t,
        "df": count - 2,
        "p_raw": p_value,
        "p_holm": None,
        "ci_low": float(interval.low),
        "ci_high": float(interval.high),
        "alpha": alpha,
        "p_below_alpha": bool(p_value < alpha),
        "sign": _sign(correlation),
        "primary_reason": reason,
        "test_status": "ok",
        "skip_reason": "",
        "traffic_source": source,
    }


def _skipped_pearson(period, role, reason, count, alpha, source, skip_reason):
    return {
        "period": period,
        "role": role,
        "n": count,
        "r": None,
        "t": None,
        "df": None,
        "p_raw": None,
        "p_holm": None,
        "ci_low": None,
        "ci_high": None,
        "alpha": alpha,
        "p_below_alpha": False,
        "sign": "",
        "primary_reason": reason,
        "test_status": "skipped",
        "skip_reason": skip_reason,
        "traffic_source": source,
    }


def _apply_holm(frame):
    if frame.empty:
        return
    mask = (frame["role"].map(_label) == "secondary") & (frame["test_status"].map(_label) == "ok")
    indexes = [index for index in frame.index if bool(mask.loc[index])]
    if not indexes:
        return
    adjusted = _holm([float(frame.at[index, "p_raw"]) for index in indexes])
    for index, value in zip(indexes, adjusted):
        frame.at[index, "p_holm"] = float(value)


def _holm(p_values):
    array = np.asarray(p_values, dtype=float)
    count = int(array.size)
    order = np.argsort(array, kind="mergesort")
    adjusted = np.empty(count, dtype=float)
    running = 0.0
    for rank, index in enumerate(order):
        value = min(1.0, (count - rank) * float(array[index]))
        running = max(running, value)
        adjusted[index] = running
    return adjusted


def _spearman_row(merged, period, threshold):
    if _valid_count(merged, period) < threshold:
        return _skipped_spearman(period, _valid_count(merged, period), "below_min_uhf")
    pairs = _analysis_pairs(merged, period)
    count = len(pairs)
    x_values = [pair[1] for pair in pairs]
    y_values = [pair[2] for pair in pairs]
    if count < 3:
        return _skipped_spearman(period, count, "n_below_3")
    if _zero_variance(x_values) or _zero_variance(y_values):
        return _skipped_spearman(period, count, "zero_variance")
    result = stats.spearmanr(x_values, y_values)
    return {
        "period": period,
        "n": count,
        "rho": float(result.statistic),
        "p_raw": float(result.pvalue),
        "test_status": "ok",
        "skip_reason": "",
    }


def _skipped_spearman(period, count, skip_reason):
    return {
        "period": period,
        "n": count,
        "rho": None,
        "p_raw": None,
        "test_status": "skipped",
        "skip_reason": skip_reason,
    }


def _leave_one_out(merged, period):
    pairs = _analysis_pairs(merged, period)
    rows = []
    for index, (uhf, _x_value, _y_value) in enumerate(pairs):
        kept = pairs[:index] + pairs[index + 1 :]
        correlation, p_value, count = _correlate(kept)
        rows.append(
            {
                "analysis_id": f"loo_{uhf}",
                "period": period,
                "n": count,
                "r": correlation,
                "p_raw": p_value,
                "note": uhf,
            }
        )
    return rows


def _threshold_grid(merged, locations, selection, period, config):
    years = _years_for(selection, period)
    rows = []
    for segments in config["thresholds"]["sensitivity_min_segments"]:
        for hours in config["thresholds"]["sensitivity_min_hours"]:
            means = _neighborhood_means(
                locations,
                years,
                int(segments),
                int(hours),
                minimum_days=int(config["thresholds"]["min_observation_dates"]),
            )
            correlation, p_value, count = _correlate_means(merged, period, means)
            rows.append(
                {
                    "analysis_id": f"segments_{int(segments)}_hours_{int(hours)}",
                    "period": period,
                    "n": count,
                    "r": correlation,
                    "p_raw": p_value,
                    "note": "",
                }
            )
    return rows


def _neighborhood_intersection(merged, selection, period):
    sets = [_valid_ids(merged, _period_label(item)) for item in _included_items(selection)]
    shared = set.intersection(*sets) if sets else set()
    pairs = [pair for pair in _analysis_pairs(merged, period) if pair[0] in shared]
    correlation, p_value, count = _correlate(pairs)
    return {
        "analysis_id": "neighborhood_intersection",
        "period": period,
        "n": count,
        "r": correlation,
        "p_raw": p_value,
        "note": "",
    }


def _segment_intersection(merged, locations, selection, period, config):
    minimum_hours = int(config["thresholds"]["min_complete_hours"])
    minimum_days = int(config["thresholds"]["min_observation_dates"])
    minimum_segments = int(config["thresholds"]["min_segments_per_uhf"])
    sets = []
    for item in _included_items(selection):
        for year in item.get("years") or []:
            rows = _eligible_locations(locations, minimum_hours, minimum_days)
            rows = rows[pd.to_numeric(rows["year"], errors="coerce") == int(year)]
            sets.append(set(rows["road_segment_id"].map(_label)))
    shared = set.intersection(*sets) if sets else set()
    means = _neighborhood_means(
        locations,
        _years_for(selection, period),
        minimum_segments,
        minimum_hours,
        segment_ids=shared,
        minimum_days=minimum_days,
    )
    correlation, p_value, count = _correlate_means(merged, period, means)
    return {
        "analysis_id": "segment_intersection",
        "period": period,
        "n": count,
        "r": correlation,
        "p_raw": p_value,
        "note": "",
    }


def _bootstrap_rows(merged, selection, period):
    primary_ids = _valid_ids(merged, period)
    generator = np.random.default_rng(0)
    rows = []
    for item in _included_items(selection):
        label = _period_label(item)
        if label == period:
            continue
        shared = sorted(primary_ids & _valid_ids(merged, label), key=_uhf_sort)
        count = len(shared)
        if count == 0:
            correlation, p_value = None, None
        else:
            drawn = generator.choice(shared, size=count, replace=True)
            means = {uhf: _traffic_for(merged, period, uhf) for uhf in drawn}
            pairs = []
            for uhf in drawn:
                rate = _rate_for(merged, period, uhf)
                traffic = means.get(uhf)
                if traffic is None or rate is None:
                    continue
                pairs.append((uhf, traffic, rate))
            correlation, p_value, _count = _correlate(pairs)
        rows.append(
            {
                "analysis_id": f"bootstrap_{label}",
                "period": period,
                "n": count,
                "r": correlation,
                "p_raw": p_value,
                "note": "paired neighborhoods",
            }
        )
    return rows


def _comparison_row(merged, record, chosen, consistency):
    period = _label(record.get("period"))
    overlap = len(_valid_ids(merged, period) & _valid_ids(merged, chosen)) if chosen else 0
    if period == chosen:
        overlap = len(_valid_ids(merged, period))
    low = _float(record.get("ci_low"))
    high = _float(record.get("ci_high"))
    contains = low is not None and high is not None and low <= 0 <= high
    sign = _label(record.get("sign"))
    magnitude = record.get("r")
    magnitude_text = "" if _missing(magnitude) else format(float(magnitude), ".16g")
    text = (
        f"direction={sign}; magnitude={magnitude_text}; "
        f"contains_0={'true' if contains else 'false'}; "
        f"overlap={overlap}; {consistency}"
    )
    return {
        "period": period,
        "n": record.get("n"),
        "r": record.get("r"),
        "p_raw": record.get("p_raw"),
        "p_holm": record.get("p_holm"),
        "ci_low": record.get("ci_low"),
        "ci_high": record.get("ci_high"),
        "sign": sign,
        "traffic_source": _label(record.get("traffic_source")),
        "n_segments_median": _segment_median(merged, period),
        "measurement_consistency": text,
    }


def _correlate(pairs):
    count = len(pairs)
    if count < 3:
        return None, None, count
    x_values = [pair[1] for pair in pairs]
    y_values = [pair[2] for pair in pairs]
    if _zero_variance(x_values) or _zero_variance(y_values):
        return None, None, count
    result = stats.pearsonr(x_values, y_values, alternative="two-sided")
    correlation = float(result.statistic)
    if not math.isfinite(correlation) or math.isclose(abs(correlation), 1.0, rel_tol=0.0, abs_tol=1e-8):
        return None, None, count
    return correlation, float(result.pvalue), count


def _correlate_means(merged, period, means):
    pairs = []
    for uhf, _traffic, rate in _analysis_pairs(merged, period):
        if uhf in means:
            pairs.append((uhf, float(means[uhf]), rate))
    return _correlate(pairs)


def _neighborhood_means(frame, years, min_segments, min_hours, segment_ids=None, minimum_days=2):
    rows = _eligible_locations(frame, min_hours, minimum_days)
    if segment_ids is not None:
        rows = rows[rows["road_segment_id"].map(_label).isin(segment_ids)]
    means = {}
    if rows.empty or "uhf42_id" not in rows.columns:
        return means
    uhfs = sorted(set(rows["uhf42_id"].map(_uhf)), key=_uhf_sort)
    for uhf in uhfs:
        if not uhf:
            continue
        year_means = []
        complete = True
        for year in years:
            part = rows[
                (rows["uhf42_id"].map(_uhf) == uhf)
                & (pd.to_numeric(rows["year"], errors="coerce") == int(year))
            ]
            if part.empty:
                complete = False
                break
            segment_means = part.groupby(part["road_segment_id"].map(_label))["mean_hourly"].mean()
            if len(segment_means) < min_segments:
                complete = False
                break
            year_means.append(float(segment_means.mean()))
        if complete and year_means:
            means[uhf] = sum(year_means) / len(year_means)
    return means


def _eligible_locations(frame, min_hours, minimum_days):
    if frame is None or len(frame) == 0:
        return pd.DataFrame(columns=getattr(frame, "columns", []))
    hours = pd.to_numeric(frame["n_complete_hours"], errors="coerce")
    days = pd.to_numeric(frame["n_days"], errors="coerce")
    eligible = frame["eligible"].map(_eligible_flag)
    return frame.loc[(hours >= min_hours) & (days >= minimum_days) & eligible].copy()


def _analysis_pairs(merged, period):
    pairs = []
    for record in _valid_records(merged, period):
        traffic = _float(record.get("traffic_mean_vehicles_per_hour"))
        rate = _float(record.get("asthma_ed_rate_per_10000"))
        uhf = _uhf(record.get("uhf42_id"))
        if uhf and traffic is not None and rate is not None:
            pairs.append((uhf, traffic, rate))
    return pairs


def _valid_records(merged, period):
    frame = _as_frame(merged)
    if frame.empty or "period" not in frame.columns:
        return []
    rows = frame[frame["period"].map(_label) == _label(period)]
    if "coverage_status" in rows.columns:
        rows = rows[rows["coverage_status"].map(_label).str.lower() == "valid"]
    return rows.to_dict(orient="records")


def _valid_count(merged, period):
    return len(_valid_records(merged, period))


def _valid_ids(merged, period):
    return {_uhf(record.get("uhf42_id")) for record in _valid_records(merged, period)}


def _traffic_for(merged, period, uhf):
    for record in _valid_records(merged, period):
        if _uhf(record.get("uhf42_id")) == uhf:
            return _float(record.get("traffic_mean_vehicles_per_hour"))
    return None


def _rate_for(merged, period, uhf):
    for record in _valid_records(merged, period):
        if _uhf(record.get("uhf42_id")) == uhf:
            return _float(record.get("asthma_ed_rate_per_10000"))
    return None


def _traffic_source(merged, period):
    for record in _valid_records(merged, period):
        source = _label(record.get("traffic_source"))
        if source:
            return source
    return ""


def _segment_median(merged, period):
    values = []
    for record in _valid_records(merged, period):
        number = _float(record.get("n_road_segments"))
        if number is not None:
            values.append(number)
    if not values:
        return None
    return float(np.median(np.asarray(values, dtype=float)))


def _years_for(selection, period):
    for item in _included_items(selection):
        if _period_label(item) == _label(period):
            return [int(year) for year in item.get("years") or []]
    return [int(token) for token in _years(period)]


def _included_items(selection):
    if isinstance(selection, dict):
        items = selection.get("periods", [])
    else:
        items = selection or []
    included = []
    for item in items:
        if isinstance(item, dict) and _flag(item.get("included", True)):
            included.append(item)
    return included


def _period_label(item):
    if isinstance(item, dict):
        return _label(item.get("period"))
    return _label(item)


def _recency(period):
    years = _years(period)
    return (max(years) if years else -1, _label(period))


def _years(period):
    return [int(token) for token in re.findall(r"\d{4}", _label(period))]


def _formula_t(correlation, count):
    return correlation * math.sqrt((count - 2) / (1 - correlation * correlation))


def _zero_variance(values):
    array = np.asarray(values, dtype=float)
    return array.size < 2 or float(np.std(array)) == 0.0


def _sign(correlation):
    if correlation > 0:
        return "positive"
    if correlation < 0:
        return "negative"
    return "zero"


def _as_frame(merged):
    if isinstance(merged, pd.DataFrame):
        return merged.copy()
    return pd.DataFrame(merged)


def _flag(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _eligible_flag(value):
    if isinstance(value, str):
        return False
    try:
        return bool(value) is True
    except (TypeError, ValueError):
        return False


def _label(value):
    if _missing(value):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if math.isfinite(number) and number.is_integer():
            return str(int(number))
        return str(value).strip()
    return str(value).strip()


def _uhf(value):
    return _label(value)


def _uhf_sort(value):
    text = _label(value)
    try:
        return (0, int(text), text)
    except ValueError:
        return (1, 0, text)


def _float(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _missing(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
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
