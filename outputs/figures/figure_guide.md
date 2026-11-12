# Figure guide

Each chart is saved twice, as `.png` and `.pdf`, with the same content. On the sample and result charts, the text printed on the figure is a column name or a string of `n`, `r`, and `p`. On the neighborhood path charts, the printed title is the neighborhood id and name, then `n`, `r`, `p`, a reading, and the sign of `r`. This file is the readable title and the sample behind each one.

The charts sit in three folders:

- **Sample figures,** S1–S7, in `outputs/figures/sample/`. They describe the merged file. They do not show a test.
- **Result figures,** R1–R4, in `outputs/figures/results/`. They are the hypothesis tests.
- **Neighborhood paths,** N107, N106, and N105, in `outputs/figures/neighborhood/`. Each one follows a single neighborhood across the years where it is valid, and the title gives that path's Pearson correlation.

The study asks how neighborhood traffic lines up with asthma emergency-department visits for children ages 4 and under in New York City. The unit is one United Hospital Fund neighborhood (UHF42). There are 42 of those neighborhoods. A point or a boxplot observation is a neighborhood, not a person, a street, or a hospital visit.

Years in every figure are the included study years: **2015, 2016, 2017, 2018, 2019, 2022, and 2023**. 2020 and 2021 are excluded in `config/pipeline.toml` and do not appear. Each included period is a single calendar year. Traffic in all of these figures comes from NYC Open Data automated traffic counts (`7ym2-wayt`). Historical counts and annual average daily traffic are not plotted. The asthma numbers come from the NYC Environment and Health Data Portal export for emergency-department visits, ages 4 and under.

Two samples show up repeatedly:

- **All neighborhoods with a value.** Asthma rates exist for all 42 neighborhoods in every included year. Traffic means exist only where a usable hourly volume could be built.
- **Eligible neighborhoods.** `coverage_status` is `valid`: the neighborhood has an asthma rate and a traffic mean from at least one eligible road segment. These are the neighborhoods in the correlation tests. Counts are 37 (2015), 39 (2016), 35 (2017), 36 (2018), 35 (2019), 30 (2022), and 32 (2023).

**2023 is the primary year** because `years.primary_preference` is 2023 and that year has at least 30 eligible neighborhoods. **2022 is the comparison year** because it is the latest included year that is not the primary. In 2023 the 32 eligible neighborhoods are 4 in the Bronx, 11 in Brooklyn, 7 in Manhattan, 8 in Queens, and 2 on Staten Island.

| Measure | What it is | How to read it |
| --- | --- | --- |
| `traffic_mean_vehicles_per_hour` | Neighborhood mean hourly traffic volume | Unweighted mean of eligible road-segment means, in vehicles per hour |
| `asthma_ed_rate_per_10000` | Asthma ED visit rate, ages 4 and under | Visits per 10,000 children in that neighborhood that year |
| Eligible / valid | Neighborhood kept in a correlation | Both measures present and the segment rule met |
| `n` on a result chart | Neighborhoods in that year's Pearson test | Equals the eligible count for that year |
| `r` | Pearson correlation | Positive would mean higher traffic with a higher asthma rate |
| `rho` | Spearman rank correlation | Same pair of variables, ranks instead of raw values |
| `p` | Two-sided p-value | Null hypothesis is a correlation of 0; alpha is 0.05 |

---

## Sample figures

These describe the merged file. They do not show a test. Files are in `outputs/figures/sample/`.

### S1 — 2023 distribution of neighborhood traffic volume

`S1_traffic_distribution.png` and `.pdf`

Histogram of `traffic_mean_vehicles_per_hour` for **2023 only**. The 32 bars' worth of neighborhoods are the ones with a traffic mean that year, which is the same 32 later used in the primary correlation. The other 10 UHF42 neighborhoods have no traffic mean in 2023 and are omitted.

The printed title is `n=32, mean=980.05473, median=572.360406, sd=1022.873282`. The horizontal axis is the column name. Most neighborhoods sit under about 1,000 vehicles per hour. The mean is well above the median because a few neighborhoods are much higher, out toward about 4,000 vehicles per hour.

### S2 — 2023 distribution of the child asthma ED rate

`S2_asthma_distribution.png` and `.pdf`

Histogram of `asthma_ed_rate_per_10000` for **2023 only**. This is all **42** neighborhoods, including the 10 that lack usable traffic and are left out of S1 and out of the 2023 correlation.

