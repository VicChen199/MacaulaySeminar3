# Quality tables

These four CSVs record whether each traffic count could be placed in a neighborhood and whether each neighborhood had enough traffic to enter the asthma comparison. They are audit files. The Pearson test does not read them directly. It reads the rows in `outputs/data/final_merged_dataset.csv` that these checks marked `valid`.

The study asks, for New York City’s 42 United Hospital Fund (UHF42) neighborhoods, how the neighborhood mean of sampled hourly vehicle counts lines up with the annual asthma emergency-department visit rate for children ages 4 and under. The years in this run are 2015, 2016, 2017, 2018, 2019, 2022, and 2023. 2020 and 2021 are left out. Every included period uses NYC DOT automated traffic counts. A complete hour is the sum of the four quarter-hour volumes at minutes 0, 15, 30, and 45. A location-year mean averages those complete hours within each day, then averages the days. A neighborhood mean is the unweighted mean of its eligible road-segment means. A missing count stays missing. It is never stored as zero vehicles.

The counts below match the CSVs in this folder from the current pipeline run. Running `scripts/05_assign_uhf42.py` through `scripts/10_report.py` again rewrites the CSVs. This note is not rewritten by those scripts.

Pipeline order:

1. `spatial_join_audit.csv` — `scripts/05_assign_uhf42.py`
2. `traffic_coverage_by_uhf42.csv` — `scripts/06_aggregate_traffic.py`
3. `missing_data_report.csv` — `scripts/07_merge.py`
4. `excluded_observations.csv` — `scripts/10_report.py`, stacked from the earlier exclusion logs

## `spatial_join_audit.csv`

One row per distinct automated-count monitoring location, 1,486 rows. A location is one point, built from the count’s WKT, not one row per 15-minute volume. `monitoring_location_id` is the SHA-1 of the road-segment id, the point’s State Plane x rounded to 1 meter, and the point’s State Plane y rounded to 1 meter.

`scripts/05_assign_uhf42.py` projects the points and the DOHMH UHF42 polygons to EPSG:2263 and tests whether each point falls inside a polygon. The boundary id on the shapefile is matched to the asthma file’s `uhf42_id`. A point more than 1 meter from every polygon edge, and inside exactly one polygon, whose traffic borough agrees with that polygon, is `interior` and keeps that `uhf42_id`. Those 1,386 locations are the only ones copied into `outputs/data/traffic_with_uhf42.parquet`. The other 100 locations are listed here and dropped. `geography.lion_path` is empty, so the LION street file is not used to rescue them.

| `assignment` | `rule` | Rows | What happened |
| --- | --- | --- | --- |
| `interior` | `within` | 1,386 | Inside one UHF42 polygon, boroughs agree, `uhf42_id` filled. |
| `outside_nyc` | `outside` | 47 | In no polygon and farther than 1 meter from every edge. `uhf42_id` blank. |
| `boundary` | `boundary` | 46 | Within 1 meter of an edge, or inside more than one polygon. `uhf42_id` blank. |
| `borough_mismatch` | `borough` | 7 | Inside exactly one polygon, but the borough on the traffic record disagrees with the polygon borough. `uhf42_id` blank so the point cannot vote for the wrong neighborhood. |

Columns:

- `monitoring_location_id` — stable id for the point. The same id is the `key` on the `assign-uhf` rows of `excluded_observations.csv`.
- `assignment` — `interior`, `outside_nyc`, `boundary`, or `borough_mismatch`.
- `uhf42_id` — neighborhood code when the point is interior. Blank for the 100 excluded locations. Codes run 101–107 (Bronx), 201–211 (Brooklyn), 301–310 (Manhattan), 401–410 (Queens), and 501–504 (Staten Island).
- `borough_traffic` — borough label on the traffic record after the alias table in `config/pipeline.toml`.
- `borough_polygon` — borough of the containing polygon. Blank when the point is outside the city or hits more than one polygon.
- `rule` — the short name of the decision: `within`, `outside`, `boundary`, or `borough`.

This file answers a geography question: did the counter land in the same neighborhood the asthma rate describes? It does not say whether that counter was sampled often enough to represent the neighborhood. That question is the next file.

## `traffic_coverage_by_uhf42.csv`

One row per neighborhood that had any aggregated traffic in a study period, 256 rows. It is written from `outputs/data/traffic_uhf42_year.csv`. A neighborhood with no counter in that period is absent here. The full 42-by-7 grid is in `missing_data_report.csv`, which has 294 rows. The 38 neighborhood-periods missing from this file are the merge rows whose traffic source is blank.

