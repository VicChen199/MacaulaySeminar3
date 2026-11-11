"""Audit asthma periods and traffic year counts."""

import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.audit import AuditError, inventory_asthma
from lib.config import load_config
from lib.socrata import SocrataError, fetch_schema, fetch_year_counts

_AUDIT_DIR = Path("outputs/audit")
_PERIOD_HEADER = "period,period_kind,constituent_years,n_rows"


def _write_periods(periods, path):
    with path.open("w", newline="") as handle:
        handle.write(_PERIOD_HEADER + "\n")
        writer = csv.writer(handle, quoting=csv.QUOTE_NONNUMERIC, lineterminator="\n")
        for row in periods.itertuples(index=False):
            writer.writerow(
                (row.period, row.period_kind, row.constituent_years, int(row.n_rows))
            )


def main():
    try:
        config = load_config()
        periods = inventory_asthma(config["asthma"]["raw_dir"])
        schema = fetch_schema(config)
        counts = fetch_year_counts(config, schema)
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        _write_periods(periods, _AUDIT_DIR / "asthma_time_periods.csv")
        counts.to_csv(_AUDIT_DIR / "traffic_year_counts.csv", index=False)
    except (AuditError, SocrataError) as exc:
        print(exc.message)
        return exc.exit_code

    for period, kind in zip(periods["period"], periods["period_kind"]):
        print(f"{period} {kind}")
    for source, year, record_count in zip(
        counts["source"], counts["year"], counts["record_count"]
    ):
        print(f"{source} {year} {record_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
