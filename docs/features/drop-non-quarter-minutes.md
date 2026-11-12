# Feature: drop-non-quarter-minutes

Source contract: `docs/architecture.md`

Contract patched: yes

## Slice: Score past non-quarter automated minutes
status: done
goal: Source selection keeps scoring a year when some automated rows use a minute other than 0, 15, 30, or 45, and those rows do not enter the quarter-hour sum.
owns:
- lib/audit.py
- tests/test_select_sources.py
routes:
- select-sources
acceptance:
- An automated coverage year that includes integer minutes outside {0, 15, 30, 45} and also includes 0, 15, 30, and 45 does not print `hourly_definition_unresolved` and does not exit 2. A complete hour is still the sum of the four quarter volumes only. A minute of 10, 20, 40, or 50 adds nothing to that sum.
- Three quarter-hours are still not a complete hour. `test_three_quarters_are_not_a_complete_hour` stays green.
- A historical minute such as 10 still prints `hourly_definition_unresolved` with the source and year and exits 2. `test_hourly_definition_unresolved_exits_2` stays green.
- An automated minute that does not parse still exits 2 with `hourly_definition_unresolved` and the year.
- `source_selection`, `dataset_source_comparison`, and `coverage_for_selection` keep their current columns. This slice writes no exclusion file and does not change download, clean, or aggregation.
done_when: A fixture selection whose automated 2022 rows mix quarter-hours with minutes 10, 20, 40, and 50 exits 0 and scores only the quarter-hours. The same minutes on the historical source still exit 2.
tables: `source_selection`, `dataset_source_comparison`, and `coverage_for_selection` keep their current columns.
migration: none
existing_tests:
- tests/test_select_sources.py::test_hourly_definition_unresolved_exits_2
- tests/test_select_sources.py::test_three_quarters_are_not_a_complete_hour
- tests/test_select_sources.py::test_scenario_a_prints_one_source_per_period
- tests/test_select_sources.py::test_two_directions_keep_an_hour_only_when_both_are_present

## Slice: Drop non-quarter automated rows in cleaning
status: done
goal: Cleaning removes automated rows whose minute is an integer outside {0, 15, 30, 45} and lists each removed row.
owns:
- lib/clean_traffic.py
- tests/test_clean.py
routes:
- clean
acceptance:
- `clean_traffic` drops an automated row whose minute is an integer outside {0, 15, 30, 45}. The row is absent from `cleaned_traffic`. `exclusions_clean` gains one row with `stage` `clean`, `key` equal to that row's `:id`, `reason` `minute_not_quarter`, and `source` `automated`. A year that is otherwise valid exits 0.
- Zero volumes stay. A negative volume is still excluded with `negative_volume`. An exact duplicate is still excluded with `duplicate_row`.
- An automated minute that does not parse still exits 2 and the message names that token. `cleaned_traffic` is not written for that run.
- A historical minute that is not a one-hour count still exits 2. `test_historical_minute_exits_2` stays green.
- Aggregation is unchanged. A non-quarter minute that is already in the aggregation input still exits 2. `test_minute_outside_the_quarter_set_exits_2` and `test_script_bad_minute_exits_2` stay green.
- `test_unexpected_minute_exits_2` changes with this slice: minute 10 on an automated row is dropped and listed, and it does not exit 2.
done_when: A fixture clean of automated rows that include minute 10 exits 0, omits that row from `cleaned_traffic`, and lists it in `exclusions_clean` with reason `minute_not_quarter`. A historical non-hour minute still exits 2.
tables: `exclusions_clean` keeps columns `stage`, `key`, `reason`, and `source`. `cleaned_traffic` keeps its current columns and omits the dropped rows.
migration: none
existing_tests:
- tests/test_clean.py::test_historical_minute_exits_2
- tests/test_clean.py::test_negative_volume_is_excluded_and_zero_remains
- tests/test_clean.py::test_fixture_clean_prints_period_counts
- tests/test_aggregate_traffic.py::test_minute_outside_the_quarter_set_exits_2
- tests/test_aggregate_traffic.py::test_script_bad_minute_exits_2