`adequate` is true when that neighborhood-period is `valid` in the neighborhood traffic table. For these single-year periods, valid means at least `min_segments_per_uhf` eligible segments. That threshold is 1. A segment is eligible when at least one of its locations has at least `min_observation_dates` distinct local dates (2) and at least `min_complete_hours` complete hours (1). Both cuts apply together. Hours that are missing a direction at a two-way location never become a complete hour, and a missing direction is not filled with zero.

| Period | Rows in this file | Adequate | Short of the segment rule |
| --- | --- | --- | --- |
| 2015 | 40 | 37 | 3 |
| 2016 | 40 | 39 | 1 |
| 2017 | 36 | 35 | 1 |
| 2018 | 37 | 36 | 1 |
| 2019 | 38 | 35 | 3 |
| 2022 | 30 | 30 | 0 |
| 2023 | 35 | 32 | 3 |

All 256 rows have `source` = `automated`. Source selection kept automated counts for every asthma year in this run. Historical counts and AADT are not mixed into these means.

The 12 rows with `adequate` false all have `n_eligible_segments` = 0 and `n_locations` = 0, while `n_complete_hours` is still positive. Counters in that neighborhood produced some complete hours, and every one of those location-years failed the two-date rule, so the neighborhood mean was left empty. Those hours are the `aggregate` / `below_threshold` rows in the exclusion log. They are not a traffic volume of zero.

| Period | UHF | Neighborhood | Complete hours that did not qualify |
| --- | --- | --- | --- |
| 2015 | 204 | East New York, Brooklyn | 1 |
| 2015 | 306 | Chelsea–Clinton, Manhattan | 2 |
| 2015 | 503 | Willowbrook, Staten Island | 5 |
| 2016 | 310 | Lower Manhattan | 19 |
| 2017 | 208 | Canarsie–Flatlands, Brooklyn | 8 |
| 2018 | 406 | Fresh Meadows, Queens | 6 |
| 2019 | 104 | Pelham–Throgs Neck, Bronx | 48 |
| 2019 | 308 | Greenwich Village–SoHo, Manhattan | 5 |
| 2019 | 404 | Bayside–Little Neck, Queens | 48 |
| 2023 | 304 | Upper West Side, Manhattan | 16 |
| 2023 | 502 | Stapleton–St. George, Staten Island | 24 |
| 2023 | 504 | South Beach–Tottenville, Staten Island | 24 |

Pelham–Throgs Neck and Bayside–Little Neck in 2019 each have 48 complete hours and still no eligible segment, because those hours did not cover two distinct dates at a location that could vote. Lower Manhattan in 2016 is the same pattern with 19 hours.

Columns:

- `period` — asthma period label. In this run each label is one calendar year.
- `uhf42_id` — UHF42 neighborhood code.
- `n_eligible_segments` — road segments whose location-year mean was allowed to vote. This is the same number stored as `n_road_segments` on the neighborhood traffic row. For a pooled period it would be the minimum across the pooled years. Every period here is a single year.
- `n_locations` — eligible monitoring locations, the ones that met the date and hour rule and could vote. It is 0 on every inadequate row because no location in that neighborhood-period qualified.
- `n_complete_hours` — complete hours summed across locations, including locations that failed the date rule. On an inadequate row this count is positive and `n_locations` is still 0: hours were counted, and none of those locations spanned two distinct dates, so none became a segment vote.
- `adequate` — `True` when the neighborhood receives a traffic mean and can be `valid` after the asthma merge. `False` when the segment rule fails.
- `source` — count system that supplied the hours. `automated` throughout this file.

`adequate` is a traffic-coverage flag. It becomes a usable analysis row only after the merge also finds an asthma rate. In this run every neighborhood-period has a rate, so the 244 adequate rows are exactly the 244 valid rows.

## `missing_data_report.csv`

One row for every neighborhood in every study period, 294 rows (42 × 7). `scripts/07_merge.py` outer-joins `outputs/data/cleaned_asthma.csv` to `outputs/data/traffic_uhf42_year.csv` on `uhf42_id` and `period`, then writes this thinner report beside `outputs/data/final_merged_dataset.csv`. Nulls stay null. The merge refuses the run if any period has a duplicate neighborhood or more than 42 neighborhoods.

A row is usable when it has an asthma rate, a traffic mean, and at least one eligible segment. Usable rows have a blank `exclusion_reason`. They are the rows with `coverage_status` = `valid` in the merged file, and they are the pairs passed to Pearson and Spearman.

| `exclusion_reason` | Rows | Meaning in this run |
| --- | --- | --- |
| blank | 244 | Rate and traffic mean both present, segment rule met. These are the analysis rows. |
| `missing_traffic` | 50 | Asthma rate present, traffic mean absent. |
| `missing_rate` | 0 | The asthma export has a rate for every neighborhood-year in the window. |
| `segment_threshold` | 0 | No row had both values and still fell under one eligible segment. The 12 short-coverage neighborhoods are already inside `missing_traffic`, because a failed segment rule stores a null mean rather than a zero. |
| `missing_rate;missing_traffic` | 0 | No neighborhood-period is missing both. |

