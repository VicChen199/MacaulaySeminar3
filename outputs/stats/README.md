# Statistics tables

These six CSVs are the numeric results of the neighborhood comparison. `scripts/08_describe.py` writes the two descriptive files from `outputs/data/final_merged_dataset.csv`. `scripts/09_test_hypotheses.py` writes the four test files from that merged file and from `outputs/data/traffic_location_year.parquet`.

The question is how the UHF42 neighborhood mean of sampled hourly vehicle counts lines up with the annual asthma emergency-department visit rate for children ages 4 and under. The null hypothesis says the Pearson correlation between those two neighborhood measures is 0. The alternative says the correlation is positive: neighborhoods with more traffic have higher child asthma ED rates. The primary test is a two-sided Pearson correlation at alpha 0.05. The sign of r is reported on its own, because a two-sided test can reject for either direction. The unit is the neighborhood, not the child and not the counter. A neighborhood pattern is not a statement about individual children. The pipeline does not treat the correlation as evidence that traffic causes asthma visits.

Exposure, `traffic_mean_vehicles_per_hour`, is vehicles per complete counted hour, averaged from days to locations to road segments to the neighborhood. It is the mean of the hours DOT happened to count, not a complete annual average of every hour in the year. Outcome, `asthma_ed_rate_per_10000`, is the neighborhood rate of asthma ED visits per 10,000 children ages 4 and under. Both variables stay null when they are missing. The test uses rows with `coverage_status` = `valid` only.

The primary period is chosen before any p-value is read. `years.primary_preference` is 2023. That year is included and has 32 valid neighborhoods, which clears `thresholds.min_uhf_per_period` (30), so 2023 is primary. The other six years are secondary. Holm adjustment is applied only to the secondary Pearson p-values.

The figures that plot these tables are under `outputs/figures/results/`: `R1_primary_scatter` (2023), `R2_comparison_scatter` (the latest year that is not primary, 2022), `R3_correlation_forest` (every tested year), and `R4_pearson_spearman` (both coefficients). Sample figures S1–S7 under `outputs/figures/sample/` describe the same merged file and do not print p-values. Three neighborhood path charts under `outputs/figures/neighborhood/` follow Hunts Point - Mott Haven, High Bridge - Morrisania, and Crotona - Tremont across their valid years. Each title prints that neighborhood's own Pearson *r*, p-value, and a reading of positive, negative, or null. They are described in the figure guide. They do not add a row to the Pearson table.

The numbers below are the values currently stored in these CSVs. A new run of `scripts/08_describe.py` and `scripts/09_test_hypotheses.py` rewrites the CSVs. This note is not rewritten by those scripts.

## `descriptive_statistics_by_year.csv`

One row per period per variable, plus a final block whose `period` is `usable`. Twenty-four rows: 7 periods × 3 variables, and the same three variables on the stacked valid rows. `scripts/08_describe.py` computes every statistic from the merged neighborhood file. For a named year, the frame is all 42 neighborhoods, valid and insufficient together, so `n_missing` counts neighborhoods that lack that variable. For `period` = `usable`, the frame is only `coverage_status` = `valid`, which is why all three variables there have n = 244 and no missing values. The 244 is the stack of valid neighborhood-years (37 + 39 + 35 + 36 + 35 + 30 + 32). It is not a sample size for a pooled correlation. Pearson is fit separately inside each year.

`std` is the sample standard deviation (divisor n − 1). `range` is max − min. `q1` and `q3` are the 25th and 75th percentiles, and `iqr` is q3 − q1. `pct_missing` is `100 * n_missing / (n + n_missing)`.

Variables:

- `asthma_ed_rate_per_10000` — child asthma ED visits per 10,000 children ages 4 and under. Present for all 42 neighborhoods in every year, so `n` = 42, `n_missing` = 0, and `pct_missing` = 0.
- `traffic_mean_vehicles_per_hour` — neighborhood mean of sampled complete-hour vehicle counts. `n` is the number of neighborhoods with a mean, and it matches the Pearson `n` for that year. Missingness is the share of the 42 neighborhoods with no mean.
- `n_road_segments` — eligible road segments behind that neighborhood mean. On the year rows, a zero can appear for a neighborhood that had traffic rows and no eligible segment. On the `usable` row every value is at least 1, because a valid row has at least one eligible segment.

What the current distributions say:

