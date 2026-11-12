# Feature: major-road-length

Source contract: `docs/architecture.md`

Contract patched: yes

## Slice: Score major-road length
status: todo
goal: Write one major-road kilometers-per-square-kilometer score for each UHF42 neighborhood from a single LION snapshot. A street centerline adds nothing. The score is not a year and does not replace the count-site exposure.
owns:
- config/pipeline.toml
- lib/major_roads.py
- scripts/11_score_major_roads.py
- run_all.sh
- tests/test_score_major_roads.py
- tests/test_toolchain.py
routes:
- score-major-roads
- run-tests
acceptance:
- `geography.lion_url` defaults to `https://data.cityofnewyork.us/api/geospatial/2v4z-66xt?method=export&format=Shapefile`. `geography.lion_major_rw_types` defaults to `[2, 3, 4, 9]`. `geography.lion_constructed_status` defaults to `[2]`. `geography.lion_path` stays empty. `load_config` returns those keys, and `test_load_config_returns_architecture_table` expects them in `_expected_config`.
- `score_major_roads` keeps a centerline only when its stripped integer roadway type is in `lion_major_rw_types` and its stripped integer status is in `lion_constructed_status`. Roadway type 1 adds no length. A text value `"2"` matches integer 2.
- Length and area are measured after reprojection to EPSG:2263 and converted with that CRS. `major_road_km_per_km2` equals `major_road_km / area_km2`. A neighborhood with no assigned length has `major_road_km` 0, a positive area, and score 0, and it is still a row.
- A centerline portion that intersects two polygons is assigned to the polygon with the longer intersection. An equal length goes to the lower `uhf42_id` string. The other polygon gets none of that portion.
- The fixture test calls `score_major_roads` on two polygons and does not download, does not write `outputs/` or `data/`, and does not call Socrata. `ensure_lion` is not called on that path.
- `python scripts/11_score_major_roads.py` takes no arguments. When `geography.lion_path` names a file, that file is the LION extract and no HTTP request is made. When the path is empty and `data/raw/geography/LION_SOURCE.txt` is absent, the script downloads `geography.lion_url` once, writes the URL and sha256 into `LION_SOURCE.txt`, and reuses that extract on the next run. It does not rewrite `config/pipeline.toml`. `assign-uhf` still reads only `geography.lion_path`.
- The script writes `outputs/data/major_road_burden.csv` with columns `uhf42_id`, `major_road_km`, `area_km2`, `major_road_km_per_km2`, `n_major_pieces`, one row for each of the 42 UHF ids. On success the terminal prints the neighborhood count, the median score, and the count of zero scores, and exits 0.
- A missing UHF shapefile prints that the person should run `select-sources` and exits 2. A LION file without a roadway-type field or a status field prints the field names and exits 2. An id set other than the 42 UHF ids exits 2 and writes no score file. A failed download prints the URL and exits 1.
- `run_all.sh` runs `scripts/11_score_major_roads.py` after `scripts/08_describe.py` and before `scripts/09_test_hypotheses.py`. `SCRIPT_ROUTE` in `tests/test_toolchain.py` matches that order. `test_run_all_terminal_stops_at_first_nonzero` still stops at `scripts/01_audit_sources.py`.
done_when: A fixture with a highway of known length inside one polygon, a street of the same length, and a boundary tie exits through `score_major_roads` with the highway length in that polygon, zero length from the street, and the tie assigned to the lower `uhf42_id`. `run_all.sh` lists script 11 after script 08 and before script 09.
tables: `major_road_burden` is new. Columns are `uhf42_id`, `major_road_km`, `area_km2`, `major_road_km_per_km2`, `n_major_pieces`. `LION_SOURCE.txt` records the URL and sha256 when an extract is downloaded or reused.
migration: Add `major_road_burden`. Add config keys `geography.lion_url`, `geography.lion_major_rw_types`, and `geography.lion_constructed_status`.
existing_tests:
- tests/test_toolchain.py::test_load_config_returns_architecture_table
- tests/test_toolchain.py::test_run_all_script_route_has_no_health_route
- tests/test_toolchain.py::test_run_all_terminal_stops_at_first_nonzero
- tests/test_assign_uhf.py::test_non_interior_counts_are_excluded_and_exit_0

