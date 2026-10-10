"""The sentences a cell's result is stated in, and the linter every generated sentence passes.

A result is a count with its interval: "0 of 300 trials showed a lost acknowledgment (95% upper
bound 1.0% by the rule of three)". Words that claim more than a count can show are rejected:
"safe", "proven", "no bugs", and "never".
"""

from __future__ import annotations

import re

from crashcourt.stats.intervals import rule_of_three, wilson

#: Words a generated sentence may not contain, matched as whole words, case-insensitively.
FORBIDDEN: tuple[str, ...] = ("safe", "safely", "safety", "proven", "proves", "no bugs", "never")

_PATTERN = re.compile(r"\b(" + "|".join(re.escape(w) for w in FORBIDDEN) + r")\b", re.IGNORECASE)


class WordingError(ValueError):
    """A sentence claims more than its counts can show."""


def lint(text: str) -> list[str]:
    """Every forbidden word in `text`, in order; empty means the sentence may be published."""
    return [m.group(0) for m in _PATTERN.finditer(text)]


def checked(text: str) -> str:
    """`text`, or WordingError naming the forbidden words it uses."""
    found = lint(text)
    if found:
        raise WordingError(f"forbidden wording {found}: {text!r}")
    return text


def _percent(x: float) -> str:
    return f"{x * 100:.1f}%"


def count_sentence(k: int, n: int, what: str) -> str:
    """k of n with its interval, in the one form results are stated in."""
    interval = wilson(k, n)
    if k == 0:
        sentence = (
            f"0 of {n} trials showed {what} (95% upper bound {_percent(rule_of_three(n))} by the "
            f"rule of three; Wilson 95% interval 0.00 to {interval.high:.2f})"
        )
    else:
        sentence = (
            f"{k} of {n} trials showed {what} "
            f"(Wilson 95% interval {interval.low:.2f} to {interval.high:.2f})"
        )
    return checked(sentence)