Asthma rates are highest and most spread out in 2015 (mean 279.7, median 199.3, sd 234.5, from 43.4 to 876.5 per 10,000). The neighborhood mean then falls: 268.7 in 2016, 218.7 in 2017, 219.8 in 2018, 181.2 in 2019, 190.4 in 2022, and 186.6 in 2023. Medians fall with the means, from 199.3 to 161.1, and the 2023 maximum is 466.0 against 876.5 in 2015. The rate distribution stays right-skewed in every year: the mean is above the median, and the upper tail is long. Across the 244 valid neighborhood-years the mean rate is 224.6 and the median is 160.7.

Traffic means do not follow that decline. The neighborhood mean of counted vehicles per hour is 461 in 2015, 351 in 2016, 442 in 2017, 478 in 2018, 559 in 2019, 564 in 2022, and 980 in 2023. Medians stay lower than means (361, 298, 360, 350, 418, 437, 572), so a few high-volume counters pull the mean up. The 2023 sd is 1,023 and the maximum is 4,119 vehicles per hour, against a 2015 maximum of 1,564. Those extremes are a property of which automated counters were active. They are why the rank correlation in `spearman_results_by_year.csv` is reported beside Pearson, and why leave-one-neighborhood-out is in the sensitivity file. Traffic is missing for 11.9% of neighborhoods in 2015 (5 of 42), 7.1% in 2016, 16.7% in 2017, 14.3% in 2018, 16.7% in 2019, 28.6% in 2022 (12 of 42), and 23.8% in 2023 (10 of 42).

Eligible segment counts thin out over time. The median among neighborhoods that have a segment count is 4 in 2015, 5 in 2016, 4 in 2017, 3 in 2018 and 2019, and 2 in 2022 and 2023. The 2023 maximum is 18 segments; the 2016 maximum is 13. The `usable` row, limited to neighborhoods that entered a test, has median 3 segments and mean 3.86. Later primary years rest on fewer counters per neighborhood. `correlation_comparison_results.csv` records that median again as `n_segments_median`, restricted to the valid rows of each year (2.0 in both 2022 and 2023).

Columns:

- `period` — a study year, or `usable` for the stacked valid rows.
- `variable` — one of the three names above.
- `n` — neighborhoods with a non-null value.
- `mean`, `median`, `std`, `min`, `max`, `range`, `q1`, `q3`, `iqr` — ordinary sample summaries of those observed values. Blank only when `n` is 0. In this file every row has n above 0.
- `n_missing` — rows in the period frame with a null for that variable.
- `pct_missing` — missing share of the period frame, in percent.

## `descriptive_statistics_by_borough.csv`

One row per period, borough, and coverage status. Sixty-six rows. The same script groups the merged file by `borough` and `coverage_status` and writes the count and the percentage of that period’s rows. There is no statistical test in this file. It shows whether the neighborhoods that survive the traffic rule are spread across the five boroughs or concentrated in a few.

For a calendar year, the denominator of `pct` is 42, one row per UHF42 neighborhood. Bronx has 7 neighborhoods, Brooklyn 11, Manhattan 10, Queens 10, and Staten Island 4, so a borough that is entirely valid appears as 16.67%, 26.19%, 23.81%, 23.81%, or 9.52%. For `period` = `usable` the denominator is 244, and only `valid` rows are present, so `pct` is that borough’s share of the stacked analysis sample.

Coverage by year, valid neighborhoods:

| Period | Bronx | Brooklyn | Manhattan | Queens | Staten Island | Valid n |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2015 | 7 | 10 | 7 | 10 | 3 | 37 |
| 2016 | 7 | 11 | 7 | 10 | 4 | 39 |
| 2017 | 7 | 9 | 8 | 9 | 2 | 35 |
| 2018 | 7 | 11 | 8 | 8 | 2 | 36 |
| 2019 | 6 | 9 | 8 | 8 | 4 | 35 |
| 2022 | 6 | 10 | 5 | 6 | 3 | 30 |
| 2023 | 4 | 11 | 7 | 8 | 2 | 32 |

The insufficient counts are the complement that brings each borough back to its full size. Brooklyn is valid in all 11 neighborhoods in 2016, 2018, and 2023. The thin years are 2022 and 2023 outside Brooklyn: 2022 Manhattan is split 5 valid and 5 insufficient, 2022 Queens is 6 and 4, and 2023 Bronx is 4 valid and 3 insufficient. Staten Island contributes 2 valid neighborhoods in 2017, 2018, and 2023.

