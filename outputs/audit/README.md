# Audit tables

These files record the decisions that sit in front of the neighborhood comparison: which asthma periods exist, how many traffic records each source has, which source was chosen, whether the download matched the audit count, and which rows were dropped before a neighborhood mean was formed. The Pearson test does not read this folder. It reads `outputs/data/final_merged_dataset.csv`. The combined drop list is `outputs/quality/excluded_observations.csv`, built by stacking the four exclusion CSVs here.

The study compares, for New York City’s 42 United Hospital Fund (UHF42) neighborhoods, the neighborhood mean of sampled hourly vehicle counts with the annual asthma emergency-department visit rate for children ages 4 and under. Target years are 2015, 2016, 2017, 2018, 2019, 2022, and 2023. 2020 and 2021 are excluded. A neighborhood needs at least 30 valid peers in a year before that year can be tested (`thresholds.min_uhf_per_period`). A neighborhood is adequate for source selection when it has at least one eligible road segment. An eligible location needs at least two distinct local dates and one complete hour. A complete automated hour is the sum of the quarter-hour volumes at minutes 0, 15, 30, and 45.

The counts below match this folder from the current pipeline run. Rerunning `scripts/01_audit_sources.py` through `scripts/07_merge.py` rewrites these files. This note is not rewritten by those scripts.

Pipeline order:

1. `asthma_time_periods.csv`, `traffic_year_counts.csv`, `schema_snapshot.json` — `scripts/01_audit_sources.py`
2. `source_selection.json`, `coverage_for_selection.csv` — `scripts/02_select_sources.py`
3. `download_log.csv` — `scripts/03_download_traffic.py`
4. `exclusions_clean.csv` — `scripts/04_clean.py`
5. `exclusions_geo.csv` — `scripts/05_assign_uhf42.py`
6. `exclusions_aggregate.csv` — `scripts/06_aggregate_traffic.py`
7. `exclusions_merge.csv` — `scripts/07_merge.py`

## `asthma_time_periods.csv`

One row per asthma period kept from the Environment and Health Data Portal export, 7 rows. `scripts/01_audit_sources.py` reads the single CSV in `data/raw/asthma`, keeps the original `TimePeriod` label, and classifies it. A label that is one four-digit year is `year` when that year is in `years.targets`. A label that is an inclusive range of target years would be `pooled`, and the pipeline would keep one rate for the whole range rather than splitting it into annual rows. A year in `years.excluded` (2020, 2021), a range that touches an excluded year, or any other label is dropped and does not appear here.

Every kept label in this run is a single year, `period_kind` is `year`, `constituent_years` is that same year, and `n_rows` is 42. The export has one row for each UHF42 neighborhood in each study year: 294 asthma rows in total. There is no pooled period, so each correlation later in `outputs/stats/` is a single-year cross-section.

Columns:

- `period` — the original `TimePeriod` string. Here it is `2015`, `2016`, `2017`, `2018`, `2019`, `2022`, or `2023`.
- `period_kind` — `year` or `pooled`. All seven rows are `year`.
- `constituent_years` — the calendar years inside the label, comma-separated if a label were pooled. Each row holds one year.
- `n_rows` — neighborhoods in the export for that period. 42 on every row, which is the full UHF42 set.

## `traffic_year_counts.csv`

One row per source per target year, 21 rows (3 sources × 7 years). The audit asks each Socrata API for `count(*)` of that year and writes the count. It does not download volumes. `schema_ok` is true when the live schema has a year field, or a date field the audit can turn into a year. All 21 rows are `schema_ok` true.

| Year | Automated (`7ym2-wayt`) | Historical (`btm5-ppia`) | AADT (`6amx-2pbv`) |
| --- | ---: | ---: | ---: |
| 2015 | 124,428 | 5,921 | 60,321 |
| 2016 | 129,617 | 3,169 | 60,321 |
| 2017 | 123,901 | 5,163 | 60,321 |
| 2018 | 98,961 | 1,048 | 60,321 |
| 2019 | 115,730 | 5,841 | 60,321 |
| 2022 | 78,652 | 0 | 0 |
| 2023 | 85,106 | 0 | 0 |

