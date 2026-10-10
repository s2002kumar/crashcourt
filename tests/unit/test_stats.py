from __future__ import annotations

import pytest

from crashcourt.stats import (
    WordingError,
    checked,
    count_sentence,
    detection_probability,
    lint,
    rule_of_three,
    trials_to_detect,
    wilson,
)


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [
        (19, 20, 0.76, 0.99),  # the specification's worked values
        (20, 20, 0.84, 1.00),
        (0, 20, 0.00, 0.16),
    ],
)
def test_wilson_matches_the_known_values(k: int, n: int, low: float, high: float) -> None:
    interval = wilson(k, n)
    assert (round(interval.low, 2), round(interval.high, 2)) == (low, high)


def test_wilson_stays_inside_zero_and_one() -> None:
    for n in (1, 2, 20, 300):
        for k in range(n + 1):
            i = wilson(k, n)
            assert 0.0 <= i.low <= k / n <= i.high <= 1.0


def test_rule_of_three_bounds() -> None:
    assert rule_of_three(300) == pytest.approx(0.01)  # 0 of 300: at most 1.0%
    assert rule_of_three(1000) == pytest.approx(0.003)
    assert rule_of_three(20) == pytest.approx(0.15)


def test_detection_power() -> None:
    assert detection_probability(0.01, 300) == pytest.approx(0.951, abs=0.001)
    assert trials_to_detect(0.005, 0.95) == 598


@pytest.mark.parametrize("bad", [(-1, 10), (11, 10), (0, 0)])
def test_counts_must_make_sense(bad: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="must be"):
        wilson(*bad)


def test_sentences_state_counts_with_their_interval() -> None:
    assert count_sentence(19, 20, "a fork") == (
        "19 of 20 trials showed a fork (Wilson 95% interval 0.76 to 0.99)"
    )
    assert count_sentence(0, 300, "a lost acknowledgment") == (
        "0 of 300 trials showed a lost acknowledgment (95% upper bound 1.0% by the rule of "
        "three; Wilson 95% interval 0.00 to 0.01)"
    )


@pytest.mark.parametrize(
    "text",
    ["The saver is safe.", "Proven durable", "no bugs found", "It never forks", "SAFE"],
)
def test_the_linter_rejects_overclaiming(text: str) -> None:
    assert lint(text)
    with pytest.raises(WordingError):
        checked(text)


def test_the_linter_passes_counts_and_words_that_only_contain_a_forbidden_one() -> None:
    assert lint("0 of 300 trials showed a fork (95% upper bound 1.0%)") == []
    assert lint("unsafe retries; nevertheless; improvements") == []
