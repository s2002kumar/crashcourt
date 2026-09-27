from __future__ import annotations

import pytest

from crashcourt.pinning import normalize_repo, repo_of, rewrite_compose

DA = "sha256:" + "a" * 64
DB = "sha256:" + "b" * 64

COMPOSE = """services:
  postgres:
    image: postgres:18  # current stable major
    command: ["postgres"]
  toxiproxy:
    image: ghcr.io/shopify/toxiproxy:2.9.0
"""


def test_pins_every_image_and_preserves_comments() -> None:
    digests = {"postgres:18": DA, "ghcr.io/shopify/toxiproxy:2.9.0": DB}
    text, changes = rewrite_compose(COMPOSE, digests.__getitem__)
    assert f"    image: postgres:18@{DA}  # current stable major\n" in text
    assert f"    image: ghcr.io/shopify/toxiproxy:2.9.0@{DB}\n" in text
    assert '    command: ["postgres"]\n' in text
    assert len(changes) == 2


def test_repinning_replaces_old_digest_and_is_idempotent() -> None:
    once, _ = rewrite_compose(COMPOSE, lambda ref: DA)
    twice, changes = rewrite_compose(once, lambda ref: DA)
    assert twice == once and changes == []
    moved, changes = rewrite_compose(once, lambda ref: DB)
    assert DA not in moved and len(changes) == 2


def test_malformed_digest_is_rejected() -> None:
    with pytest.raises(ValueError):
        rewrite_compose(COMPOSE, lambda ref: "latest")


def test_repo_names() -> None:
    assert repo_of("postgres:18") == "postgres"
    assert repo_of("localhost:5000/team/img:1.0") == "localhost:5000/team/img"
    assert repo_of("ghcr.io/shopify/toxiproxy") == "ghcr.io/shopify/toxiproxy"
    assert normalize_repo("docker.io/library/postgres") == "postgres"