`missing_traffic` is true on those same 50 rows and false on the other 244. `missing_rate` is false on every row. By period, neighborhoods without a traffic mean are 5 in 2015, 3 in 2016, 7 in 2017, 6 in 2018, 7 in 2019, 12 in 2022, and 10 in 2023. Usable n is 42 minus that count: 37, 39, 35, 36, 35, 30, and 32. Those are the sample sizes in `outputs/stats/pearson_results_by_year.csv`. The smallest usable year is 2022, which sits on the configured floor of 30 neighborhoods per period (`thresholds.min_uhf_per_period`). A year under 30 would be skipped and could not be the primary period.

Of the 50 missing-traffic rows, 38 have no traffic row at all (they are absent from `traffic_coverage_by_uhf42.csv`). The other 12 are the inadequate rows in that coverage file. The split is visible in `excluded_observations.csv`: 38 merge keys have a blank `source`, and 12 have `source` = `automated`.

Columns:

- `period` — asthma period.
- `uhf42_id` — neighborhood code.
- `missing_rate` — true when `asthma_ed_rate_per_10000` is null.
- `missing_traffic` — true when `traffic_mean_vehicles_per_hour` is null.
- `exclusion_reason` — blank, `missing_rate`, `missing_traffic`, `segment_threshold`, or `missing_rate;missing_traffic` when both values are null.

This report is the roster of who entered the correlation. A neighborhood absent from a year’s Pearson test is a neighborhood whose traffic mean is null, not a neighborhood whose traffic was imputed.

## `excluded_observations.csv`

One row per dropped observation, 3,280 rows. `scripts/10_report.py` concatenates the stage logs in `outputs/audit/` into this single file. Column order is `stage`, `key`, `reason`, `source`. The same rows are the “Missing Data and Exclusions” sheet of `outputs/research_results.xlsx`. A row here was removed from the path that builds the neighborhood mean, or removed at the merge. The 244 valid neighborhood-years do not appear.

| Stage | Reason | Rows | What `key` is |
| --- | --- | --- | --- |
| `clean` | `minute_not_quarter` | 2,871 | Socrata row id (`:id`) of an automated count. |
| `assign-uhf` | `outside_nyc` | 47 | `monitoring_location_id`. |
| `assign-uhf` | `boundary` | 46 | `monitoring_location_id`. |
| `assign-uhf` | `borough_mismatch` | 7 | `monitoring_location_id`. |
| `aggregate` | `below_threshold` | 259 | `monitoring_location_id` of a location-year that was not eligible. All 259 keys are unique, and every source is `automated`. |
| `merge` | `missing_traffic` | 50 | `uhf42_id\|period`, for example `101\|2022`. |

Columns:

- `stage` — pipeline step that dropped the row: `clean`, `assign-uhf`, `aggregate`, or `merge`.
- `key` — id of the dropped thing. The id’s meaning depends on the stage, as in the table above.
- `reason` — why it was dropped.
- `source` — count system. `automated` on the clean, assign, and aggregate rows. On a merge row, `automated` means a traffic row existed and the mean was still null (the 12 inadequate neighborhoods). A blank source means that neighborhood-period never received a traffic row (the other 38).

How to read each reason in this project:

- `minute_not_quarter` — the minute parsed as an integer and was not 0, 15, 30, or 45. That 15-minute record is left out of the four-quarter sum. The year stays usable. A minute that cannot be parsed would have stopped `scripts/04_clean.py` instead of writing a row here. This run has no `duplicate_row` and no `negative_volume` exclusions. Those reasons exist in the cleaner and did not occur.
- `outside_nyc`, `boundary`, `borough_mismatch` — the same 100 locations as the non-interior rows of `spatial_join_audit.csv`. Their volumes never reach a neighborhood mean.
- `below_threshold` — the location has a year row and failed the joint rule of 2 distinct local dates and 1 complete hour. With the hour floor set to 1, these are location-years that did not span two dates. Their hours can still show up as `n_complete_hours` on an inadequate coverage row, and they do not become a segment vote.
- `missing_traffic` — the neighborhood-period has an asthma rate and no traffic mean, either because no eligible counter fell in it or because every counter there failed the date-and-hour rule. These 50 keys are the non-blank `exclusion_reason` values in `missing_data_report.csv`.

Reading the file from the bottom of the pipeline upward: 2,871 off-quarter minutes never formed an hour, 100 locations never received a neighborhood, 259 location-years formed hours and still could not vote, and 50 neighborhood-years therefore had no exposure to pair with the child asthma rate. The correlations in `outputs/stats/` are estimated on the neighborhood-years that remained.
