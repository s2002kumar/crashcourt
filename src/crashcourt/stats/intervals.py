"""Counts with intervals: the only way a cell's result is stated.

A cell is one fixed configuration run N times; k is how many trials showed the anomaly. Every
cell reports k of N with the Wilson 95% score interval, and the rule-of-three bound when k is 0.
Zero anomalies is an upper bound on a rate, never a statement that the rate is zero.

    Wilson:         (p + z^2/2n) / (1 + z^2/n)  +-  z / (1 + z^2/n) * sqrt(p(1-p)/n + z^2/4n^2)
    rule of three:  k = 0 in n trials bounds the rate by 3/n at 95%
    detection:      P(at least one in n | rate p) = 1 - (1 - p)^n; n >= ln(1 - P) / ln(1 - p)

z is 1.96, as the specification states it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Z95 = 1.96


@dataclass(frozen=True)
class Interval:
    low: float
    high: float


def _check(k: int, n: int) -> None:
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if not 0 <= k <= n:
        raise ValueError(f"k must be between 0 and n, got k={k}, n={n}")


def wilson(k: int, n: int, z: float = Z95) -> Interval:
    """The Wilson score interval for k anomalies in n trials, clamped to [0, 1]."""
    _check(k, n)
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z / denominator * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    # At k = 0 and k = n the bound is exactly 0 or 1; floating point would leave it a hair inside.
    low = 0.0 if k == 0 else max(0.0, centre - half)
    high = 1.0 if k == n else min(1.0, centre + half)
    return Interval(low, high)


def rule_of_three(n: int) -> float:
    """The 95% upper bound on the rate when none of n trials showed the anomaly: 3/n."""
    _check(0, n)
    return min(1.0, 3 / n)


def detection_probability(rate: float, n: int) -> float:
    """The chance that n trials show at least one anomaly whose true rate is `rate`."""
    if not 0 < rate < 1:
        raise ValueError(f"rate must be strictly between 0 and 1, got {rate}")
    _check(0, n)
    return 1 - (1 - rate) ** n


def trials_to_detect(rate: float, probability: float) -> int:
    """The fewest trials that show an anomaly of true rate `rate` with at least `probability`."""
    if not 0 < rate < 1 or not 0 < probability < 1:
        raise ValueError("rate and probability must be strictly between 0 and 1")
    return math.ceil(math.log(1 - probability) / math.log(1 - rate))
