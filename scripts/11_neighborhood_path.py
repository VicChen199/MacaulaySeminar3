"""Draw one time path for each of three pinned neighborhoods.

The charts show traffic_mean_vehicles_per_hour and asthma_ed_rate_per_10000
for years with coverage_status valid. The title prints that path's Pearson r,
its two-sided p-value, and a reading of positive, negative, or null.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from lib.plots import neighborhood_path_figures

_MERGED = Path("outputs/data/final_merged_dataset.csv")
_FIGURES = Path("outputs/figures/neighborhood")


def _read_table(path):
    if not path.is_file():
        print(path.as_posix())
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def main():
    merged = _read_table(_MERGED)
    if merged is None:
        return 2
    paths = neighborhood_path_figures(merged, _FIGURES)
    for path in paths:
        print(Path(path).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