Of the 244 usable neighborhood-years, Brooklyn contributes 71 (29.1%), Queens 59 (24.2%), Manhattan 50 (20.5%), the Bronx 44 (18.0%), and Staten Island 20 (8.2%). Brooklyn’s share of the usable stack is larger than its 11/42 share of the map because Brooklyn kept its counters more often. Staten Island’s share stays close to its share of the 42 neighborhoods.

Columns:

- `period` — a study year, or `usable`.
- `borough` — Bronx, Brooklyn, Manhattan, Queens, or Staten Island, taken from the asthma neighborhood file.
- `coverage_status` — `valid` or `insufficient`. A valid row has a rate, a traffic mean, and at least one eligible segment.
- `n` — neighborhoods in that borough and status.
- `pct` — `100 * n /` rows in that period frame.

## `pearson_results_by_year.csv`

One row per included period, 7 rows. This is the hypothesis test. `lib/stats.py` `pearson_by_period` takes the valid neighborhoods in the year, sets x to `traffic_mean_vehicles_per_hour` and y to `asthma_ed_rate_per_10000`, and calls `scipy.stats.pearsonr` two-sided. The t statistic stored here is `r * sqrt((n - 2) / (1 - r^2))`, with `df` = n − 2. The script also compares that t with the t from `scipy.stats.linregress` and stops if the relative gap exceeds 1e-4. `p_raw` and the 95% interval come from SciPy. Alpha is 0.05 on every row.

2023 is `role` = `primary` because it is the configured preference year and its usable n is at least 30. `primary_reason` is `primary_preference` on every row: the same choice rule produced this assignment, and the column records that rule rather than a different rule per year. `p_holm` is blank on the primary row. Holm is computed only for secondary rows with `test_status` = `ok`, stepping from the smallest secondary p-value upward and capping at 1. Every row here has `test_status` = `ok`, a blank `skip_reason`, and `traffic_source` = `automated`. A year with fewer than 30 valid neighborhoods, fewer than 3 pairs, a variable with zero variance, or a perfect correlation would be `skipped` with a null r and a null p-value. None of those happened.

Current Pearson results, traffic mean against the child asthma ED rate:

| Period | Role | n | r | t | df | p (raw) | p (Holm) | 95% CI | Sign | p < 0.05 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 2015 | secondary | 37 | −0.181 | −1.088 | 35 | 0.284 | 1.000 | −0.477 to 0.152 | negative | no |
| 2016 | secondary | 39 | −0.283 | −1.797 | 37 | 0.081 | 0.483 | −0.550 to 0.035 | negative | no |
| 2017 | secondary | 35 | −0.039 | −0.226 | 33 | 0.823 | 1.000 | −0.368 to 0.298 | negative | no |
| 2018 | secondary | 36 | 0.113 | 0.661 | 34 | 0.513 | 1.000 | −0.224 to 0.425 | positive | no |
| 2019 | secondary | 35 | −0.108 | −0.621 | 33 | 0.539 | 1.000 | −0.426 to 0.234 | negative | no |
| 2022 | secondary | 30 | −0.263 | −1.442 | 28 | 0.160 | 0.802 | −0.569 to 0.108 | negative | no |
| 2023 | primary | 32 | −0.081 | −0.444 | 30 | 0.660 |  | −0.418 to 0.276 | negative | no |

The primary estimate is r = −0.081 in 32 neighborhoods. The 95% interval runs from −0.42 to 0.28 and contains 0. The two-sided p-value is 0.66, so the primary test does not reject a correlation of 0. The sign is negative, which is the opposite direction from the alternative that predicted a positive association. The interval is wide enough to include moderate correlations in either direction. With 32 neighborhoods, a correlation has to be roughly 0.35 in absolute value before this test would reject at 0.05, and the observed primary correlation is about a quarter of that size.

Across years, six of the seven point estimates are negative and 2018 is positive and small (r = 0.113). Every unadjusted p-value is above 0.05. The smallest is 2016 (r = −0.283, p = 0.081). After Holm adjustment across the six secondary tests, that p-value is 0.483, and the other secondary p-values move to 0.80 or 1. Every confidence interval contains 0. The file therefore shows a weak negative point estimate in most years, a wide interval in every year, and no year in which the Pearson test distinguishes the correlation from 0.

Columns:

