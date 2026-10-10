"""Cell statistics: counts with Wilson intervals, rule-of-three bounds, detection power, and the
wording every published sentence must pass (T-13)."""

from crashcourt.stats.intervals import (
    Interval,
    detection_probability,
    rule_of_three,
    trials_to_detect,
    wilson,
)
from crashcourt.stats.wording import WordingError, checked, count_sentence, lint

__all__ = [
    "Interval",
    "WordingError",
    "checked",
    "count_sentence",
    "detection_probability",
    "lint",
    "rule_of_three",
    "trials_to_detect",
    "wilson",
]
