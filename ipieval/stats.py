"""Paired comparisons with a template-level cluster bootstrap.

Runs of two conditions are paired by (case_id, seed). The point estimate is
the mean paired difference. The 95% interval resamples task templates with
replacement and keeps every pair of a sampled template together (its
instances, variants, and seeds), because outcomes within a template are
correlated. With few templates the interval is approximate, so it is
reported as exploratory together with the raw counts.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass
class PairedResult:
    metric: str
    x: str
    y: str
    n_pairs: int
    n_templates: int
    rate_x: float
    rate_y: float
    events_x: int
    events_y: int
    diff: float
    lo: Optional[float]
    hi: Optional[float]
    discordant_x_only: int
    discordant_y_only: int
    informative: bool


def pair_runs(rows: Iterable[Dict], metric: str, x: str, y: str) -> List[Tuple[str, int, int]]:
    """Return (template_id, x_value, y_value) for every (case_id, seed) present in both."""
    by_key: Dict[Tuple[str, int], Dict[str, Dict]] = defaultdict(dict)
    for r in rows:
        by_key[(r["case_id"], r["seed"])][r["condition"]] = r
    pairs = []
    for key in sorted(by_key):
        runs = by_key[key]
        if x in runs and y in runs:
            pairs.append((runs[x]["template_id"], int(bool(runs[x][metric])),
                          int(bool(runs[y][metric]))))
    return pairs


def cluster_bootstrap(pairs: Sequence[Tuple[str, int, int]], n_boot: int = 10000,
                      seed: int = 0, alpha: float = 0.05) -> Tuple[float, float]:
    clusters: Dict[str, List[int]] = defaultdict(list)
    for template, a, b in pairs:
        clusters[template].append(a - b)
    sums = {t: (sum(d), len(d)) for t, d in clusters.items()}
    templates = sorted(sums)
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        total = count = 0
        for _ in templates:
            s, n = sums[templates[rng.randrange(len(templates))]]
            total += s
            count += n
        stats.append(total / count)
    stats.sort()
    lo = stats[int((alpha / 2) * n_boot)]
    hi = stats[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return lo, hi


def paired_comparison(rows: Iterable[Dict], metric: str, x: str, y: str,
                      n_boot: int = 10000, seed: int = 0) -> PairedResult:
    pairs = pair_runs(rows, metric, x, y)
    n = len(pairs)
    templates = {t for t, _, _ in pairs}
    ex = sum(a for _, a, _ in pairs)
    ey = sum(b for _, _, b in pairs)
    x_only = sum(1 for _, a, b in pairs if a and not b)
    y_only = sum(1 for _, a, b in pairs if b and not a)
    diff = (ex - ey) / n if n else float("nan")
    # With no discordant pairs every resample gives a difference of exactly 0,
    # so the interval carries no information about the size of an effect.
    informative = n > 0 and (x_only + y_only) > 0 and len(templates) > 1
    lo = hi = None
    if informative:
        lo, hi = cluster_bootstrap(pairs, n_boot=n_boot, seed=seed)
    return PairedResult(
        metric=metric, x=x, y=y, n_pairs=n, n_templates=len(templates),
        rate_x=ex / n if n else float("nan"), rate_y=ey / n if n else float("nan"),
        events_x=ex, events_y=ey, diff=diff, lo=lo, hi=hi,
        discordant_x_only=x_only, discordant_y_only=y_only, informative=informative,
    )


def rate(rows: Sequence[Dict], metric: str) -> Tuple[int, int]:
    """(events, n) for a boolean metric, ignoring rows where it is None."""
    values = [r[metric] for r in rows if r[metric] is not None]
    return sum(bool(v) for v in values), len(values)