Automated counts are quarter-hour volume rows from NYC DOT’s automated counters. The yearly totals fall from about 125,000 rows in 2015–2017 to 78,652 in 2022 and 85,106 in 2023. Historical counts are the older DOT count file. That API has rows in 2015–2019, including only 1,048 in 2018, and none in 2022 or 2023. AADT is annual average daily traffic from the state feed. The audit count is 60,321 in each year from 2015 through 2019 and 0 in 2022 and 2023. Those AADT figures are row counts of the station file, not vehicles per hour. The sample row in `schema_snapshot.json` shows one station whose `count` field is 81,839, which is that station’s AADT.

This table is how the pipeline knows a source has any records before it spends a download on them. Source selection still has to turn records into neighborhood coverage. A large row count in a year that cannot be placed in 30 neighborhoods would not be chosen.

Columns:

- `source` — `automated`, `historical`, or `aadt`.
- `year` — a target year.
- `record_count` — rows the API reported for that source and year.
- `schema_ok` — true when the year filter could be built from the live columns.

## `schema_snapshot.json`

The live column names and one example row from each of the three APIs, captured by `fetch_schema` with `$limit=1`. Later scripts resolve config aliases against these names. The first alias that is actually in the live keys wins. This file is the record of what the APIs looked like when the audit ran.

Automated (`7ym2-wayt`) is a quarter-hour feed. The columns used downstream are `requestid`, `boro`, `yr`, `m`, `d`, `hh`, `mm`, `vol`, `segmentid`, `wktgeom`, and `direction`. The sample geometry is a State Plane point (`POINT (1035363.4 185093.4)`), which is why every monitoring location later has `source_crs` `EPSG:2263`. `street`, `fromst`, and `tost` are in the snapshot and are not part of the exposure.

Historical (`btm5-ppia`) is one count session per row, with a `date` and 24 hour-of-day columns such as `_12_00_1_00_am` through `_11_00_12_00am`. It has `segmentid` and direction, and the snapshot columns do not include a WKT field. It was not downloaded. Automated coverage was already adequate for every asthma year, so `scripts/02_select_sources.py` never paged this API.

AADT (`6amx-2pbv`) is a station-year file: `aadt_year`, `station_id`, `county`, route fields, and `count`. The sample station is on Interstate 95 in the Bronx with `count` 81,839. Hourly exposure from this source would be `count / 24`. AADT is never mixed into an automated or historical series. It was not scored, because automated coverage was already sufficient.

## `source_selection.json`

The source decision for this run, written by `select_source` after automated coverage was scored. Asthma rates are not an input. The rule is: if automated coverage is adequate in every asthma period, use automated for all of them (scenario A). Historical is paged only when automated leaves a gap. AADT is paged only when neither automated nor historical covers any period. A period that both automated and historical could cover stays on automated. Two sources are mixed only when a traffic-versus-traffic Pearson correlation on the overlap year is at least 0.8 and the overlap has at least 30 neighborhoods. That check does not read asthma rates.

This file is scenario A:

- `scenario` is `A`.
- `mix_used` is false. Historical was not added to any year.
- `comparability_pass`, `comparability_r`, and `comparability_n` are null. The overlap check never ran, because there was no gap to fill.
- `periods` has seven objects, one per asthma year. Each has `source` `automated`, `years` equal to that single year, `included` true, and a blank `reason`.

No period was dropped. 2020 and 2021 never entered the period list. The primary year is chosen later, in the stats step, and this file does not contain a p-value.

## `coverage_for_selection.csv`

One row per neighborhood that the selection scorer could place in a period, 262 rows, all `source` = `automated`. `scripts/02_select_sources.py` pages a coverage extract of the automated API (id, date parts, volume, segment, WKT, direction, borough), assigns points to UHF42 polygons, and applies the same hour, direction, and threshold rules used later in aggregation. A neighborhood is `adequate` when `n_eligible_segments` is at least `min_segments_per_uhf` (1). A year is coverable when at least 30 neighborhoods are adequate. Every year cleared that bar, which is why the selection file is scenario A and why historical and AADT have no rows here.

