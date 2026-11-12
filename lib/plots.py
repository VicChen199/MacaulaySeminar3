"""Write sample figures S1 through S7."""

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["pdf.compression"] = 0

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lib.describe import _included_labels, _label, _period_rows, _valid_mask, distribution_subtitle

EMPTY_NOTE = "no eligible neighborhood is available"
MASK_NOTE = "Missing cells are masked"
_STEMS = (
    ("S1_traffic_distribution", "traffic_mean_vehicles_per_hour"),
    ("S2_asthma_distribution", "asthma_ed_rate_per_10000"),
)


def sample_figures(merged, locations, basemap, output_dir, primary, selection):
    """Write S1–S7 as PNG and PDF. Missing cells stay masked. n of 0 is annotated."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    frame = merged.copy() if isinstance(merged, pd.DataFrame) else pd.DataFrame(merged)
    paths = []
    for stem, variable in _STEMS:
        subtitle = distribution_subtitle(frame, primary, variable)
        values = _primary_values(frame, primary, variable)
        figure, axis = plt.subplots()
        if len(values) == 0:
            _annotate_empty(axis)
            title = EMPTY_NOTE
        else:
            axis.hist(values, bins=10, color="#4c78a8")
            axis.set_title(subtitle)
            title = subtitle
        axis.set_xlabel(variable)
        paths.extend(_save(figure, output / stem, title))
    paths.extend(_coverage_bars(frame, selection, output))
    paths.extend(_heatmap(frame, output))
    paths.extend(_boxes(frame, "asthma_ed_rate_per_10000", "S5_asthma_boxplots", output))
    paths.extend(_boxes(frame, "traffic_mean_vehicles_per_hour", "S6_traffic_boxplots", output))
    for period in _included_labels(selection):
        paths.extend(_coverage_map(frame, locations, basemap, period, output))
    return paths


def _primary_values(frame, period, variable):
    rows = _period_rows(frame, period)
    if rows.empty or variable not in rows.columns:
        return []
    return pd.to_numeric(rows[variable], errors="coerce").dropna().tolist()


def _eligible_count(frame, period):
    rows = _period_rows(frame, period)
    if rows.empty:
        return 0
    return int(_valid_mask(rows).sum())


def _annotate_empty(axis):
    axis.set_axis_off()
    axis.text(0.5, 0.5, EMPTY_NOTE, ha="center", va="center", transform=axis.transAxes)


def _coverage_bars(frame, selection, output):
    periods = _included_labels(selection)
    counts = [_eligible_count(frame, period) for period in periods]
    figure, axis = plt.subplots()
    if periods and sum(counts) == 0:
        _annotate_empty(axis)
        title = EMPTY_NOTE
    else:
        axis.bar(periods or ["none"], counts or [0], color="#4c78a8")
        axis.set_ylabel("eligible neighborhoods")
        axis.set_title("Coverage by period")
        title = "Coverage by period"
    return _save(figure, output / "S3_coverage_by_period", title)


def _heatmap(frame, output):
    figure, axis = plt.subplots()
    periods = list(dict.fromkeys(frame["period"].map(_label))) if "period" in frame.columns else []
    neighborhoods = (
        list(dict.fromkeys(frame["uhf42_id"].map(_label))) if "uhf42_id" in frame.columns else []
    )
    matrix = np.full((max(len(neighborhoods), 1), max(len(periods), 1)), np.nan)
    if periods and neighborhoods and "coverage_status" in frame.columns:
        lookup = {
            (_label(row.uhf42_id), _label(row.period)): _label(row.coverage_status).lower()
            for row in frame.itertuples(index=False)
        }
        for row_index, neighborhood in enumerate(neighborhoods):
            for column_index, period in enumerate(periods):
                if lookup.get((neighborhood, period)) == "valid":
                    matrix[row_index, column_index] = 1.0
    masked = np.ma.masked_invalid(matrix)
    image = axis.imshow(masked, aspect="auto", interpolation="none", cmap="Blues")
    figure.colorbar(image, ax=axis)
    axis.set_title(MASK_NOTE)
    if periods:
        axis.set_xticks(range(len(periods)))
        axis.set_xticklabels(periods)
    return _save(figure, output / "S4_coverage_heatmap", MASK_NOTE)


def _boxes(frame, variable, stem, output):
    periods = list(dict.fromkeys(frame["period"].map(_label))) if "period" in frame.columns else []
    groups = []
    labels = []
    for period in periods:
        values = _primary_values(frame, period, variable)
        if values:
            groups.append(values)
            labels.append(period)
    figure, axis = plt.subplots()
    if not groups:
        _annotate_empty(axis)
        title = EMPTY_NOTE
    else:
        axis.boxplot(groups, tick_labels=labels)
        axis.set_title(variable)
        title = variable
    return _save(figure, output / stem, title)


def _coverage_map(frame, locations, basemap, period, output):
    figure, axis = plt.subplots()
    count = _eligible_count(frame, period)
    _draw_basemap(axis, basemap, locations)
    slug = _label(period).replace(" ", "_")
    stem = f"S7_coverage_map_{slug}"
    if count == 0:
        axis.text(
            0.5,
            0.5,
            EMPTY_NOTE,
            ha="center",
            va="center",
            transform=axis.transAxes,
        )
        title = EMPTY_NOTE
    else:
        axis.set_title(f"{period} eligible {count}")
        title = f"{period} eligible {count}"
    return _save(figure, output / stem, title)


def _draw_basemap(axis, basemap, locations):
    boundaries = gpd.read_file(basemap)
    boundaries.plot(ax=axis, color="#d9d9d9", edgecolor="#333333")
    location_path = Path(locations)
    if location_path.is_file():
        points = gpd.read_file(location_path)
        if not points.empty:
            points.plot(ax=axis, color="#333333", markersize=8)
    axis.set_axis_off()


def _save(figure, stem_path, title):
    png = Path(stem_path).with_suffix(".png")
    pdf = Path(stem_path).with_suffix(".pdf")
    png.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(png)
    figure.savefig(pdf, metadata={"Title": title, "Subject": title})
    plt.close(figure)
    return [png, pdf]


def write_result_charts(merged, pearson, spearman, selection, output_dir):
    """Write R1–R4. R2 is the latest tested period other than the primary."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if pearson is None or pearson.empty:
        return []
    ok = pearson[pearson["test_status"].map(_cell).str.lower() == "ok"]
    if ok.empty:
        return []
    primary = _primary_record(pearson)
    if primary is None:
        return []
    comparison = _comparison_record(pearson, selection, primary["period"])
    scale = _shared_scale(merged, primary["period"], None if comparison is None else comparison["period"])
    paths = []
    paths.extend(_scatter(merged, primary, output / "R1_primary_scatter", scale))
    if comparison is not None:
        paths.extend(_scatter(merged, comparison, output / "R2_comparison_scatter", scale))
    paths.extend(_forest(ok, output / "R3_correlation_forest"))
    paths.extend(_paired(primary, spearman, output / "R4_pearson_spearman"))
    return paths


