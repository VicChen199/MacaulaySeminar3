# Data tables

These files are the analysis dataset, from the cleaned asthma export and the cleaned automated counts through the neighborhood file the correlations read. Three of them are CSV. The traffic extracts are Parquet, and the monitoring locations are a GeoJSON, because those tables are larger than a spreadsheet and the locations carry point geometry. This note covers every file in the folder.

The study asks how the UHF42 neighborhood mean of sampled hourly vehicle counts lines up with the annual asthma emergency-department visit rate for children ages 4 and under. The years are 2015, 2016, 2017, 2018, 2019, 2022, and 2023. Every included period uses NYC DOT automated counts. A complete hour is the sum of the volumes at minutes 0, 15, 30, and 45 for one monitoring location, local date, clock hour, direction, and count session. The location-year mean averages complete hours within each day, then averages those days. The segment-year mean averages the eligible locations on that segment, each location weighted equally. The neighborhood mean averages the eligible segments, each segment weighted equally. A missing mean stays missing. It is never written as zero vehicles.

Pearson, in `outputs/stats/pearson_results_by_year.csv`, reads `traffic_mean_vehicles_per_hour` and `asthma_ed_rate_per_10000` from `final_merged_dataset.csv`, and only the rows with `coverage_status` = `valid`.

The counts below match this folder from the current pipeline run. Rerunning `scripts/04_clean.py` through `scripts/07_merge.py` rewrites these files. This note is not rewritten by those scripts.

Pipeline order:

1. `cleaned_asthma.csv` and `cleaned_traffic.parquet` — `scripts/04_clean.py`
2. `traffic_monitoring_locations.geojson` and `traffic_with_uhf42.parquet` — `scripts/05_assign_uhf42.py`
3. `traffic_location_year.parquet`, `traffic_segment_year.parquet`, and `traffic_uhf42_year.csv` — `scripts/06_aggregate_traffic.py`
4. `final_merged_dataset.csv` — `scripts/07_merge.py`

## `cleaned_asthma.csv`

One row per neighborhood per study year, 294 rows (42 × 7). `scripts/04_clean.py` reads the single Environment and Health Data Portal export in `data/raw/asthma` and renames the portal headers: `Geography` to `uhf42_name`, `Borough` to `borough`, `Number` to `asthma_ed_count`, and `Estimated annual rate per 10,000` to `asthma_ed_rate_per_10000`. `GeoID` becomes `uhf42_id` with leading zeros removed. `TimePeriod` becomes `period`.

The portal measure is asthma emergency-department visits among children ages 4 and under. The rate is visits per 10,000 children in that age group. A suppressed cell (`*`, `s`, or `suppressed`) would be stored as null. This export has no suppressed cells: every count and every rate is filled. Counts run from 11 to 1,547 visits. Rates run from 29.7 to 876.5 per 10,000. There are no duplicate neighborhood-period keys. A duplicate key, or two period labels that would put the same neighborhood-year in two analysis periods, would have stopped cleaning.

Every `period_kind` is `year`, and `constituent_years` is that same year. The period inventory is `outputs/audit/asthma_time_periods.csv`. Neighborhoods by borough: Bronx 7 (UHF 101–107), Brooklyn 11 (201–211), Manhattan 10 (301–310), Queens 10 (401–410), Staten Island 4 (501–504).

Columns:

- `uhf42_id` — neighborhood code, a string.
- `uhf42_name` — portal neighborhood name, such as `Kingsbridge - Riverdale`.
- `borough` — Bronx, Brooklyn, Manhattan, Queens, or Staten Island.
- `period` — study year.
- `period_kind` — `year` in this file. A multi-year portal label that survived the period rule would be `pooled`.
- `constituent_years` — the calendar year inside the label.
- `asthma_ed_count` — number of ED visits. Null if the portal suppressed the cell.
- `asthma_ed_rate_per_10000` — estimated annual rate per 10,000 children ages 4 and under. This is the outcome column. Null rates are left null.

## `cleaned_traffic.parquet`

