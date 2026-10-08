from __future__ import annotations

import pytest

from crashcourt.faults.base import FaultError
from crashcourt.faults.selftest import CHECKS, Check, Trial, _guarded, report, require_local_dsn


def trial(number: int, *, skip: tuple[str, ...] = (), leave_out: tuple[str, ...] = ()) -> Trial:
    checks = [
        Check(name, "skip" if name in skip else "pass") for name in CHECKS if name not in leave_out
    ]
    return Trial(number, checks)


def headline(results: list[Trial]) -> str:
    return report(results).splitlines()[0]


def test_headline_says_when_every_check_ran() -> None:
    assert headline([trial(1), trial(2)]) == (
        "Trials passed: 2 of 2 (a trial passes when no check fails). Skipped: none. Not run: none."
    )


def test_headline_names_skipped_and_omitted_checks() -> None:
    results = [trial(n, skip=("signals",), leave_out=("restart_database",)) for n in (1, 2, 3)]
    assert headline(results) == (
        "Trials passed: 3 of 3 (a trial passes when no check fails). "
        "Skipped: signals in 3 of 3 trials. Not run: restart_database."
    )


def test_a_check_that_raises_fails_with_the_error() -> None:
    def broken() -> Check:
        raise FaultError("no backend with application_name 'x' ended")

    check = _guarded("terminate_backend", broken)
    assert check.status == "fail"
    assert check.observed == {"raised": "FaultError: no backend with application_name 'x' ended"}


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@127.0.0.1:55432/db",
        "postgresql://u:p@localhost/db",
        "host=::1 port=5432 dbname=db",
        "host=/var/run/postgresql dbname=db",
        "host=127.0.0.1,localhost dbname=db",
    ],
)
def test_local_databases_are_accepted(dsn: str) -> None:
    require_local_dsn(dsn)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:secret@db.example.com/db",
        "host=10.0.0.5 dbname=db",
        "host=127.0.0.1,10.0.0.5 dbname=db",
        "host=localhost hostaddr=10.0.0.5 dbname=db",
        "dbname=db",
    ],
)
def test_remote_or_implicit_databases_are_refused(dsn: str) -> None:
    with pytest.raises(FaultError, match="not on this host") as caught:
        require_local_dsn(dsn)
    assert "secret" not in str(caught.value)
