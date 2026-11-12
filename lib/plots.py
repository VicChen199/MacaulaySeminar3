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
