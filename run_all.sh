#!/bin/sh
set -e
export PYTHONPATH=.

LOG=outputs/run_all.log
mkdir -p outputs
: > "$LOG"

_FORMAT_PY=$(mktemp)
trap 'rm -f "$_FORMAT_PY"' EXIT
cat > "$_FORMAT_PY" <<'PY'
import re
import sys
from pathlib import Path

SOURCES = {"automated", "historical", "aadt"}
RESULT = re.compile(
    r"^(primary|secondary) (\S+) r=(\S+) ci=([^,\s]+),(\S+) p=(\S+)$"
)
SKIP = re.compile(r"^(primary|secondary) (\S+) skipped (.+)$")
ELIGIBLE = re.compile(r"^(\S+) eligible (\d+) median (\S+)$")
USABLE = re.compile(r"^(\S+) usable (\d+)$")
ASSIGN = (
    ("locations assigned", "Locations assigned"),
    ("borough-mismatched", "Borough-mismatched"),
    ("on a boundary", "On a boundary"),
    ("unmatched", "Unmatched"),
)


def _lines(path):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _emit(lines):
    if not lines:
        return
    sys.stdout.write("\n".join(lines) + "\n")


def _comma(value):
    return f"{int(value):,}"


def _num(value):
    return f"{float(value):.3f}"


def _pvalue(value):
    number = float(value)
    if number >= 0.001 or number == 0:
        return f"{number:.3f}"
    return f"{number:.3g}"


def _raw(out, state, line):
    if state["section"] is not None and out and out[-1] != "":
        out.append("")
    state["section"] = None
    out.append(line)


def _heading(out, state, title):
    if state["section"] == title:
        return
    if out:
        out.append("")
    out.append(title)
    state["section"] = title


def _figure_label(path):
    stem = Path(path).stem
    code, sep, rest = stem.partition("_")
    if not sep:
        return f"  {stem}"
    return f"  {code}  {rest.replace('_', ' ')}"


def _is_figure(line):
    return line.startswith("outputs/figures/") or line.endswith((".png", ".pdf"))


def format_audit(lines):
    out = []
    state = {"section": None}
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[1] in {"year", "pooled"}:
            _heading(out, state, "Asthma periods")
            kind = "single year" if parts[1] == "year" else "pooled years"
            out.append(f"  {parts[0]}: {kind}")
            continue
        if (
            len(parts) == 3
            and parts[0] in SOURCES
            and parts[1].isdigit()
            and parts[2].isdigit()
        ):
            _heading(out, state, "Traffic record counts")
            out.append(f"  {parts[0]} {parts[1]}: {_comma(parts[2])} records")
            continue
        _raw(out, state, line)
    return out


def format_select(lines):
    out = []
    state = {"section": None}
    for line in lines:
        if line.startswith("scenario "):
            out.append(f"Scenario: {line.split(' ', 1)[1]}")
            state["section"] = None
            continue
        if " excluded " in line:
            period, reason = line.split(" excluded ", 1)
            _heading(out, state, "Periods")
            out.append(f"  {period}: excluded, {reason}")
            continue
        parts = line.split()
        if len(parts) == 2 and parts[1] in SOURCES:
            _heading(out, state, "Periods")
            out.append(f"  {parts[0]}: {parts[1]}")
            continue
        _raw(out, state, line)
    return out


def format_download(lines):
    out = []
    state = {"section": None}
    for line in lines:
        parts = line.split()
        if (
            len(parts) == 4
            and parts[0] in SOURCES
            and parts[1].isdigit()
            and parts[2].isdigit()
            and parts[3].isdigit()
        ):
            _heading(out, state, "Downloaded counts")
            source, year, expected, saved = parts
            if expected == saved:
                out.append(f"  {source} {year}: {_comma(saved)} records")
            else:
                out.append(
                    f"  {source} {year}: saved {_comma(saved)} of {_comma(expected)} expected"
                )
            continue
        _raw(out, state, line)
    return out


def format_clean(lines):
    out = []
    state = {"section": None}
    for line in lines:
        if line.startswith("duplicates removed "):
            _raw(out, state, f"Duplicates removed: {_comma(line.rsplit(' ', 1)[1])}")
            continue
        if line.startswith("invalid counts flagged "):
            _raw(out, state, f"Invalid counts flagged: {_comma(line.rsplit(' ', 1)[1])}")
            continue
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            _heading(out, state, "Asthma rows")
            out.append(f"  {parts[0]}: {_comma(parts[1])}")
            continue
        _raw(out, state, line)
    return out


def format_assign(lines):
    out = []
    for line in lines:
        matched = False
        for prefix, label in ASSIGN:
            lead = prefix + " "
            if line.startswith(lead) and line[len(lead) :].isdigit():
                out.append(f"{label}: {_comma(line[len(lead) :])}")
                matched = True
                break
        if not matched:
            out.append(line)
    return out


def format_aggregate(lines):
    out = []
    state = {"section": None}
    for line in lines:
        match = ELIGIBLE.match(line)
        if match:
            period, count, median = match.groups()
            _heading(out, state, "Eligible neighborhoods")
            if count == "0":
                out.append(f"  {period}: no eligible neighborhoods")
            else:
                out.append(
                    f"  {period}: {_comma(count)} eligible, median {median} segments"
                )
            continue
        _raw(out, state, line)
    return out


