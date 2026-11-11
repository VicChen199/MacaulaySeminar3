#!/bin/sh
set -e
export PYTHONPATH=.
python scripts/01_audit_sources.py
python scripts/02_select_sources.py
python scripts/03_download_traffic.py
python scripts/04_clean.py
python scripts/05_assign_uhf42.py
python scripts/06_aggregate_traffic.py
python scripts/07_merge.py
python scripts/08_describe.py
python scripts/09_test_hypotheses.py
python scripts/10_report.py