The printed title is `n=42, mean=186.614286, median=161.1, sd=105.595283`. The horizontal axis is the column name. The distribution is right-skewed: many neighborhoods fall near 100 to 200 visits per 10,000, and the upper tail reaches about 470.

### S3 — Eligible neighborhoods by year

`S3_coverage_by_period.png` and `.pdf`

Bar chart of how many of the 42 neighborhoods are eligible in each included year. The printed title is `Coverage by period`. The vertical axis is `eligible neighborhoods`. Bars run from the selection order, newest first: 2023, 2022, 2019, 2018, 2017, 2016, 2015.

| Year | Eligible neighborhoods | Out of 42 |
| --- | ---: | ---: |
| 2023 | 32 | 10 not eligible |
| 2022 | 30 | 12 not eligible |
| 2019 | 35 | 7 not eligible |
| 2018 | 36 | 6 not eligible |
| 2017 | 35 | 7 not eligible |
| 2016 | 39 | 3 not eligible |
| 2015 | 37 | 5 not eligible |

2022 is the thinnest year. 2016 is the fullest. Every year clears the 30-neighborhood minimum, so every year is tested.

### S4 — Which neighborhoods are eligible in which year

`S4_coverage_heatmap.png` and `.pdf`

Grid of all **42 neighborhoods** (rows) by the **seven included years** (columns, 2015 on the left through 2023 on the right). A blue cell means that neighborhood is eligible in that year. A white cell is masked: the neighborhood is not eligible that year, because traffic is missing or coverage is insufficient. Asthma rates are not missing; the gaps are traffic coverage.

The printed title is `Missing cells are masked`. Rows are unlabeled, in merged-file order, so the chart does not name Kingsbridge, Bedford Stuyvesant, or any other neighborhood. Every blue cell is the value 1. The color bar does not encode a rate or a traffic volume.

### S5 — Asthma ED rate across years

`S5_asthma_boxplots.png` and `.pdf`

Boxplots of `asthma_ed_rate_per_10000`. One box per included year, left to right: 2015, 2016, 2017, 2018, 2019, 2022, 2023. The printed title is the column name.

Each box uses **all 42 neighborhoods**, including neighborhoods that are not eligible for the correlation that year. The median falls from about 199 per 10,000 in 2015 to about 136 in 2019, then sits near 156 to 161 in 2022 and 2023. The upper tail is longest in 2015 and 2016 (neighborhoods above about 800) and shorter by 2023, where the highest rate is about 466.

### S6 — Traffic volume across years

`S6_traffic_boxplots.png` and `.pdf`

Boxplots of `traffic_mean_vehicles_per_hour`. Same year order as S5. The printed title is the column name.

Each box uses neighborhoods with a traffic mean that year. Those counts match the correlation samples: 37, 39, 35, 36, 35, 30, and 32. Medians stay in a few hundred vehicles per hour through 2022. The 2023 box sits higher (median about 572) and has a long upper tail, including neighborhoods above about 4,000 vehicles per hour. Those high 2023 values are the same ones that stretch the shared horizontal axis of R1 and R2.

### S7 — Monitor map for each year, titled with that year's eligible count

One pair of files per included year:

| File stem | Year | Eligible neighborhoods in the title |
| --- | --- | ---: |
| `S7_coverage_map_2015` | 2015 | 37 |
| `S7_coverage_map_2016` | 2016 | 39 |
| `S7_coverage_map_2017` | 2017 | 35 |
| `S7_coverage_map_2018` | 2018 | 36 |
| `S7_coverage_map_2019` | 2019 | 35 |
| `S7_coverage_map_2022` | 2022 | 30 |
| `S7_coverage_map_2023` | 2023 | 32 |

The printed title is `{year} eligible {count}`, for example `2023 eligible 32`. The drawn layers are the UHF42 neighborhood basemap and the monitoring-location points from `outputs/data/traffic_monitoring_locations.geojson`. Those points are the full set of assigned monitors. They are not filtered to the year in the title, and neighborhoods are not shaded by whether they were eligible. The year-specific information is the eligible count in the title. That count is the same number as the bar for that year in S3 and the `n` for that year in R3.

---

## Result figures

These are the hypothesis tests. Files are in `outputs/figures/results/`. Each test is a two-sided Pearson correlation of traffic volume with the asthma ED rate, alpha 0.05. The hypothesis of interest is a positive correlation. None of the seven years has a p-value below 0.05, and every 95% confidence interval includes 0.

