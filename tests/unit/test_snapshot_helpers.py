"""The snapshot dump's pure helpers (the database path is the integration test's)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crashcourt.snapshot.dump import EPOCH, _ns, redact  # pyright: ignore[reportPrivateUsage]


def test_commit_timestamps_convert_exactly() -> None:
    moment = EPOCH + timedelta(seconds=1_800_000_000, microseconds=123_456)
    assert _ns(moment) == 1_800_000_000_123_456_000
    assert _ns(None) is None
    assert _ns(datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)) == 1_000_000_000


def test_the_recorded_dsn_drops_the_password() -> None:
    recorded = redact("postgresql://crashcourt:secret@127.0.0.1:55432/crashcourt")
    assert "secret" not in recorded and "password" not in recorded
    assert "55432" in recorded and "crashcourt" in recorded