- `period` — study year.
- `role` — `primary` or `secondary`.
- `n` — valid neighborhoods in the correlation.
- `r` — Pearson correlation.
- `t` — t statistic from the formula above.
- `df` — n − 2.
- `p_raw` — two-sided SciPy p-value for a null correlation of 0.
- `p_holm` — Holm-adjusted p-value for a secondary test. Blank for the primary test.
- `ci_low`, `ci_high` — ends of the 95% confidence interval for r.
- `alpha` — 0.05.
- `p_below_alpha` — true when `p_raw` is below alpha. False on every row in this run.
- `sign` — `positive`, `negative`, or `zero`, from the sign of r.
- `primary_reason` — why 2023 was selected. `primary_preference` means the configured year qualified.
- `test_status` — `ok` or `skipped`.
- `skip_reason` — blank when the test ran. Otherwise `below_min_uhf`, `n_below_3`, `zero_variance`, or `perfect_correlation`.
- `traffic_source` — `automated`.

## `spearman_results_by_year.csv`

One row per included period, 7 rows. `spearman_by_period` repeats the same valid pairs with `scipy.stats.spearmanr`. Spearman correlates the ranks of the traffic mean and the ranks of the asthma rate, so a single very high counter moves the coefficient less than it moves Pearson. The same skip rules apply: under 30 valid neighborhoods, under 3 pairs, or zero variance leaves `rho` and `p_raw` blank and sets `test_status` to `skipped`. Holm is not applied here. This table is a sensitivity check. It does not replace the primary Pearson test, and the hypothesis statement is about Pearson’s r.

| Period | n | rho | p (raw) | Status |
| --- | ---: | ---: | ---: | --- |
| 2015 | 37 | −0.158 | 0.350 | ok |
| 2016 | 39 | −0.454 | 0.00373 | ok |
| 2017 | 35 | −0.011 | 0.948 | ok |
| 2018 | 36 | 0.034 | 0.843 | ok |
| 2019 | 35 | −0.146 | 0.401 | ok |
| 2022 | 30 | −0.170 | 0.369 | ok |
| 2023 | 32 | 0.089 | 0.627 | ok |

2016 is the year where the two coefficients disagree in strength. Pearson there is −0.283 with p = 0.081. Spearman is −0.454 with p = 0.00373. The rank association in 2016 is negative and would be marked unusual under a single two-sided rank test at 0.05. That p-value is not adjusted for the seven years, and it is not the primary test. The direction agrees with Pearson: higher traffic ranks sit with lower asthma-rate ranks in that year.

On the primary year the two coefficients disagree in sign and agree in conclusion. Pearson is −0.081 (p = 0.660) and Spearman is 0.089 (p = 0.627). Both are small, both intervals of judgment include no association, and the sign of a correlation this close to zero is sensitive to whether the extreme traffic values keep their magnitude. `R4_pearson_spearman` plots the two columns side by side for that reason. 2017 and 2018 are essentially flat on the rank scale (rho −0.011 and 0.034).

Columns:

- `period` — study year.
- `n` — valid neighborhoods, the same n as Pearson.
- `rho` — Spearman rank correlation.
- `p_raw` — two-sided p-value from SciPy.
- `test_status` — `ok` or `skipped`.
- `skip_reason` — blank when the test ran.

## `sensitivity_analysis_results.csv`

Forty-two rows, all for the primary period 2023. `sensitivity` reruns Pearson under changes that ask whether the primary r depends on one neighborhood, on the coverage thresholds, or on using only neighborhoods and segments that appear in every year. `stats.bootstrap_comparisons` is false, so this file has no paired-bootstrap rows. A blank `r` and blank `p_raw` mean the rerun had fewer than 3 neighborhoods, a constant variable, or a perfect correlation, and no coefficient was stored.

`analysis_id` names the rerun. `note` carries the dropped neighborhood id on leave-one-out rows and is blank otherwise. `period` is 2023 on every row.

Leave-one-out (`loo_<uhf42_id>`, 32 rows). Each row drops one of the 32 valid 2023 neighborhoods and refits Pearson on the other 31. n is 31 on every row. r stays between −0.121 (dropping UHF 103, Fordham–Bronx Park) and 0.019 (dropping UHF 201, Greenpoint). Greenpoint is the only drop that flips the sign, and the flipped r is 0.019 with p = 0.919. Every leave-one-out p-value is above 0.50. No single neighborhood is producing the primary result, and no single neighborhood turns it into a correlation the test would distinguish from 0.

Stricter coverage (`segments_<s>_hours_<h>`, 6 rows). The primary rule is 1 eligible segment and 1 complete hour, with 2 distinct dates still required. These rows rebuild the 2023 neighborhood means from `traffic_location_year.parquet` using 5, 8, or 10 segments and 24 or 48 complete hours.