| Period | Rows | Adequate |
| --- | ---: | ---: |
| 2015 | 40 | 37 |
| 2016 | 40 | 39 |
| 2017 | 39 | 35 |
| 2018 | 37 | 36 |
| 2019 | 39 | 35 |
| 2022 | 32 | 30 |
| 2023 | 35 | 32 |

2022 is the year that sits on the floor: 30 adequate neighborhoods.

This file is the coverage that chose the source. It is not the coverage in the final correlation. After the full download, the cleaner drops minutes that are not 0, 15, 30, or 45, and the spatial join drops locations outside a polygon, on a boundary, or in a borough that disagrees with the polygon. Those steps are `exclusions_clean.csv` and `exclusions_geo.csv`. The analysis coverage is `outputs/data/traffic_uhf42_year.csv` and `outputs/quality/traffic_coverage_by_uhf42.csv` (256 rows, 244 adequate).

Comparing the two coverage files on this run:

- 256 neighborhood-periods appear in both. The adequate flag agrees on all 256. No neighborhood that was adequate at selection became inadequate after cleaning, and none of the shared inadequate rows became adequate.
- 25 of those 256 have fewer eligible segments in the final file than they had at selection, and fewer complete hours. Each of them still has at least one eligible segment, so the flag stays adequate. The loss is the off-quarter minutes and the locations the spatial join refused.
- 6 neighborhood-periods appear only in this selection file, all with `n_eligible_segments` = 0 and `adequate` false: UHF 205, 302, and 404 in 2017; UHF 303 in 2019; UHF 407 and 410 in 2022. They did not count toward the 30-neighborhood bar. They are absent from the final traffic table.

Columns:

- `period` — asthma period.
- `uhf42_id` — neighborhood code. 101–107 Bronx, 201–211 Brooklyn, 301–310 Manhattan, 401–410 Queens, 501–504 Staten Island.
- `n_eligible_segments` — segments with an eligible location-year at selection time.
- `n_locations` — locations rolled into that neighborhood-period by the scorer.
- `n_complete_hours` — complete hours the scorer counted.
- `adequate` — true when the segment count meets the minimum of 1.
- `source` — `automated` on every row.

`scripts/02_select_sources.py` also writes `outputs/dataset_source_comparison.csv`, beside this folder. At selection time that comparison table records the recommended source and leaves `n_matched` and `n_usable` blank. `scripts/07_merge.py` fills those two counts and leaves `recommended_source` as `automated`.

## `download_log.csv`

One row per downloaded source-year, 7 rows. Only automated years were downloaded, because selection included only that source. `scripts/03_download_traffic.py` pages the full automated extract for each year, checks the row count against `traffic_year_counts.csv`, and records a SHA-256 of the raw parquet.

On every row, `downloaded_count` equals `expected_count` and `duplicate_ids` is 0. The download matches the audit count exactly:

| Year | Rows | Duplicate `:id` |
| --- | ---: | ---: |
| 2015 | 124,428 | 0 |
| 2016 | 129,617 | 0 |
| 2017 | 123,901 | 0 |
| 2018 | 98,961 | 0 |
| 2019 | 115,730 | 0 |
| 2022 | 78,652 | 0 |
| 2023 | 85,106 | 0 |

The seven raw parquets sum to 756,395 rows. `parquet_sha256` is the checksum of that year’s raw file. A later run that writes a different checksum has not reproduced this download.

Columns:

- `source` — `automated`.
- `year` — target year.
- `expected_count` — `record_count` from `traffic_year_counts.csv`.
- `downloaded_count` — rows written to the raw parquet.
- `duplicate_ids` — repeated Socrata `:id` values. A nonzero count would have stopped the download.
- `parquet_sha256` — checksum of the raw year file.

The 2022 file includes 2,871 rows whose minute is an integer other than 0, 15, 30, or 45. Those rows are in this download log and are removed in `exclusions_clean.csv`, so the cleaned 2022 table has 75,781 rows. The other six years pass through cleaning at the same row count as this log.

## `exclusions_clean.csv`