R1 and R2 share axis limits, taken from the combined range of the 2023 and 2022 eligible neighborhoods, so the two scatters can be compared on the same scale. 2023's high-traffic neighborhoods set the right edge near 4,000 vehicles per hour.

### R1 — Primary scatter, 2023

`R1_primary_scatter.png` and `.pdf`

Scatter of the **32 eligible neighborhoods in 2023**. Horizontal axis: neighborhood mean vehicles per hour. Vertical axis: asthma ED visits per 10,000 children ages 4 and under. One point is one neighborhood.

The printed title is `n=32; r=-0.08080239123990451; p=0.6602110540504577`. Pearson *r* is about **−0.081**. The two-sided p-value is about **0.660**. The 95% confidence interval, which is not drawn, runs from about −0.418 to 0.276. The cloud has no clear upward slope. A few neighborhoods with traffic above about 2,500 vehicles per hour sit at low to moderate asthma rates.

### R2 — Comparison scatter, 2022

`R2_comparison_scatter.png` and `.pdf`

Same axes and the same shared scale as R1, for the **30 eligible neighborhoods in 2022**. 2022 is the latest tested year other than the primary year.

The printed title is `n=30; r=-0.26289269183354286; p=0.16044742549452454`. Pearson *r* is about **−0.263**. The two-sided p-value is about **0.160**. The 95% confidence interval runs from about −0.569 to 0.108. The association is more negative than in 2023 and still includes zero. Because the axis is stretched to fit 2023, most 2022 points sit on the left side of the frame. One neighborhood is near 3,500 vehicles per hour with a low asthma rate.

### R3 — Pearson correlation in every tested year

`R3_correlation_forest.png` and `.pdf`

One dot per tested year. The vertical axis is the year, 2015 at the bottom through 2023 at the top. The horizontal axis is Pearson *r*. The file name says forest; the chart does not draw confidence-interval bars.

The printed title is one line that concatenates `n`, `r`, and `p` for every year, and the line is clipped by the frame. Read the coefficients here:

| Year | Role | Neighborhoods | Pearson *r* | Two-sided *p* | 95% interval |
| --- | --- | ---: | ---: | ---: | --- |
| 2015 | secondary | 37 | −0.181 | 0.284 | −0.477 to 0.152 |
| 2016 | secondary | 39 | −0.283 | 0.081 | −0.550 to 0.035 |
| 2017 | secondary | 35 | −0.039 | 0.823 | −0.368 to 0.298 |
| 2018 | secondary | 36 | +0.113 | 0.513 | −0.224 to 0.425 |
| 2019 | secondary | 35 | −0.108 | 0.539 | −0.426 to 0.234 |
| 2022 | secondary | 30 | −0.263 | 0.160 | −0.569 to 0.108 |
| 2023 | primary | 32 | −0.081 | 0.660 | −0.418 to 0.276 |

2018 is the only positive correlation. 2016 is the most negative and the closest to 0.05. Every interval includes 0.

### R4 — Pearson and Spearman for the primary year, 2023

`R4_pearson_spearman.png` and `.pdf`

Two points for the **same 32 eligible neighborhoods in 2023**. Blue is the Pearson correlation of the raw values. Orange is the Spearman correlation of the ranks. There is no other year on this chart.

The printed title is `Pearson r=-0.08080239123990451; Spearman rho=0.08926771185874081`. Pearson *r* is about **−0.081** (p about 0.660). Spearman *rho* is about **+0.089** (p about 0.627). The signs disagree, and both coefficients are close to zero. The rank correlation does not support a positive association any more than the Pearson test does.

---

## Neighborhood paths

These are pictures of three neighborhoods over the included years. Files are in `outputs/figures/neighborhood/`. `scripts/11_neighborhood_path.py` reads `final_merged_dataset.csv` and writes them. It does not change S1–S7 or R1–R4.

Each title adds a Pearson correlation of that neighborhood's own valid years: `traffic_mean_vehicles_per_hour` with `asthma_ed_rate_per_10000`, two-sided, alpha 0.05. `n` here is the number of valid years, not the number of neighborhoods. `reading` is `positive` or `negative` when the p-value is below 0.05, and `null` when it is not. `sign` is the direction of `r` itself: `positive`, `negative`, or `zero`. A path with fewer than three valid years, with no variation in either measure, or with a perfect correlation, prints `correlation skipped` and does not print an `r` or a `p`.

