"""Build the workbook, the exclusion log, and the two narratives."""

from pathlib import Path

import pandas as pd
from openpyxl import Workbook

SUMMARY_NAME = "results_summary.md"
GUIDE_NAME = "interpretation_guide.md"
EXCLUSION_COLUMNS = ("stage", "key", "reason", "source")
DICTIONARY_COLUMNS = ("file", "column", "type", "meaning", "units")
SHEETS = (
    "Year and Source Coverage",
    "Matched Dataset",
    "Sample Characteristics",
    "Descriptive Statistics",
    "Pearson Correlations",
    "Spearman Sensitivity",
    "Correlation Comparisons",
    "Missing Data and Exclusions",
    "Data Dictionary",
)
LIMITATION = "No Pearson test has test_status=ok."
R2_SENTENCE = "R2 is omitted because only one period was tested."


def workbook(bundle, output_path):
    """Write the nine result sheets in contract order."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exclusions = compile_exclusions(bundle.get("exclusions") or [], None)
    book = Workbook()
    book.remove(book.active)
    _write_frame(book, SHEETS[0], bundle.get("source_comparison"))
    _write_frame(book, SHEETS[1], bundle.get("merged"))
    _write_frame(book, SHEETS[2], bundle.get("borough"))
    _write_frame(book, SHEETS[3], bundle.get("descriptive"))
    _write_frame(book, SHEETS[4], bundle.get("pearson"))
    sheet = book.create_sheet(SHEETS[5])
    _append_frame(sheet, bundle.get("spearman"))
    sheet.append([])
    _append_frame(sheet, bundle.get("sensitivity"))
    _write_frame(book, SHEETS[6], bundle.get("comparison"))
    _write_frame(book, SHEETS[7], exclusions)
    _write_frame(book, SHEETS[8], _dictionary(bundle))
    book.save(path)
    return path


def compile_exclusions(frames, output_path):
    """Concatenate exclusion logs. Column order is stage, key, reason, source."""

    parts = []
    for frame in frames or []:
        if frame is None or len(frame) == 0:
            continue
        part = frame.copy()
        for column in EXCLUSION_COLUMNS:
            if column not in part.columns:
                part[column] = ""
        parts.append(part.loc[:, list(EXCLUSION_COLUMNS)])
    if parts:
        result = pd.concat(parts, ignore_index=True)
    else:
        result = pd.DataFrame(columns=list(EXCLUSION_COLUMNS))
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(path, index=False)
    return result


def narratives(bundle, output_dir):
    """Write the four-part summary and the interpretation guide."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary = output / SUMMARY_NAME
    guide = output / GUIDE_NAME
    summary.write_text(_summary(bundle), encoding="utf-8")
    guide.write_text(_guide(bundle), encoding="utf-8")
    return summary, guide


def _summary(bundle):
    pearson = bundle.get("pearson")
    ok = _ok_rows(pearson)
    primary = _primary(pearson)
    lines = ["# Results summary", ""]
    if not ok:
        lines.extend([LIMITATION, ""])
    lines.extend(
        [
            "## Question and hypotheses",
            "",
            "The question asks how neighborhood mean hourly traffic volume lines up with the asthma emergency-department rate for children ages 4 and under.",
            "H0 says the Pearson correlation is 0. H1 says the correlation is positive. The test is two-sided at alpha 0.05.",
            "",
            "## Sources, sample size, variables, and descriptive statistics",
            "",
            "Traffic comes from the selected count source. The outcome is asthma_ed_rate_per_10000. The exposure is traffic_mean_vehicles_per_hour.",
        ]
    )
    descriptive = bundle.get("descriptive")
    if descriptive is not None and len(descriptive):
        row = descriptive.iloc[0]
        lines.append(
            f"The descriptive table lists {_cell(row.get('variable'))} with n={_cell(row.get('n'))} and mean {_cell(row.get('mean'))}."
        )
    lines.extend(["", "## Primary numbers", ""])
    if not ok or primary is None:
        lines.append(LIMITATION)
    else:
        lines.append(
            f"The primary period is {_cell(primary.get('period'))}. "
            f"n={_cell(primary.get('n'))}; r={_cell(primary.get('r'))}; "
            f"p={_cell(primary.get('p_raw'))}; The sign is {_cell(primary.get('sign'))}."
        )
        lines.append(
            f"The interval runs from {_cell(primary.get('ci_low'))} to {_cell(primary.get('ci_high'))}."
        )
    lines.extend(["", "## Test justification", ""])
    if not ok:
        lines.append(LIMITATION)
    else:
        for record in ok:
            lines.append(
                f"{_cell(record.get('period'))} role {_cell(record.get('role'))} "
                f"n={_cell(record.get('n'))}; r={_cell(record.get('r'))}; p={_cell(record.get('p_raw'))}"
            )
        lines.append("The statistical charts are R1_primary_scatter and R3_correlation_forest.")
        if len(_records(pearson)) < 2:
            lines.append(R2_SENTENCE)
    return "\n".join(lines) + "\n"


