# Feature: audit-year-from-date

Source contract: `docs/architecture.md`

Contract patched: yes

## Slice: Count audit years from a date alias
status: done
goal: The audit counts historical rows by extracting the year from `date`, and counts AADT rows through `aadt_year`, without adding a year column to a traffic extract.
owns:
- config/pipeline.toml
- lib/socrata.py
- tests/test_audit_sources.py
- tests/test_toolchain.py
routes:
- audit-sources
acceptance:
- `fields.aadt.year` is `["year", "yr", "aadt_year"]`. `fields.historical.date` is `["date"]`. Automated and historical year lists stay `["year", "yr"]`. Automated and AADT have no date alias. `load_config` returns that table, and `test_load_config_returns_architecture_table` expects it.
- When a source's live keys include a configured year alias, `fetch_year_counts` still filters and groups on the first such alias. A text sample is quoted. The current fixtures stay on that path: automated uses `yr` and not `year`, historical uses `year`, and AADT uses `year`. `schema_ok` is true. `test_year_counts_from_mocked_count` and `test_fixture_audit_prints_period_kinds_and_year_counts` still pass.
- When historical live keys include `date` and include neither `year` nor `yr`, each target-year count uses `$where=date_extract_y(date)=<year>` with the year unquoted, and the one grouped year list uses `date_extract_y(date)` in both `$select` and `$group`. `schema_ok` is true and `record_count` is the returned count. No `$offset` and no volume page.
- When AADT live keys include `aadt_year` and include neither `year` nor `yr`, and the sample value is text, each count uses `$where=aadt_year='<year>'`. `schema_ok` is true.
- A live schema with neither a year alias nor that source's date alias prints the live column names joined by `, ` and exits 2. `traffic_year_counts.csv` is not written for that run.
- A failed count query still prints the live column names and exits 2. An empty asthma export still prints that the export has no rows and exits 2. A pooled `TimePeriod` stays one period.
- `traffic_year_counts` stays `source`, `year`, `record_count`, `schema_ok`. `schema_snapshot.json` stays the live keys. This slice does not download historical rows, unpivot the 24 hour columns, or teach `select-sources` or `download-traffic` the date expression.
done_when: A fixture audit whose historical schema has `date` and no year alias, and whose AADT schema has `aadt_year` and no year alias, prints each period kind and the three API year counts and exits 0. A schema with neither a year alias nor a date alias exits 2 with the live column names. The existing year-alias fixtures still exit 0.
tables: `traffic_year_counts` and `schema_snapshot` keep their current columns. No year column is added to a traffic extract.
migration: none
existing_tests:
- tests/test_audit_sources.py::test_pooled_time_period_stays_one_period
- tests/test_audit_sources.py::test_empty_export_exits_2
- tests/test_audit_sources.py::test_year_counts_from_mocked_count
- tests/test_audit_sources.py::test_fixture_audit_prints_period_kinds_and_year_counts
- tests/test_toolchain.py::test_requirements_pin_minimums
- tests/test_toolchain.py::test_interpreter_is_python_311
- tests/test_toolchain.py::test_run_all_script_route_has_no_health_route
- tests/test_toolchain.py::test_run_all_terminal_stops_at_first_nonzero
