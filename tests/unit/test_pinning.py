from __future__ import annotations

from pathlib import Path

import pytest

from crashcourt.pinning import main, normalize_repo, repo_of, rewrite_compose, rewrite_dockerfile

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


def test_line_endings_are_preserved() -> None:
    text, _ = rewrite_compose(COMPOSE.replace("\n", "\r\n"), lambda ref: DA)
    assert f"    image: postgres:18@{DA}  # current stable major\r\n" in text
    assert text.count("\r\n") == COMPOSE.count("\n")


def test_main_rewrites_the_file_without_changing_line_endings(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_bytes(COMPOSE.encode())
    assert main(compose, lambda ref: DA) == 0
    written = compose.read_bytes()
    assert b"\r" not in written
    assert written == rewrite_compose(COMPOSE, lambda ref: DA)[0].encode()


def test_malformed_digest_is_rejected() -> None:
    with pytest.raises(ValueError):
        rewrite_compose(COMPOSE, lambda ref: "latest")


def test_repo_names() -> None:
    assert repo_of("postgres:18") == "postgres"
    assert repo_of("localhost:5000/team/img:1.0") == "localhost:5000/team/img"
    assert repo_of("ghcr.io/shopify/toxiproxy") == "ghcr.io/shopify/toxiproxy"
    assert normalize_repo("docker.io/library/postgres") == "postgres"


DOCKERFILE = """FROM ghcr.io/astral-sh/uv:0.12.1-python3.12-trixie-slim AS build
RUN echo FROM nothing
FROM python:3.12-slim  # runtime
"""


def test_dockerfile_base_images_are_pinned() -> None:
    text, changes = rewrite_dockerfile(DOCKERFILE, lambda ref: DA)
    assert f"FROM ghcr.io/astral-sh/uv:0.12.1-python3.12-trixie-slim@{DA} AS build\n" in text
    assert "RUN echo FROM nothing\n" in text
    assert f"FROM python:3.12-slim@{DA}  # runtime\n" in text
    assert len(changes) == 2


def test_main_pins_the_harness_dockerfile_next_to_compose(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_bytes(COMPOSE.encode())
    (tmp_path / "Dockerfile.harness").write_bytes(DOCKERFILE.encode())
    assert main(compose, lambda ref: DB) == 0
    assert f"@{DB} AS build" in (tmp_path / "Dockerfile.harness").read_text(encoding="utf-8")