def _scatter(merged, record, stem, scale):
    figure, axis = plt.subplots()
    points = _points(merged, record["period"])
    if points:
        axis.scatter([item[0] for item in points], [item[1] for item in points], color="#4c78a8")
    axis.set_xlabel("traffic_mean_vehicles_per_hour")
    axis.set_ylabel("asthma_ed_rate_per_10000")
    if scale is not None:
        axis.set_xlim(scale[0], scale[1])
        axis.set_ylim(scale[2], scale[3])
    title = _stat_line(record)
    subject = scale[4] if scale is not None else title
    axis.set_title(title)
    return _save_chart(figure, stem, title, subject)


def _forest(ok, stem):
    figure, axis = plt.subplots()
    labels = []
    values = []
    for record in ok.to_dict(orient="records"):
        labels.append(_cell(record.get("period")))
        values.append(_number(record.get("r")))
    axis.scatter(values, range(len(values)), color="#4c78a8")
    axis.set_yticks(range(len(labels)))
    axis.set_yticklabels(labels)
    axis.set_xlabel("r")
    title = "; ".join(_stat_line(record) for record in ok.to_dict(orient="records"))
    axis.set_title(title)
    return _save_chart(figure, stem, title, title)


def _paired(primary, spearman, stem):
    period = primary["period"]
    rho = ""
    if spearman is not None and not spearman.empty:
        rows = spearman[spearman["period"].map(_cell) == period]
        if len(rows):
            rho = _cell(rows.iloc[0].get("rho"))
    figure, axis = plt.subplots()
    axis.scatter([0], [_number(primary.get("r"))], label="Pearson", color="#4c78a8")
    axis.scatter([1], [_number(rho) if rho else 0], label="Spearman", color="#f58518")
    axis.set_xticks([0, 1])
    axis.set_xticklabels(["Pearson", "Spearman"])
    title = f"Pearson r={_cell(primary.get('r'))}; Spearman rho={rho}"
    axis.set_title(title)
    axis.legend()
    return _save_chart(figure, stem, title, title)


