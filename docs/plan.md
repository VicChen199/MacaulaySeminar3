# Plan

Source contract: `docs/architecture.md`

Phase 0 is the walking skeleton and is never parallel. Later phases are vertical slices: one user-visible outcome with the schema, API, and screen it needs.

A phase may share a `parallel_group` only when its `owns` globs do not overlap another phase in that group and it does not edit the schema. Schema migrations stay sequential.

There is no database schema and no network server. A phase with no `parallel_group` runs alone. The only shared group is `sample-and-hypotheses`.

Every acceptance id below is one of: `audit-sources`, `select-sources`, `download-traffic`, `clean`, `assign-uhf`, `aggregate-traffic`, `merge-analysis`, `describe-sample`, `test-hypotheses`, `write-report`, `run-tests`. Each of those has a route trace and an arrow on the system map.

The test command for every phase is `pytest` from the repository root with `PYTHONPATH=.`. Tests under `run-tests` load `tests/fixtures/` only. They do not read `data/` or `outputs/`, they do not write those trees, and they do not call Socrata. Script behavior is checked by calling the named library functions on fixture tables, or by running the script against a temporary copy of the fixture tree. The terminal is the only screen.

`./run_all.sh` is a caller. It is not an operation. It exports `PYTHONPATH=.`, runs `scripts/01` through `scripts/10` in order, and stops at the first non-zero exit.