## Slice: Correlate major-road length with asthma
status: todo
goal: Add a Pearson series of `major_road_km_per_km2` against the asthma rate. The count-site primary test stays the primary test, including its period choice.
owns:
- lib/stats.py
- scripts/09_test_hypotheses.py
- tests/test_hypotheses.py
routes:
- test-hypotheses
acceptance:
- `major_road_pearson` joins the one score to every merged neighborhood with a non-null `asthma_ed_rate_per_10000`, including `coverage_status=insufficient`. x is `major_road_km_per_km2`. y is `asthma_ed_rate_per_10000`. A score of 0 stays in the test. A null rate is omitted. The same score is used for every period.
- The series uses the primary period `pearson_by_period` already chose. An earlier period with a smaller major-road p-value stays secondary. `primary_reason` on the major-road rows matches the count-site primary reason. The choice does not read either series' p-values.
- Each row records t = r * sqrt((n - 2) / (1 - r^2)), df = n - 2, the SciPy two-sided p-value, and the 95% interval from `confidence_interval`. A relative gap larger than 1e-4 between the formula t and SciPy's t exits 2. n below 3, zero variance, or |r| = 1 writes `test_status=skipped` and a null p-value. Holm applies only to secondary rows of this series with `test_status=ok`. Alpha stays 0.05.
- The series does not run Spearman, sensitivity, or bootstrap. It does not change any value in `pearson_results_by_year.csv`, `spearman_results_by_year.csv`, `sensitivity_analysis_results.csv`, or `correlation_comparison_results.csv`.
- The script writes `outputs/stats/major_road_pearson_by_year.csv` with columns `period`, `role`, `n`, `r`, `t`, `df`, `p_raw`, `p_holm`, `ci_low`, `ci_high`, `alpha`, `p_below_alpha`, `sign`, `primary_reason`, `test_status`, `skip_reason`. One row per included period.
- On success the terminal still prints the count-site primary line and each secondary line, then one line per major-road period with n, r, the interval, and the p-value. A skipped major-road period prints the reason and no p-value.
- A missing `major_road_burden.csv` prints that path and exits 2, and that run writes none of the stat tables. The existing hypothesis helper plants a score table so the count-site tests still run. `test_preference_year_stays_primary_when_an_earlier_p_is_smaller` still keeps the preference year as the count-site primary.
- The fixture test does not write the repository `outputs/` or `data/` trees and does not call Socrata.
done_when: A fixture whose count-site primary r is unchanged also writes one major-road row per included period, uses the same primary period, and includes a neighborhood with a rate and `coverage_status=insufficient`. A missing score file exits 2 and leaves no stat tables.
tables: `major_road_pearson` is new. `pearson_results`, `spearman_results`, `sensitivity_results`, and `correlation_comparison` keep their current columns and their count-site values.
migration: Add `major_road_pearson`.
existing_tests:
- tests/test_hypotheses.py::test_preference_year_stays_primary_when_an_earlier_p_is_smaller
- tests/test_hypotheses.py::test_script_prints_the_primary_interval_and_writes_the_tables
- tests/test_hypotheses.py::test_every_skipped_period_exits_2
- tests/test_hypotheses.py::test_missing_merged_file_exits_2
- tests/test_hypotheses.py::test_bootstrap_runs_only_when_the_flag_is_true

## Slice: Report the major-road series
status: todo
goal: Show the major-road Pearson numbers in the workbook, in R5, and in a fifth summary part. The count-site figures and the first four summary parts stay the count-site test.
owns:
- lib/plots.py
- lib/report.py
- scripts/10_report.py
- tests/test_write_report.py
routes:
- write-report
acceptance:
- The workbook sheets, in order, are Year and Source Coverage, Matched Dataset, Sample Characteristics, Descriptive Statistics, Pearson Correlations, Major Road Correlations, Spearman Sensitivity, Correlation Comparisons, Missing Data and Exclusions, Data Dictionary. `SHEETS` in `tests/test_write_report.py` matches that order. The Data Dictionary includes `major_road_km_per_km2` and the `major_road_pearson` columns.
- R5 is `outputs/figures/results/R5_major_road_scatter.png` and `.pdf` for the primary period. The chart shows n, r, and the exact p-value from `major_road_pearson_by_year.csv`. The x-axis is `major_road_km_per_km2` and the y-axis is `asthma_ed_rate_per_10000`. R1 through R4 stay the count-site charts. R1's r and p still match `pearson_results_by_year.csv`.
- `results_summary.md` has the existing four headings plus `## Major-road length`. `HEADINGS` matches those five. The fifth part states that series' primary n, r, interval, and p-value and names R5. Those numbers match `major_road_pearson_by_year.csv`. The first four parts still match the count-site Pearson CSV. The fifth part contains no causal claim and no literature comparison.
- `interpretation_guide.md` defines `major_road_km_per_km2` with the primary major-road r. It keeps the ecological limit.
- When the major-road primary row is `test_status=skipped` and a count-site row is ok, R5 is omitted, the fifth part states the skip, and the script exits 0. When no count-site row is ok, the limitation statement is written and the script exits 2. A missing `major_road_pearson_by_year.csv` prints the path and exits 2.
- The fixture test does not write the repository `outputs/` or `data/` trees and does not call Socrata.
done_when: A fixture with one ok count-site row and one ok major-road primary row prints the workbook path and the R1, R3, and R5 paths and exits 0. The workbook has the ten sheets in order. The fifth summary part matches `major_road_pearson_by_year.csv`, and the count-site r and p still match `pearson_results_by_year.csv`.
tables: `major_road_burden` and `major_road_pearson` are read. The workbook gains the sheet Major Road Correlations. No count-site column changes.
migration: none
existing_tests:
- tests/test_write_report.py::test_two_periods_keep_shared_scale_and_match_the_pearson_csv
- tests/test_write_report.py::test_one_tested_period_prints_r1_and_r3_and_omits_r2
- tests/test_write_report.py::test_no_ok_pearson_test_states_the_limitation_and_exits_2