def format_merge(lines):
    out = []
    state = {"section": None}
    for line in lines:
        match = USABLE.match(line)
        if match:
            period, count = match.groups()
            _heading(out, state, "Usable neighborhoods")
            if count == "0":
                out.append(f"  {period}: none")
            else:
                out.append(f"  {period}: {_comma(count)}")
            continue
        _raw(out, state, line)
    return out


def _format_figures(lines, heading, primary=False):
    out = []
    state = {"section": None}
    seen = set()
    for line in lines:
        if primary and line.startswith("primary period "):
            _raw(out, state, f"Primary period: {line.split(' ', 2)[2]}")
            continue
        if line.endswith(".xlsx"):
            _raw(out, state, "Workbook")
            out.append(f"  {line}")
            continue
        if _is_figure(line):
            _heading(out, state, heading)
            label = _figure_label(line)
            if label not in seen:
                seen.add(label)
                out.append(label)
            continue
        _raw(out, state, line)
    return out


def format_describe(lines):
    return _format_figures(lines, "Sample figures", primary=True)


def format_report(lines):
    return _format_figures(lines, "Result figures", primary=False)


def format_hypotheses(lines):
    out = []
    state = {"section": None}
    for line in lines:
        match = RESULT.match(line)
        if match:
            role, period, pearson, low, high, p_value = match.groups()
            _heading(out, state, "Correlations")
            out.append(
                f"  {period} ({role}): r = {_num(pearson)}, "
                f"95% CI [{_num(low)}, {_num(high)}], p = {_pvalue(p_value)}"
            )
            continue
        match = SKIP.match(line)
        if match:
            role, period, reason = match.groups()
            _heading(out, state, "Correlations")
            out.append(f"  {period} ({role}) skipped: {reason}")
            continue
        _raw(out, state, line)
    return out


FORMATTERS = {
    "01": format_audit,
    "02": format_select,
    "03": format_download,
    "04": format_clean,
    "05": format_assign,
    "06": format_aggregate,
    "07": format_merge,
    "08": format_describe,
    "09": format_hypotheses,
    "10": format_report,
}


def main():
    stage = sys.argv[1]
    lines = _lines(sys.argv[2])
    formatter = FORMATTERS.get(stage, lambda items: items)
    _emit(formatter(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY

_fmt_elapsed() {
  _secs=$1
  if [ "$_secs" -ge 3600 ]; then
    printf '%dh %dm %02ds' $((_secs / 3600)) $(((_secs % 3600) / 60)) $((_secs % 60))
  elif [ "$_secs" -ge 60 ]; then
    printf '%dm %02ds' $((_secs / 60)) $((_secs % 60))
  elif [ "$_secs" -eq 0 ]; then
    printf '<1s'
  else
    printf '%ds' "$_secs"
  fi
}

_show() {
  printf '%s\n' "$1" | tee -a "$LOG"
}

run_stage() {
  _num=$1
  _title=$2
  _script=$3
  _start=$(date +%s)
  _show ""
  _show "Starting stage ${_num} — ${_title}"
  _tmp=$(mktemp)
  _formatted=$(mktemp)
  set +e
  python "$_script" >"$_tmp" 2>&1
  _status=$?
  python -u "$_FORMAT_PY" "$_num" "$_tmp" >"$_formatted"
  _fmt_status=$?
  set -e
  if [ "$_fmt_status" -eq 0 ]; then
    cat "$_formatted" | tee -a "$LOG"
  else
    cat "$_tmp" | tee -a "$LOG"
  fi
  rm -f "$_tmp" "$_formatted"
  _end=$(date +%s)
  _show ""
  _show "Stage ${_num} finished in $(_fmt_elapsed $((_end - _start)))"
  if [ "$_status" -ne 0 ]; then
    _show "Stopped at stage ${_num} (exit ${_status})"
    exit "$_status"
  fi
}

_pipeline_start=$(date +%s)
_show "Pipeline"
_show "Started: $(date '+%Y-%m-%d %H:%M:%S')"

run_stage "01" "Audit asthma periods and traffic counts" "scripts/01_audit_sources.py"
run_stage "02" "Select a count source for each period" "scripts/02_select_sources.py"
run_stage "03" "Download traffic counts" "scripts/03_download_traffic.py"
run_stage "04" "Clean asthma and traffic" "scripts/04_clean.py"
run_stage "05" "Assign counts to neighborhoods" "scripts/05_assign_uhf42.py"
run_stage "06" "Aggregate traffic by neighborhood" "scripts/06_aggregate_traffic.py"
run_stage "07" "Merge traffic with asthma rates" "scripts/07_merge.py"
run_stage "08" "Describe the sample and draw figures" "scripts/08_describe.py"
run_stage "09" "Test the correlation hypotheses" "scripts/09_test_hypotheses.py"
run_stage "10" "Write the report" "scripts/10_report.py"

_pipeline_end=$(date +%s)
_show ""
_show "Pipeline finished in $(_fmt_elapsed $((_pipeline_end - _pipeline_start)))"