def _guide(bundle):
    pearson = bundle.get("pearson")
    ok = _ok_rows(pearson)
    primary = _primary(pearson)
    if not ok or primary is None:
        lines = [
            "n is the number of neighborhoods with a rate and a traffic mean.",
            "r is the Pearson correlation.",
            "p-value is not available.",
            "confidence interval is not available.",
            "directional reading is not available.",
            "cross-period consistency is not available.",
            "ecological limit is that a neighborhood pattern is not an individual-level pattern.",
            LIMITATION,
        ]
        return "\n".join(lines) + "\n"
    lines = [
        f"n is the number of neighborhoods in the Pearson test. The primary n is {_cell(primary.get('n'))}.",
        "r is the Pearson correlation between traffic_mean_vehicles_per_hour and asthma_ed_rate_per_10000. "
        f"r={_cell(primary.get('r'))}",
        "p-value is the two-sided tail probability under a null correlation of 0. "
        f"p={_cell(primary.get('p_raw'))}",
        "confidence interval is the 95 percent interval for that correlation, "
        f"from {_cell(primary.get('ci_low'))} to {_cell(primary.get('ci_high'))}.",
        f"directional reading is the sign of r. The primary sign is {_cell(primary.get('sign'))}.",
        "cross-period consistency is whether the sign and the interval agree across tested periods.",
        "ecological limit is that a neighborhood pattern is not an individual-level pattern.",
    ]
    return "\n".join(lines) + "\n"


def _dictionary(bundle):
    catalog = (
        ("final_merged_dataset.csv", bundle.get("merged")),
        ("pearson_results_by_year.csv", bundle.get("pearson")),
        ("spearman_results_by_year.csv", bundle.get("spearman")),
        ("sensitivity_analysis_results.csv", bundle.get("sensitivity")),
        ("correlation_comparison_results.csv", bundle.get("comparison")),
        ("descriptive_statistics_by_year.csv", bundle.get("descriptive")),
        ("descriptive_statistics_by_borough.csv", bundle.get("borough")),
        ("dataset_source_comparison.csv", bundle.get("source_comparison")),
        ("excluded_observations.csv", None),
    )
    rows = []
    for filename, frame in catalog:
        columns = EXCLUSION_COLUMNS if frame is None else list(getattr(frame, "columns", []))
        for column in columns:
            rows.append(
                {
                    "file": filename,
                    "column": column,
                    "type": "str",
                    "meaning": f"{column} in {filename}",
                    "units": "see the source table",
                }
            )
    return pd.DataFrame(rows, columns=list(DICTIONARY_COLUMNS))


def _write_frame(book, name, frame):
    sheet = book.create_sheet(name)
    _append_frame(sheet, frame)


def _append_frame(sheet, frame):
    columns = list(EXCLUSION_COLUMNS if frame is None else frame.columns)
    sheet.append(columns)
    if frame is None or len(frame) == 0:
        return
    for record in frame.to_dict(orient="records"):
        sheet.append([_cell(record.get(column)) for column in columns])


def _ok_rows(frame):
    return [
        record
        for record in _records(frame)
        if _cell(record.get("test_status")).lower() == "ok"
    ]


def _primary(frame):
    for record in _records(frame):
        if _cell(record.get("role")).lower() == "primary":
            return record
    return None


def _records(frame):
    if frame is None or len(getattr(frame, "index", [])) == 0:
        return []
    return frame.to_dict(orient="records")


def _cell(value):
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()