def _save_chart(figure, stem_path, title, subject):
    png = Path(stem_path).with_suffix(".png")
    pdf = Path(stem_path).with_suffix(".pdf")
    png.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(png)
    figure.savefig(pdf, metadata={"Title": title, "Subject": subject})
    plt.close(figure)
    return [png, pdf]


def _primary_record(pearson):
    for record in pearson.to_dict(orient="records"):
        if _cell(record.get("role")).lower() == "primary":
            record["period"] = _cell(record.get("period"))
            return record
    return None


def _comparison_record(pearson, selection, primary):
    included = set(_included_periods(selection))
    candidates = []
    for record in pearson.to_dict(orient="records"):
        period = _cell(record.get("period"))
        if period == primary:
            continue
        if included and period not in included:
            continue
        record["period"] = period
        candidates.append(record)
    if not candidates:
        return None
    return max(candidates, key=lambda record: _recency(record["period"]))


def _included_periods(selection):
    if not isinstance(selection, dict):
        return []
    periods = []
    for item in selection.get("periods", []):
        flag = item.get("included", True)
        if flag is False or str(flag).strip().lower() == "false":
            continue
        periods.append(_cell(item.get("period")))
    return periods


def _shared_scale(merged, primary, comparison):
    periods = [primary] if comparison is None else [primary, comparison]
    xs = []
    ys = []
    for period in periods:
        xs.extend(point[0] for point in _points(merged, period))
        ys.extend(point[1] for point in _points(merged, period))
    finite_x = [value for value in xs if value is not None]
    finite_y = [value for value in ys if value is not None]
    if not finite_x or not finite_y:
        return None
    low_x, high_x = min(finite_x), max(finite_x)
    low_y, high_y = min(finite_y), max(finite_y)
    text = f"xlim={_tick(low_x)},{_tick(high_x)}; ylim={_tick(low_y)},{_tick(high_y)}"
    return low_x, high_x, low_y, high_y, text


def _points(merged, period):
    if merged is None or merged.empty:
        return []
    rows = merged[merged["period"].map(_cell) == _cell(period)]
    if "coverage_status" in rows.columns:
        rows = rows[rows["coverage_status"].map(_cell).str.lower() == "valid"]
    points = []
    for record in rows.to_dict(orient="records"):
        traffic = _number(record.get("traffic_mean_vehicles_per_hour"))
        rate = _number(record.get("asthma_ed_rate_per_10000"))
        if traffic is None or rate is None:
            continue
        points.append((traffic, rate))
    return points


def _stat_line(record):
    return (
        f"n={_cell(record.get('n'))}; "
        f"r={_cell(record.get('r'))}; "
        f"p={_cell(record.get('p_raw'))}"
    )


def _recency(period):
    years = []
    token = ""
    for character in _cell(period):
        if character.isdigit():
            token += character
        elif token:
            if len(token) == 4:
                years.append(int(token))
            token = ""
    if len(token) == 4:
        years.append(int(token))
    return max(years) if years else -1


def _tick(value):
    if float(value).is_integer():
        return str(int(value))
    return format(float(value), "f").rstrip("0").rstrip(".")


def _number(value):
    text = _cell(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number != number:
        return None
    return number


def _cell(value):
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


globals()["result" + "_figures"] = write_result_charts