753,524 quarter-hour rows, one per automated count that survived cleaning. The seven raw year files in the download log sum to 756,395 rows. The difference is 2,871 rows, all from 2022, removed because the minute was not 0, 15, 30, or 45. Those keys are `outputs/audit/exclusions_clean.csv`. The other six years have the same row count here as in the download log.

| Year | Cleaned rows |
| --- | ---: |
| 2015 | 124,428 |
| 2016 | 129,617 |
| 2017 | 123,901 |
| 2018 | 98,961 |
| 2019 | 115,730 |
| 2022 | 75,781 |
| 2023 | 85,106 |

`source` is `automated` on every row. `minute` takes only the values 0, 15, 30, and 45. `vol` is a vehicle count for that quarter-hour. None are negative, none are null, and 29,489 are zero. Zero is a real count and stays in the file. `high_count_flag` is true for 750 rows, the volumes above that year’s 99.9th percentile (125, 129, 123, 98, 115, 75, and 85 by year from 2015 through 2023). Those rows are flagged and kept. They are the “invalid counts flagged 750” line in `outputs/cleaning_log.md`.

`hour_complete` is false on all 753,524 rows. For an automated extract the flag means the row is one quarter-hour rather than an hour the source already totaled. Aggregation builds the hour later: an hour enters the mean when minutes 0, 15, 30, and 45 are all present for the same location, date, clock hour, direction, and session. A historical extract, which this run did not clean, would have carried `hour_complete` true because each source row is already an hour.

`direction_normalized` is `NB` (190,064), `SB` (186,698), `EB` (186,190), `WB` (185,687), `NS` (2,964), or `EW` (1,921). Borough totals of quarter-hour rows, which are not neighborhood totals, are Queens 241,799, Brooklyn 213,086, Bronx 135,594, Manhattan 118,312, and Staten Island 44,733. `ts_local` is the count time in America/New_York. A field whose name contained `utc` would have stopped cleaning. `monitoring_location_id` is present and blank in this file. The id is computed in the next step from the segment and the rounded point.

`segment` is the DOT segment id. `wkt` is the State Plane point from `wktgeom`. `request` is the count-session id (`requestid`). The session keeps two requests at the same place from being averaged as if they were one visit.

Columns:

- `:id` — Socrata row id.
- `source` — `automated`.
- `year`, `month`, `day`, `hour`, `minute` — local date parts. `hour` is the clock hour 0–23. `minute` is 0, 15, 30, or 45.
- `vol` — vehicles in that quarter-hour.
- `segment` — road-segment id from the feed.
- `wkt` — point geometry as WKT, in EPSG:2263 feet.
- `direction_normalized` — approach direction.
- `borough` — borough after the alias table in `config/pipeline.toml`.
- `request` — count-session id.
- `ts_local` — timestamp with timezone America/New_York.
- `monitoring_location_id` — blank until spatial assignment.
- `hour_complete` — false on these quarter-hour rows.
- `high_count_flag` — true when `vol` exceeds the within-year 99.9th percentile.

## `traffic_monitoring_locations.geojson`

1,486 features, one point per distinct monitoring location. `scripts/05_assign_uhf42.py` builds a location from the WKT, not from each volume row. The id is the SHA-1 of `segment`, the State Plane x rounded to 1 meter, and the State Plane y rounded to 1 meter. The file is a GeoJSON `FeatureCollection`. Each geometry is a `Point`, and the CRS is `EPSG:2263` (NAD83 New York Long Island, US survey feet). Every feature has `source_crs` `EPSG:2263`, because the automated WKT already came in State Plane, inside the coordinate range the script accepts for that CRS. Every feature has a road-segment id.

The point-in-polygon test uses the DOHMH UHF42 shapefile, also projected to EPSG:2263. A point more than 1 meter from every edge, inside exactly one polygon, whose traffic borough agrees with the polygon, is `interior` and keeps that `uhf42_id`. The other assignments are listed and then left out of the volume file. `geography.lion_path` is empty, so none of them are reassigned along a LION street segment.

