"""Protocol v1 finite-sample statistics without simulator dependencies."""

import math
import random
from statistics import median

METRIC_REVISION = "cellio.metrics/1.0"


def quantile(values, p):
    ordered = sorted(values)
    if not ordered:
        return None
    h = (len(ordered) - 1) * p
    lo, hi = math.floor(h), math.ceil(h)
    return ordered[lo] + (h - lo) * (ordered[hi] - ordered[lo])


def distribution(values, unit, source_ids=(), exclusions=None):
    values = [v for v in values if type(v) in (int, float) and math.isfinite(v)]
    return {
        "numerator": sum(values) if values else None,
        "denominator": len(values),
        "unit": unit,
        "source_artifact_ids": list(source_ids),
        "metric_revision": METRIC_REVISION,
        "exclusions_with_reason": exclusions or {},
        "median": quantile(values, 0.5),
        "p95": quantile(values, 0.95),
        "p99": quantile(values, 0.99),
        "max": max(values, default=None),
    }


def ratio(n, d, ids=(), exclusions=None):
    return {
        "numerator": n,
        "denominator": d,
        "value": n / d if d else None,
        "unit": "fraction",
        "metric_revision": METRIC_REVISION,
        "source_artifact_ids": list(ids),
        "exclusions_with_reason": exclusions or {},
    }


def block_bootstrap(blocks, n_resamples=10000, seed=20261118):
    """Each (numerator, denominator) block already contains ALL repeats for one state."""
    blocks = tuple(blocks)
    rng = random.Random(seed)
    values = []
    if blocks and len(set(blocks)) == 1:
        n, d = blocks[0]
        values = [n / d] * n_resamples if d else []
    elif blocks:
        for _ in range(n_resamples):
            sample = rng.choices(blocks, k=len(blocks))
            denom = sum(b[1] for b in sample)
            if denom:
                values.append(sum(b[0] for b in sample) / denom)
    return {
        "blocks": len(blocks),
        "draws": n_resamples,
        "seed": seed,
        "lower_95": quantile(values, 0.025),
        "upper_95": quantile(values, 0.975),
        "undefined_resample_fraction": 1 - len(values) / n_resamples
        if n_resamples
        else None,
    }


def plateau(samples, kind):
    """First/last 20 post-warmup observations and robust slope in bytes per episode."""
    samples = [(i, x) for i, x in samples if type(x) in (int, float)]
    if len(samples) < 40:
        return {
            "count": len(samples),
            "first20_median": None,
            "last20_median": None,
            "delta_bytes": None,
            "theil_sen_bytes_per_episode": None,
            "alert": None,
            "reason": "fewer than 40 measured episodes",
        }
    a = median(x for _, x in samples[:20])
    b = median(x for _, x in samples[-20:])
    slopes = [
        (y2 - y1) / (x2 - x1)
        for i, (x1, y1) in enumerate(samples)
        for x2, y2 in samples[i + 1 :]
        if x2 != x1
    ]
    threshold = max(256 * 2**20, 0.05 * a) if kind == "rss" else 256 * 2**20
    return {
        "count": len(samples),
        "first20_median": a,
        "last20_median": b,
        "delta_bytes": b - a,
        "theil_sen_bytes_per_episode": median(slopes) if slopes else None,
        "alert": b - a > threshold,
        "threshold_bytes": threshold,
    }