## Phase 0: Walking skeleton
depends_on: []
owns:
- requirements.txt
- config/pipeline.toml
- lib/__init__.py
- lib/config.py
- run_all.sh
- tests/test_toolchain.py
- tests/fixtures/toolchain/**
acceptance:
- run-tests: From the repository root, with Python 3.11 and `PYTHONPATH=.`, `pytest` exits 0 and the terminal prints a passed count. The run reads `tests/fixtures/toolchain/` only. It does not read `data/` or `outputs/` and it does not call Socrata. The skeleton installs the stack libraries from `requirements.txt` at the pinned minimums (pandas>=2.2, numpy>=1.26, requests>=2.32, geopandas>=1.0, shapely>=2.0, pyproj>=3.6, scipy>=1.12, matplotlib>=3.8, seaborn>=0.13, openpyxl>=3.1, pyarrow>=15, pytest>=8.0). `load_config` reads `config/pipeline.toml` with `tomllib` and returns every key and default in the architecture config table. Automated field aliases are year `yr`, month `m`, day `d`, hour `hh`, minute `mm`, volume `vol`, segment `segmentid`, wkt `wktgeom`, direction `direction`, borough `boro`, request `requestid`. `fields.historical` and `fields.aadt` use the same canonical names; each alias list contains the canonical name and the automated alias for that field, plus canonical `aadt` for the AADT value. Borough aliases map source tokens onto Manhattan, Bronx, Brooklyn, Queens, and Staten Island. `sources.preference` is `automated`, `historical`, `aadt` in that order. The dev command is `pytest` with `PYTHONPATH=.`. The terminal is the only screen. There is no HTTP server and no health route.
done_when: `pytest` prints a passed count and exits 0. `config/pipeline.toml` is present and `load_config` returns it. `run_all.sh` lists `scripts/01_audit_sources.py` through `scripts/10_report.py` in that order and exits at the first non-zero status. No process listens on a port.

## Phase audit-sources: Show asthma period kinds and traffic year counts
depends_on:
- 0
owns:
- lib/socrata.py
- lib/audit.py
- scripts/01_audit_sources.py
- tests/test_audit_sources.py
- tests/fixtures/audit_sources/**
acceptance:
- audit-sources: `python scripts/01_audit_sources.py` takes no arguments. `load_config` runs, then `inventory_asthma` requires exactly one `.csv` or `.xlsx` in `asthma.raw_dir`. It stores each original `TimePeriod` label, classifies it with the period rule, and writes `outputs/audit/asthma_time_periods.csv` with `period`, `period_kind`, `constituent_years`, and `n_rows`. A label with one four-digit year in `years.targets` is `year`. A label with one year in `years.excluded` is dropped. A label with two four-digit years separated by a hyphen, dash, or the word "to" is `pooled` when every year in the inclusive range is a target year, and it stays one period. A range that includes an excluded year is dropped with reason `period_includes_excluded_year`. Any other label is dropped with reason `period_not_in_study`. `fetch_schema` requests `$limit=1` from each of the three APIs and writes `outputs/audit/schema_snapshot.json`. `fetch_year_counts` requests `count(*)` for each target year and one grouped year list per API. The year field is the first configured alias present in the live keys. No traffic volumes are paged. On success the terminal prints each period label, its kind (`year` or `pooled`), and the year counts for the three traffic APIs, and the process exits 0 with `outputs/audit/traffic_year_counts.csv` (`source`, `year`, `record_count`, `schema_ok`). An empty export (headers, zero rows) prints that the export has no rows and exits 2. A missing export, more than one export, a missing year field, or a failed count query prints the path or the live column names and exits 2. A network or parse failure prints the URL and exits 1.
- run-tests: A pooled `TimePeriod` stays one period. The fixture test does not write `outputs/` and does not call Socrata.
done_when: A fixture audit prints the period kinds and the three API year counts and exits 0. A pooled label is one row with `period_kind=pooled`. An empty export exits 2.

## Phase select-sources: Choose one traffic source per asthma period
depends_on:
- audit-sources
owns:
- lib/socrata.py
- lib/audit.py
- lib/geo.py
- scripts/02_select_sources.py
- tests/test_select_sources.py
- tests/fixtures/select_sources/**
acceptance:
- select-sources: `python scripts/02_select_sources.py` takes no arguments. It reads the audit tables and does not rewrite `config/pipeline.toml`. `ensure_boundaries` downloads the pinned shapefile sidecars and `UHF42.geo.json` when `data/raw/geography/SOURCE.txt` is absent, resolving `geography.git_ref` to a commit SHA, and checks that the local shapefile has 42 valid unique polygons. Later calls reuse those files. `paginate` then pulls coverage columns (`:id`, year, month, day, hour, minute, volume, segment, wkt, direction, borough, and the AADT value when scoring AADT) one year at a time into `coverage.parquet`. `order_field` is `:id`. The offset advances by the number of rows in the page. `score_coverage` uses the same hour, direction, and threshold rules as aggregation. A historical schema that is already one hour per row scores as a complete hour. A historical schema that is neither quarter-hours nor one-hour counts is unusable with reason `hourly_definition_unresolved`. `select_source` writes `outputs/audit/source_selection.json` in this order: (1) automated for every period when automated coverage is adequate for every period; (2) otherwise automated keeps the periods it covers, and historical fills only the gaps when the most recent overlap year has intersection n >= `min_uhf_per_period` and traffic-versus-traffic Pearson r >= `sources.mix_min_traffic_r`, with asthma rates unread; a period both sources cover stays on automated; a failed check excludes the gaps; (3) when automated covers no period and historical covers at least one, historical is used for those periods and automated and AADT are not added; (4) when neither covers a period, AADT is scored and is usable only with a point field or a join from `sources.aadt_xy_csv`, hourly exposure is AADT/24, and AADT does not mix with another source; (5) every dropped period is written with a reason, and zero included periods exits 2. The coverage extract is an audit artifact. On success the terminal prints the scenario, the source for each period, and each excluded period with its reason. The process writes `outputs/dataset_source_comparison.csv` with `n_matched` and `n_usable` empty, and `outputs/audit/coverage_for_selection.csv`. A shapefile that does not have 42 valid unique ids, an unresolved hourly definition, or a failed page exits 2 with the offending year and source. A network failure exits 1.
- run-tests: Pagination advances the offset by the rows in the page. The fixture test does not write `outputs/` and does not call Socrata.
done_when: A fixture selection prints the scenario and one source per included period and exits 0. A selection with zero included periods prints scenario D and exits 2. AADT is never paired with another source on the same period.

## Phase download-traffic: Save a verified parquet for each selected year
depends_on:
- select-sources
owns:
- lib/socrata.py
- scripts/03_download_traffic.py
- tests/test_download_traffic.py
- tests/fixtures/download_traffic/**
acceptance:
- download-traffic: `python scripts/03_download_traffic.py` takes no arguments. It reads `source_selection.json` and, for each included period, each constituent year, and that period's source only, `paginate` requests `count(*)` with `$where` on the resolved year field (quoting the year when the snapshot says the column is text), then pages with `$limit` = `api.page_size`, `$offset` from 0, `$order=:id`, and `$select` including `:id` plus the source fields. Each page is appended to `pages.jsonl` and `checkpoint.json` stores the next offset, the row count, and the page hash. The offset advances by the number of rows in that page. A later run resumes only when the JSONL line count equals the checkpoint. When they disagree the process exits 2 and leaves the year folder untouched. HTTP 429 and 5xx retry with backoff, honoring `Retry-After`. Any other 4xx exits 1. After the last page the row count equals `count(*)`, duplicate `:id` count is zero, and no short page appears before the count is reached. The script then writes `data/raw/traffic/<source>/year=<YYYY>/records.parquet` and `manifest.json` (row count, min and max `:id`, parquet sha256) and deletes `pages.jsonl`. A failed check writes no `records.parquet`. One year file is paged at a time. The script re-downloads the selected years and does not promote `coverage.parquet`. On success the terminal prints, for each year, the expected count and the parquet count, and `outputs/audit/download_log.csv` has one row per year. A count mismatch, duplicate `:id`, or checkpoint mismatch prints the year and both counts and exits 2, and that year has no `records.parquet`. Retry exhaustion or HTTP 4xx prints the URL and exits 1.
- run-tests: A duplicate `:id` blocks compaction. The fixture test does not write `outputs/` and does not call Socrata.
done_when: A fixture year whose page rows match `count(*)` and whose `:id` values are unique prints both counts and leaves `records.parquet` and `manifest.json`. A duplicate `:id` exits 2 and leaves that year without `records.parquet`.

## Phase clean: Write cleaned asthma rates and cleaned traffic counts
depends_on:
- download-traffic
owns:
- lib/clean_asthma.py
- lib/clean_traffic.py
- scripts/04_clean.py
- tests/test_clean.py
- tests/fixtures/clean/**
acceptance:
- clean: `python scripts/04_clean.py` takes no arguments. `clean_asthma` keeps UHF42 rows for the age-4-and-under ED measure, applies the period rule, drops labels whose range includes an excluded year, and keeps a pooled label as one period. Overlapping labels that would put the same neighborhood-year in two analysis periods exit 2. The rate and the count are parsed as numbers. Tokens that mean suppressed become null. `uhf42_id` is the decimal string of the numeric GeoID with leading zeros removed. The cleaned asthma table has one row per (`uhf42_id`, `period`) with columns `uhf42_id`, `uhf42_name`, `borough`, `period`, `period_kind`, `constituent_years`, `asthma_ed_count`, `asthma_ed_rate_per_10000`. `clean_traffic` types the automated-count fields, builds a local timestamp in America/New_York, drops exact duplicate rows while counting them, keeps zero volumes, and excludes negative volumes. A source field that indicates UTC stops the pipeline. Quarter-hours must be a subset of {0, 15, 30, 45}; any other minute set exits 2. Historical rows that are already hourly become one vehicle-per-hour value and are not divided. A historical field that is not a one-hour count exits 2. AADT rows store AADT/24 and a null hour count. Counts above the within-year 99.9th percentile are flagged and kept. One year parquet is read at a time. On success the terminal prints asthma row counts by period, duplicate rows removed, and invalid counts flagged, and the process writes `outputs/data/cleaned_asthma.csv`, `outputs/data/cleaned_traffic.parquet`, `outputs/cleaning_log.md`, and `outputs/audit/exclusions_clean.csv`. Zero asthma rows after the filter exits 2. A duplicate neighborhood-period, an unmapped direction code, or an unexpected minute set exits 2 and names the key. A missing `records.parquet` for a selected year exits 2.
- run-tests: A suppressed rate stays null. A pooled `TimePeriod` stays one period. The fixture test does not write `outputs/` and does not call Socrata.
done_when: A fixture clean prints the period counts and exits 0, with one asthma row per neighborhood-period and a null suppressed rate. An unexpected minute set exits 2.

## Phase assign-uhf: Place each monitor in one UHF42 neighborhood
depends_on:
- clean
owns:
- lib/geo.py
- scripts/05_assign_uhf42.py
- tests/test_assign_uhf.py
- tests/fixtures/assign_uhf/**
acceptance:
- assign-uhf: `python scripts/05_assign_uhf42.py` takes no arguments. It reads cleaned traffic and the local shapefile and does not contact GitHub. A missing shapefile exits 2 and tells the person to run `select-sources`. `assign_points` builds one point per distinct monitoring location from the WKT. Coordinates in longitude -74.3 to -73.6 and latitude 40.4 to 40.95 are EPSG:4326. Coordinates in the New York State Plane range are EPSG:2263. Any other range exits 2. Points and polygons are reprojected to EPSG:2263 before `geopandas.sjoin` with predicate `within`. The boundary id column is the first of `UHFCODE`, `uhfcode`, `UHF_CODE`, `GeoID`, `geoid` whose normalized values equal the asthma `uhf42_id` set. When none do, `geography.id_crosswalk_csv` is required; without it the process exits 2. `monitoring_location_id` is the SHA-1 of `segment_id`, rounded projected x, and rounded projected y. An interior point takes the containing polygon. A point in no polygon is `outside_nyc`. A point within `geography.boundary_tolerance_meters` of a boundary, or assigned to more than one polygon, is `boundary`. A traffic borough that disagrees with the polygon borough is `borough_mismatch`. Those three classes are excluded from the primary data when `geography.lion_path` is empty, and the terminal prints the counts and exits 2. When `lion_path` is set, `resolve_non_interior` assigns the point to the UHF containing the majority of the LION segment length, breaks ties by exclusion, and emits one UHF row for that location. When every point is interior, the LION file is not read. On success the terminal prints locations assigned, unmatched, on a boundary, and borough-mismatched, and the process writes `outputs/data/traffic_monitoring_locations.geojson`, `outputs/data/traffic_with_uhf42.parquet`, `outputs/quality/spatial_join_audit.csv`, and `outputs/audit/exclusions_geo.csv`. Each monitoring location has one `uhf42_id` or an exclusion reason.
- run-tests: An interior fixture point joins to one polygon. The fixture test does not write `outputs/` and does not call Socrata.
done_when: An interior fixture prints the assigned count and exits 0, with one neighborhood per location. A non-interior fixture with an empty `lion_path` prints the unmatched, boundary, and mismatch counts and exits 2.

## Phase aggregate-traffic: Turn counts into neighborhood exposure
depends_on:
- assign-uhf
owns:
- lib/aggregate.py
- scripts/06_aggregate_traffic.py
- tests/test_aggregate_traffic.py
- tests/fixtures/aggregate_traffic/**
acceptance:
- aggregate-traffic: `python scripts/06_aggregate_traffic.py` takes no arguments. It reads `traffic_with_uhf42.parquet` and `source_selection.json` and processes one year at a time. `complete_hours` sums the four quarter-hour volumes for a key of monitoring location, local date, local hour, direction, and session. The session is `requestid` when present, otherwise the location, date, and direction. An hour with fewer than four distinct quarters is incomplete and stays out of the mean. A location that never shows a second direction keeps its hour. A location that shows two directions in the extract keeps an hour only when both directions are present, and that hour is the sum of the two. A missing direction is not stored as zero. `location_years` averages complete hours to a daily mean, then averages days to a location-year mean, and marks a location eligible only when `min_observation_dates` and `min_complete_hours` both hold. `segment_years` gives each `road_segment_id` one vote, the unweighted mean of its eligible locations. A location with no segment id votes as its own segment and is flagged. `neighborhood_periods` averages eligible segments with equal weight. For a pooled period it averages the neighborhood-year means with equal year weight and keeps a neighborhood only when every constituent year is eligible (`pool.require_all_years`). AADT skips the quarter-hour step. The primary exposure written to `traffic_mean_vehicles_per_hour` is that unweighted mean. `hour_balanced_mean` averages available clock-hour means, and averages weekday and weekend means when both exist, and is not the primary exposure. The script writes sampling distributions of hour, weekday, weekend, and month into `outputs/cleaning_log.md`. On success the terminal prints, per period, neighborhoods eligible and the median segment count, and the process writes `outputs/data/traffic_location_year.parquet`, `outputs/data/traffic_segment_year.parquet`, `outputs/data/traffic_uhf42_year.csv`, `outputs/quality/traffic_coverage_by_uhf42.csv`, and `outputs/audit/exclusions_aggregate.csv`. A selected period with zero eligible neighborhoods still writes the coverage rows, prints the period, and exits 0. A failure to build an hour from the audited quarters exits 2.
- run-tests: An hour sum requires four distinct quarters and is the sum of those volumes. A segment with two locations receives one vote. The fixture test does not write `outputs/` and does not call Socrata.
done_when: A fixture period prints the eligible neighborhood count and the median segment count and exits 0. The neighborhood mean equals the unweighted mean of eligible segment means. A pooled value equals the unweighted mean of the year means. A period with zero eligible neighborhoods exits 0.

## Phase merge-analysis: Join neighborhood traffic to asthma rates
depends_on:
- aggregate-traffic
owns:
- lib/merge.py
- scripts/07_merge.py
- tests/test_merge_analysis.py
- tests/fixtures/merge_analysis/**
acceptance:
- merge-analysis: `python scripts/07_merge.py` takes no arguments. `merge_uhf_period` outer-joins `cleaned_asthma.csv` to `traffic_uhf42_year.csv` on `uhf42_id` and `period`. Missing traffic stays null. Missing asthma stays null. The merged file has one row per neighborhood per period and at most 42 neighborhoods per period. Neighborhoods that fail the segment threshold, lack a rate, or lack traffic stay in the file with `coverage_status=insufficient` and an `exclusion_reason`. Rows that pass are `valid`. The script fills `n_matched` and `n_usable` on `outputs/dataset_source_comparison.csv` and leaves `recommended_source` unchanged. On success the terminal prints the usable n for each period, and the process writes `outputs/data/final_merged_dataset.csv` and `outputs/quality/missing_data_report.csv`, plus `outputs/audit/exclusions_merge.csv`. A period with usable n of 0 is printed and the process exits 0. A duplicate key or a count above 42 exits 2 and writes no merged file.
- run-tests: The merge rejects a duplicate neighborhood-period key. The fixture test does not write `outputs/` and does not call Socrata.
done_when: A fixture merge prints usable n for each period and exits 0. Null rates and null traffic stay null. A duplicate (`uhf42_id`, `period`) exits 2 and leaves no new merged file.

## Phase describe-sample: Describe the sample and write figures S1–S7
depends_on:
- merge-analysis
parallel_group: sample-and-hypotheses
owns:
- lib/describe.py
- lib/plots.py
- scripts/08_describe.py
- tests/test_describe_sample.py
- tests/fixtures/describe_sample/**
acceptance:
- describe-sample: `python scripts/08_describe.py` takes no arguments. It reads `final_merged_dataset.csv`, `traffic_uhf42_year.csv`, `traffic_monitoring_locations.geojson`, the local figure GeoJSON, and `source_selection.json`. `descriptive_tables` computes, for each period and for the whole usable sample, n, mean, median, standard deviation, minimum, maximum, range, Q1, Q3, IQR, and missing counts for the rate and the primary traffic mean, plus the distribution of `n_road_segments`. Borough and `coverage_status` get frequency, percentage, and mode. The primary period used for S1 and S2 is computed in `lib/describe.py`: `years.primary_preference` when that period is included and its usable n is at least `thresholds.min_uhf_per_period`; otherwise the most recent included period that meets the threshold. A period below `min_uhf_per_period` cannot become primary. The choice does not read p-values. `sample_figures` writes S1 through S7 as PNG and PDF under `outputs/figures/sample/` with the file names in the architecture data model. S1 and S2 display n, mean, median, and standard deviation in the subtitle. S1–S7 carry no p-value. S4 masks missing cells. S7 writes one map per included period. On success the terminal prints each figure path and the primary period used for S1 and S2. A figure with n of 0 is still written and annotated that no eligible neighborhood is available, and the process exits 0. A missing merged file exits 2. This phase does not import `lib/stats.py` and does not write `lib/plots.py` result charts.
- run-tests: The fixture test reads `tests/fixtures/describe_sample/` only, does not write `outputs/`, and does not call Socrata.
done_when: A fixture run prints the S1–S7 paths and the primary period and exits 0. S1 and S2 use the preference period when it qualifies, and an earlier period with a smaller p-value does not replace it. A figure with n of 0 exists and says no eligible neighborhood is available.

## Phase test-hypotheses: Test the correlation and compare periods
depends_on:
- merge-analysis
parallel_group: sample-and-hypotheses
owns:
- lib/stats.py
- scripts/09_test_hypotheses.py
- tests/test_hypotheses.py
- tests/fixtures/test_hypotheses/**
acceptance:
- test-hypotheses: `python scripts/09_test_hypotheses.py` takes no arguments. It reads `final_merged_dataset.csv` and `traffic_location_year.parquet`. The primary-period function in `lib/stats.py` selects `years.primary_preference` when that period is included and its usable n is at least `thresholds.min_uhf_per_period`; otherwise the most recent included period that meets the threshold. A period below the threshold is skipped with reason `below_min_uhf` and cannot become primary. The choice is recorded in `primary_reason` and does not look at p-values. `pearson_by_period` runs `scipy.stats.pearsonr` two-sided on usable rows, x = `traffic_mean_vehicles_per_hour`, y = `asthma_ed_rate_per_10000`, one test per included period. It records t = r * sqrt((n - 2) / (1 - r^2)), df = n - 2, and the 95% interval from `confidence_interval`. SciPy supplies the p-value. A relative gap larger than 1e-4 between the formula t and SciPy's t exits 2. n below 3, zero variance, or |r| = 1 writes `test_status=skipped` and a null p-value. Holm adjustment is applied only to secondary periods with `test_status=ok`. Alpha is 0.05. `spearman_by_period` repeats the periods with Spearman's rank correlation and does not replace the primary Pearson row. `sensitivity` reruns Pearson for leave-one-neighborhood-out on the primary period, for `thresholds.sensitivity_min_segments` and `thresholds.sensitivity_min_hours`, for the intersection of neighborhoods across included periods, and for the intersection of road segments re-aggregated from `traffic_location_year.parquet`. That re-aggregation lives in `lib/stats.py`. `compare_periods` writes direction, magnitude, whether the interval contains 0, sample overlap, and `measurement_consistency`. It does not pool neighborhood-years into one Pearson test. Bootstrap comparisons run only when `stats.bootstrap_comparisons` is true, resampling neighborhoods while keeping each neighborhood's paired periods together. On success the terminal prints the primary period, r, the confidence interval, and the p-value, then one line per secondary period, and the process writes `outputs/stats/pearson_results_by_year.csv`, `outputs/stats/spearman_results_by_year.csv`, `outputs/stats/sensitivity_analysis_results.csv`, and `outputs/stats/correlation_comparison_results.csv`. When every period is skipped, the terminal prints the skip reasons and the process exits 2. A skipped test has a null p-value. This phase does not import `lib/describe.py` or edit `lib/plots.py`.
- run-tests: The primary-period function ignores a smaller p-value on an earlier year. The fixture test does not write `outputs/` and does not call Socrata.
done_when: A fixture with a qualifying preference year prints that period, r, the interval, and the p-value and exits 0. One Pearson row exists per included period. An earlier year with a smaller p-value stays secondary. When every period is skipped, the terminal prints the reasons and the process exits 2.

## Phase write-report: Write the workbook, result figures, and both narratives
depends_on:
- describe-sample
- test-hypotheses
owns:
- lib/plots.py
- lib/report.py
- scripts/10_report.py
- tests/test_write_report.py
- tests/fixtures/write_report/**
acceptance:
- write-report: `python scripts/10_report.py` takes no arguments. It reads the stat tables, the merged dataset, the comparison table, the exclusion logs, and `source_selection.json`. `result_figures` adds R1–R4 to `lib/plots.py` and writes PNG and PDF under `outputs/figures/results/`. R1 is the primary period. R3 is every tested period. Both statistical charts show n, r, and the exact p-value. R2 uses the most recent included period that is not the primary, on the same axis units as R1 when the ranges allow a shared scale. R4 plots Pearson and Spearman side by side and labels the test on each point. When at least one Pearson row has `test_status=ok`, R1 and R3 exist. When only one period was tested, R2 is omitted, the summary says so, and the process exits 0. `workbook` writes `outputs/research_results.xlsx` with sheets in this order: Year and Source Coverage, Matched Dataset, Sample Characteristics, Descriptive Statistics, Pearson Correlations, Spearman Sensitivity, Correlation Comparisons, Missing Data and Exclusions, Data Dictionary. Dictionary rows are `file`, `column`, `type`, `meaning`, `units`. `compile_exclusions` concatenates the exclusion logs into `outputs/quality/excluded_observations.csv`. `narratives` writes `outputs/results_summary.md` and `outputs/interpretation_guide.md` from the CSV values and a fixed sentence bank. The summary has four parts: the question and hypotheses; sources, sample size, variables, and descriptive statistics; the primary numbers; and the test justification with p-values and the file names of R1 and R3. The interpretation guide defines n, r, the p-value, the confidence interval, the directional reading, cross-period consistency, and the ecological limit, using the actual numbers. Every r and p in the markdown matches the Pearson CSV. The summary's third section states the numbers. The files contain no causal claim and no literature comparison. The script does not rewrite `README.md`. On success the terminal prints the workbook path and the figure paths. When no Pearson test has `test_status=ok`, the summary states the limitation and the process exits 2.
- run-tests: The fixture test reads `tests/fixtures/write_report/` only, does not write `outputs/`, and does not call Socrata.
done_when: A fixture with one `test_status=ok` Pearson row prints the workbook path and the R1 and R3 paths and exits 0. The workbook has the nine sheets in order. The r and p in `results_summary.md` match `pearson_results_by_year.csv`. A fixture with no ok test writes the limitation statement and exits 2.