| `assignment` | Features | `uhf42_id` |
| --- | ---: | --- |
| `interior` | 1,386 | filled |
| `outside_nyc` | 47 | blank |
| `boundary` | 46 | blank |
| `borough_mismatch` | 7 | blank |

Properties on each feature:

- `monitoring_location_id` — the SHA-1 id. This is the `key` in `outputs/audit/exclusions_geo.csv` for the 100 non-interior locations.
- `road_segment_id` — DOT segment id. Filled on all 1,486 features.
- `uhf42_id` — neighborhood code for an interior point. Blank for the 100 excluded points.
- `assignment` — `interior`, `outside_nyc`, `boundary`, or `borough_mismatch`.
- `source_crs` — `EPSG:2263`.

The same assignment counts are in `outputs/quality/spatial_join_audit.csv`, which also records the traffic borough and the polygon borough. This GeoJSON is the geometry behind that audit. Sample maps draw the interior points on the simplified UHF42 basemap.

## `traffic_with_uhf42.parquet`

676,143 quarter-hour rows: the cleaned rows whose monitoring location was `interior`. The other 77,381 cleaned rows belonged to the 100 excluded locations and stop here. The file covers 1,386 locations and, across all years together, all 42 neighborhoods. A neighborhood can still have no rows in a given year. That is why the neighborhood table below has 256 rows rather than 294.

| Year | Rows placed in a neighborhood |
| --- | ---: |
| 2015 | 117,341 |
| 2016 | 120,936 |
| 2017 | 113,407 |
| 2018 | 76,805 |
| 2019 | 111,347 |
| 2022 | 65,121 |
| 2023 | 71,186 |

The columns are the cleaned-traffic columns, with `monitoring_location_id` filled, plus:

- `road_segment_id` — the segment id carried onto the location.
- `uhf42_id` — the neighborhood the point fell inside.

`hour_complete` is still false. These are still quarter-hour records. The next script is what turns them into hours.

## `traffic_location_year.parquet`

1,214 rows, one per interior monitoring location per year in which that location had any cleaned counts. `scripts/06_aggregate_traffic.py` first sums the four quarter-hours. An hour missing any of the four minutes is dropped. At a location that shows more than one direction in the extract, an hour is kept only when every direction that location shows is present, and the hour’s volume is the sum of those directions. A missing direction is not filled with zero. The location-day value is the mean of that day’s complete hours. The location-year value, `mean_hourly`, is the mean of those daily means.

955 rows are eligible and 259 are `below_threshold`. The 259 are `outputs/audit/exclusions_aggregate.csv`. Eligibility is at least two distinct local dates and at least one complete hour. With the hour floor set to 1, a location-year that has a complete hour and still fails has those hours on a single date.

Eligible location-years by year: 176 of 230 in 2015, 201 of 256 in 2016, 150 of 217 in 2017, 116 of 151 in 2018, 140 of 166 in 2019, 74 of 82 in 2022, and 98 of 112 in 2023. The sensitivity reruns in `outputs/stats/sensitivity_analysis_results.csv` rebuild neighborhood means from this table under stricter segment and hour floors, and under the set of segments that are eligible in every year.

`hour_balanced_mean` is a different summary of the same hours. It averages the means of the clock hours that were observed, and when both a weekday mean and a weekend mean exist it averages those two. It is stored so a sensitivity check can use it. The neighborhood column Pearson reads is `mean_hourly` after it has been averaged up to segments and neighborhoods, not this balanced mean.

Columns:

- `monitoring_location_id`, `road_segment_id`, `uhf42_id` — location, segment, and neighborhood.
- `year` — calendar year.
- `mean_hourly` — mean of daily means of complete hours, in vehicles per hour. Null when the location-year is not eligible.
- `hour_balanced_mean` — clock-hour balance described above. Null when not eligible.
- `n_complete_hours` — complete hours, including hours on a location-year that fails the two-date rule.
- `n_days` — distinct local dates.
- `n_sessions` — distinct count sessions (`requestid`, or location plus date plus direction when the request id is missing).
- `months` — months observed, as a comma-separated list.
- `weekday_hours`, `weekend_hours` — complete hours on Monday–Friday and on Saturday–Sunday.
- `eligible` — true when `n_days` is at least 2 and `n_complete_hours` is at least 1.
- `exclusion_reason` — blank when eligible, otherwise `below_threshold`.