The three neighborhoods are pinned. Among neighborhoods that are `valid` in all seven included years, they have the highest mean `asthma_ed_rate_per_10000`: Hunts Point - Mott Haven about **617.8**, High Bridge - Morrisania **603.3**, and Crotona - Tremont about **525.0**. A later run does not swap in a different neighborhood.

Each file is two panels that share the year axis. The top panel is `traffic_mean_vehicles_per_hour`. The bottom panel is `asthma_ed_rate_per_10000`. A point is one valid year. 2020 and 2021 are excluded from the study and are not on the axis. These three neighborhoods are valid in every included year, so each panel has seven points: 2015, 2016, 2017, 2018, 2019, 2022, 2023. The traffic mean is whichever eligible segments were counted that year, so a jump can be a change in the counted roads as well as a change in volume.

### N107 — Hunts Point - Mott Haven

`N107_hunts_point_mott_haven.png` and `.pdf`

The printed title is `107 Hunts Point - Mott Haven; n=7; r=0.2521154144149449; p=0.5854673366282412; reading=null; sign=positive`. Pearson *r* is about **0.252**. The two-sided p-value is about **0.585**. The reading is **null**: the estimate is positive, and the test does not reject a correlation of 0. The 95% interval, which is not drawn, runs from about −0.618 to 0.845 and includes 0. The asthma rate falls from 876.5 per 10,000 in 2015 to 347.2 in 2023. Traffic is 837.3 vehicles per hour in 2015, 9.9 in 2019 (one eligible segment), and 771.8 in 2023 (two segments).

| Year | Traffic mean | Asthma rate | Eligible segments |
| --- | ---: | ---: | ---: |
| 2015 | 837.3 | 876.5 | 6 |
| 2016 | 318.3 | 862 | 12 |
| 2017 | 403.5 | 681.1 | 5 |
| 2018 | 279.4 | 613.6 | 6 |
| 2019 | 9.9 | 529.2 | 1 |
| 2022 | 41.6 | 415.2 | 2 |
| 2023 | 771.8 | 347.2 | 2 |

### N106 — High Bridge - Morrisania

`N106_high_bridge_morrisania.png` and `.pdf`

The printed title is `106 High Bridge - Morrisania; n=7; r=-0.3070299258623036; p=0.5029859132372803; reading=null; sign=negative`. Pearson *r* is about **−0.307**. The two-sided p-value is about **0.503**. The reading is **null**: the estimate is negative, and the test does not reject a correlation of 0. The 95% interval runs from about −0.861 to 0.580 and includes 0. The asthma rate falls from 846.6 per 10,000 in 2015 to 383.2 in 2023. Traffic peaks at 1,269.8 vehicles per hour in 2018, the year with one eligible segment, and is 908.8 in 2023.

| Year | Traffic mean | Asthma rate | Eligible segments |
| --- | ---: | ---: | ---: |
| 2015 | 237.8 | 846.6 | 10 |
| 2016 | 320.9 | 790.6 | 8 |
| 2017 | 475.0 | 608 | 5 |
| 2018 | 1,269.8 | 603.3 | 1 |
| 2019 | 220.0 | 492 | 1 |
| 2022 | 211.6 | 499.4 | 1 |
| 2023 | 908.8 | 383.2 | 2 |

### N105 — Crotona - Tremont

`N105_crotona_tremont.png` and `.pdf`

The printed title is `105 Crotona - Tremont; n=7; r=-0.5018641600309038; p=0.25111829952661907; reading=null; sign=negative`. Pearson *r* is about **−0.502**. The two-sided p-value is about **0.251**. The reading is **null**: the estimate is negative, and the test does not reject a correlation of 0. The 95% interval runs from about −0.911 to 0.404 and includes 0. The asthma rate falls from 737.6 per 10,000 in 2015 to 389.7 in 2023. Traffic is 945.9 vehicles per hour in 2019 (one eligible segment) and 540.8 in 2023 (18 segments).

| Year | Traffic mean | Asthma rate | Eligible segments |
| --- | ---: | ---: | ---: |
| 2015 | 380.6 | 737.6 | 4 |
| 2016 | 123.9 | 695 | 1 |
| 2017 | 259.4 | 499.7 | 4 |
| 2018 | 301.8 | 477 | 1 |
| 2019 | 945.9 | 433.8 | 1 |
| 2022 | 362.7 | 442.3 | 2 |
| 2023 | 540.8 | 389.7 | 18 |