2,871 rows, one per automated record removed by `scripts/04_clean.py`. Every row is `stage` = `clean`, `reason` = `minute_not_quarter`, `source` = `automated`. `key` is the Socrata `:id` of that quarter-hour record.

`minute_not_quarter` means the minute parsed as an integer and was not 0, 15, 30, or 45, so the row cannot enter a four-quarter hour. All 2,871 of them are inside the 2022 download. The cleaner keeps a year that contains some off-quarter minutes. A minute that cannot be parsed would have stopped the script instead of writing a row here.

This run has no `duplicate_row` and no `negative_volume` rows. Exact duplicate records would be removed and negative volumes would be removed. Zero volumes are kept. Counts above the within-year 99.9th percentile are flagged and kept. The cleaning log records 750 of those high counts, and they are not in this exclusion file. They remain in `outputs/data/cleaned_traffic.parquet` with `high_count_flag` true.

Columns, shared with the other three exclusion logs:

- `stage` — `clean`.
- `key` — Socrata `:id`.
- `reason` — `minute_not_quarter`.
- `source` — `automated`.

## `exclusions_geo.csv`

100 rows, one per monitoring location that `scripts/05_assign_uhf42.py` refused. `key` is `monitoring_location_id`, the SHA-1 of the segment id and the point rounded to 1 meter in State Plane. `source` is `automated`. The same 100 locations are the non-interior rows of `outputs/quality/spatial_join_audit.csv` and of `outputs/data/traffic_monitoring_locations.geojson`. Their volume rows are absent from `outputs/data/traffic_with_uhf42.parquet`.

| Reason | Locations | What the point did |
| --- | ---: | --- |
| `outside_nyc` | 47 | Fell in no UHF42 polygon and sat more than 1 meter from every edge. |
| `boundary` | 46 | Sat within 1 meter of an edge, or inside more than one polygon. |
| `borough_mismatch` | 7 | Fell inside exactly one polygon whose borough disagreed with the borough on the traffic record. The neighborhood id is left blank so the point cannot vote for the wrong neighborhood. |

`geography.lion_path` is empty, so the LION street file is not used to reassign these points. The script still exits 0. These locations are omitted, and the interior locations continue.

## `exclusions_aggregate.csv`

259 rows. `scripts/06_aggregate_traffic.py` writes one row for each location-year that produced a traffic summary and failed the eligibility rule. `stage` is `aggregate`, `reason` is `below_threshold`, `source` is `automated`, and `key` is `monitoring_location_id`. All 259 keys are distinct.

A location-year is eligible only when it has at least two distinct local dates and at least one complete hour. The hour floor is 1, so a location-year that already has a complete hour fails here because its hours fall on a single date. Those hours can still appear as `n_complete_hours` on an inadequate neighborhood in `outputs/data/traffic_uhf42_year.csv`. They do not become a segment vote, and they are not stored as zero vehicles.

The matching location table has 1,214 location-years, of which 955 are eligible and these 259 are not. See `outputs/data/traffic_location_year.parquet`.

## `exclusions_merge.csv`

50 rows, one per neighborhood-period that stays in the merged file with `coverage_status` = `insufficient`. `stage` is `merge`, `reason` is `missing_traffic`, and `key` is `uhf42_id|period` (for example `101|2022`).

A traffic mean is missing for 50 of the 294 neighborhood-years. The asthma rate is present for all 294. Twelve of the 50 have `source` = `automated`: a traffic row existed, and every location in that neighborhood-period failed the date rule, so the mean was left null. The other 38 have a blank `source`: that neighborhood-period never received a traffic row after the spatial join. The 12 are the `coverage_status` = `insufficient` rows in `outputs/data/traffic_uhf42_year.csv`. The 38 are the neighborhood-years absent from that file.

Usable n by year, 42 minus these 50, is 37, 39, 35, 36, 35, 30, and 32 for 2015 through 2023. Those are the sample sizes in `outputs/stats/pearson_results_by_year.csv`. The blank `exclusion_reason` on the other 244 merged rows means the rate, the traffic mean, and the segment rule were all present. Those 244 rows are the ones the correlations use.