| Analysis | n | r | p |
| --- | ---: | ---: | ---: |
| 5 segments, 24 hours | 5 | −0.239 | 0.698 |
| 5 segments, 48 hours | 5 | −0.239 | 0.698 |
| 8 segments, 24 hours | 1 |  |  |
| 8 segments, 48 hours | 1 |  |  |
| 10 segments, 24 hours | 1 |  |  |
| 10 segments, 48 hours | 1 |  |  |

Requiring 5 segments leaves 5 neighborhoods. The two hour floors give the same five neighborhoods, the same r, and the same p-value, so every neighborhood that still had 5 segments also had at least 48 complete hours. r moves from −0.081 to −0.239, and the p-value rises to 0.70 because n = 5. Requiring 8 or 10 segments leaves a single neighborhood, which is not enough to compute a correlation. The primary sample is a one-segment sample. A version of the study limited to neighborhoods with many counters does not have a 2023 test.

Shared neighborhoods (`neighborhood_intersection`, 1 row). The id set is the neighborhoods that are valid in all seven years. Pearson is then fit on the 2023 traffic means and 2023 asthma rates for that shared set only. n = 15, r = −0.107, p = 0.703. Fifteen neighborhoods are present in every year’s usable sample. Restricting the primary year to that stable subset leaves the correlation near zero. The other 17 neighborhoods in the 2023 test of 32 are valid in 2023 and missing in at least one other year, which is expected for a counter system that moves.

Shared segments (`segment_intersection`, 1 row). The id set is road segments that are eligible in every constituent year of every included period. Those segments are then re-aggregated to 2023 neighborhood means, and Pearson is fit where a neighborhood still has both a rebuilt mean and an asthma rate. n = 0. No road segment is eligible in all seven years, so there is no common-segment correlation to report. The study does not have a fixed panel of the same counters running through 2015–2023. Each year is a fresh cross-section of whichever automated counts were on the street.

Columns:

- `analysis_id` — `loo_<uhf>`, `segments_<s>_hours_<h>`, `neighborhood_intersection`, or `segment_intersection`.
- `period` — the primary period the rerun describes.
- `n` — neighborhoods left in that rerun.
- `r`, `p_raw` — two-sided Pearson correlation and p-value, blank when the rerun cannot support one.
- `note` — the dropped UHF42 id on a leave-one-out row. Blank for the threshold and intersection rows.

## `correlation_comparison_results.csv`

One row per included period, 7 rows. `compare_periods` copies the Pearson estimate for each year and adds two fields that describe whether the years can be read as the same finding. It does not stack the 244 neighborhood-years into one correlation. A neighborhood that appears in seven years is seven separate pairs, not seven observations in a pooled r.

`n_segments_median` is the median of `n_road_segments` among valid neighborhoods in that year: 4, 5, 4, 3, 3, 2, and 2 from 2015 through 2023. The primary year and 2022 are the years whose typical valid neighborhood rests on two eligible segments.

`measurement_consistency` is one text field with five pieces:

- `direction` — the sign of that year’s r.
- `magnitude` — the year’s r, printed to 16 significant digits.
- `contains_0` — `true` when the 95% Pearson interval includes 0. It is `true` for every year in this file.
- `overlap` — how many neighborhoods are valid both in that year and in the primary year. For 2023 the overlap is the primary n itself, 32. For the other years it is 28 (2015), 30 (2016), 26 (2017), 27 (2018), 26 (2019), and 25 (2022). Most of the 2023 sample also appears in each earlier year. The overlap is lowest in 2022, where only 25 of the 30 valid neighborhoods are also valid in 2023.
- the last token is `mixed_sign` on every row. It is `consistent_sign` only when every successful Pearson row has the same sign. 2018 is positive and the other six years are negative, so the token is `mixed_sign` everywhere, including on the 2018 row.

Read as a set, the comparison file says: the point estimates are mostly negative and all small enough that each interval contains zero; the primary year shares a majority of its neighborhoods with each earlier year; the sign is not the same in every year; and the later years are measured with fewer segments per neighborhood. Those are the limits on treating 2015 and 2023 as repeated measurements of one correlation. They are seven cross-sections, estimated on the neighborhoods that had a usable automated count that year.

Columns:

- `period`, `n`, `r`, `p_raw`, `p_holm`, `ci_low`, `ci_high`, `sign`, `traffic_source` — the Pearson columns for that year, including the blank Holm cell on 2023.
- `n_segments_median` — median eligible segment count among valid neighborhoods.
- `measurement_consistency` — the packed direction, magnitude, interval, overlap, and sign-agreement note described above.