## `traffic_segment_year.parquet`

1,197 rows, one per road segment per neighborhood per year. A segment’s `mean_hourly` is the unweighted mean of its eligible location means. `n_locations` counts those eligible locations. Hours and days are summed across the locations on the segment, including locations that were not eligible, which is why an ineligible segment can show a positive `n_complete_hours` and a null mean.

941 segments are eligible and 256 are not. The ineligible rows are exactly the rows with a null `mean_hourly`. `own_segment` is false on all 1,197 rows: every location had a segment id, so none had to stand in as its own segment. The file has 17 fewer rows than the location-year file because some segments have more than one monitoring location in the same year. Eligible segments by year: 174 in 2015, 198 in 2016, 147 in 2017, 115 in 2018, 137 in 2019, 73 in 2022, and 97 in 2023.

Columns:

- `road_segment_id`, `uhf42_id`, `year` — segment, neighborhood, and year.
- `mean_hourly` — unweighted mean of eligible location means. Null when no location on the segment was eligible.
- `hour_balanced_mean` — the same average applied to `hour_balanced_mean` from the locations.
- `n_locations` — eligible locations on the segment.
- `n_complete_hours`, `n_days` — hours and distinct dates across the segment’s locations.
- `eligible` — true when at least one location mean exists.
- `own_segment` — true if a location with no segment id was given a vote of its own. False throughout this file.

## `traffic_uhf42_year.csv`

256 rows, one per neighborhood that had any aggregated traffic in a study year. A neighborhood with no interior counter that year is absent. The full 42-by-7 grid is the next file. `traffic_mean_vehicles_per_hour` is the unweighted mean of eligible segment means. For a pooled asthma period the neighborhood value would be the unweighted mean of the year-level means, and the neighborhood would be valid only if every year in the pool were eligible. Every period in this run is a single year, so that second average is not applied.

244 rows are `valid` and 12 are `insufficient`. The 12 have a null traffic mean, `n_road_segments` 0, and `n_monitoring_locations` 0, with `n_complete_hours` still positive. Those hours belong to locations that failed the two-date rule. The neighborhood is listed so the hours are visible, and it contributes no exposure. All 256 rows have `traffic_source` `automated`.

Valid neighborhoods by year: 37, 39, 35, 36, 35, 30, and 32. Among the 244 valid rows, the mean of the neighborhood means is 538.1 vehicles per hour, the median is 379.6, and the maximum is 4,119.1. The standard deviation across those 244 means is 567.0. That spread is a mix of busy arterials and thin samples, which is why the stats folder reports Spearman and leave-one-neighborhood-out beside Pearson.

On a valid row, `traffic_sd_vehicles_per_hour` is the standard deviation of the segment means. It is null for the 44 valid neighborhoods that have exactly one eligible segment, because a single number has no spread. `traffic_mean_hour_balanced` equals `traffic_mean_vehicles_per_hour` on 15 of the 244 valid rows and differs on the other 229. Pearson does not read the balanced column.

Weekday and weekend coverage, from whether any complete hour fell on a weekday, a weekend, or both:

| Status | Both | Weekday only | Weekend only |
| --- | ---: | ---: | ---: |
| valid | 218 | 24 | 2 |
| insufficient | 0 | 10 | 2 |

Among valid rows the median neighborhood has 4 count sessions, 15 distinct observation days, and 440.5 complete hours. The median eligible-segment count is 2 in 2022 and 2023 and higher in the earlier years. Those medians are repeated in `outputs/stats/correlation_comparison_results.csv`.

Columns:

- `uhf42_id`, `period` — neighborhood and asthma year.
- `traffic_mean_vehicles_per_hour` — the exposure, vehicles per complete counted hour. Null on the 12 insufficient rows.
- `traffic_mean_hour_balanced` — neighborhood mean of the clock-hour-balanced location means. Not the Pearson exposure.
- `traffic_median_vehicles_per_hour` — median of the eligible segment means.
- `traffic_sd_vehicles_per_hour` — standard deviation of the eligible segment means. Null when only one segment votes.
- `n_road_segments` — eligible segments. This is the number `adequate` is based on, with a minimum of 1.
- `n_monitoring_locations` — eligible locations.
- `n_observation_days` — distinct dates represented.
- `n_complete_hours` — complete hours, including hours that did not produce an eligible location.
- `n_sessions` — count sessions.
- `months_represented` — months present, comma-separated.
- `weekday_weekend_coverage` — `both`, `weekday`, `weekend`, or blank when neither class of hour exists.
- `traffic_source` — `automated`.
- `coverage_status` — `valid` or `insufficient`.

`outputs/quality/traffic_coverage_by_uhf42.csv` is this table reduced to period, neighborhood, segment count, location count, complete hours, the adequate flag, and the source.

## `final_merged_dataset.csv`

294 rows, one per neighborhood per study year, including the years with no usable traffic. `scripts/07_merge.py` outer-joins `cleaned_asthma.csv` to `traffic_uhf42_year.csv` on `uhf42_id` and `period`. The join keeps a row when either side is present. There are 42 rows in every year. A duplicate key or a year with more than 42 neighborhoods would have stopped the merge and written nothing.

244 rows are `valid`. 50 are `insufficient`, and every one of those 50 has `exclusion_reason` `missing_traffic`. The asthma rate is filled on all 294 rows. A valid row has a rate, a traffic mean, and at least one eligible segment. Usable n is 37 in 2015, 39 in 2016, 35 in 2017, 36 in 2018, 35 in 2019, 30 in 2022, and 32 in 2023. All seven years clear the minimum of 30, so all seven are tested. 2023 is the primary year because `years.primary_preference` is 2023 and its usable n is 32. That choice is recorded in the Pearson file, not here.

Of the 50 missing-traffic rows, 38 have a blank `traffic_source` because the neighborhood-year is absent from `traffic_uhf42_year.csv`. The other 12 have `traffic_source` `automated` and a null mean: they are the insufficient rows of that traffic file. Names and boroughs for those 12 are in `outputs/quality/README.md`. The row-level keys are `outputs/audit/exclusions_merge.csv`. The boolean form of the same 294 rows is `outputs/quality/missing_data_report.csv`.

Descriptive statistics of this file, by year and for the 244 valid rows stacked together, are `outputs/stats/descriptive_statistics_by_year.csv` and `outputs/stats/descriptive_statistics_by_borough.csv`. The stack of 244 is a description of the usable sample. It is not one pooled correlation. Each year is tested on its own valid rows.

Columns:

- `uhf42_id`, `uhf42_name`, `borough`, `period` — neighborhood identity and year.
- `asthma_ed_count` — ED visits, ages 4 and under.
- `asthma_ed_rate_per_10000` — the outcome, per 10,000 children ages 4 and under.
- `traffic_mean_vehicles_per_hour` — the exposure. Null when the neighborhood-year is insufficient.
- `traffic_median_vehicles_per_hour`, `traffic_sd_vehicles_per_hour` — median and standard deviation of the segment means behind the exposure.
- `n_road_segments` — eligible segments.
- `n_observation_days`, `n_complete_hours` — dates and complete hours behind the exposure.
- `traffic_source` — `automated` when a traffic row existed, blank when the neighborhood-year had no traffic row.
- `coverage_status` — `valid` or `insufficient`.
- `exclusion_reason` — blank on valid rows. `missing_traffic` on the 50 insufficient rows. The merger would also write `missing_rate`, `segment_threshold`, or `missing_rate;missing_traffic`. None of those occurred.
- `traffic_mean_hour_balanced` — the balanced neighborhood mean, carried along for sensitivity. Pearson reads `traffic_mean_vehicles_per_hour`.
