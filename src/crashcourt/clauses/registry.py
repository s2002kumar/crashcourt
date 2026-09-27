"""Load and validate the clause registry (`clauses/**/*.yaml`)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from crashcourt.clauses.models import Clause, Polarity


class RegistryError(Exception):
    """Every problem found while loading, one per line, so a single run reports all of them."""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("\n".join(self.problems))


def _format_validation(error: ValidationError) -> list[str]:
    lines: list[str] = []
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"]) or "(file)"
        lines.append(f"{where}: {item['msg']}")
    return lines


class ClauseRegistry:
    """An immutable, id-unique set of clauses, iterated in id order."""

    def __init__(self, clauses: Iterable[Clause]) -> None:
        by_id: dict[str, Clause] = {}
        duplicates: list[str] = []
        for clause in clauses:
            if clause.id in by_id:
                duplicates.append(f"duplicate id {clause.id}")
            by_id[clause.id] = clause
        if duplicates:
            raise RegistryError(duplicates)
        self._by_id = dict(sorted(by_id.items()))

    @classmethod
    def load(cls, root: Path) -> ClauseRegistry:
        paths = sorted(root.rglob("*.yaml"))
        if not paths:
            raise RegistryError([f"{root}: no clause files (*.yaml) found"])
        problems: list[str] = []
        clauses: list[Clause] = []
        seen: dict[str, Path] = {}
        for path in paths:
            name = path.relative_to(root).as_posix()
            try:
                data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, ValueError) as exc:
                # PyYAML raises ValueError for impossible unquoted dates such as 2026-13-01.
                problems.append(f"{name}: invalid YAML: {exc}")
                continue
            if not isinstance(data, dict):
                problems.append(f"{name}: must be a mapping of clause fields")
                continue
            try:
                clause = Clause.model_validate(data)
            except ValidationError as exc:
                problems.extend(f"{name}: {line}" for line in _format_validation(exc))
                continue
            if path.stem != clause.id:
                problems.append(f"{name}: file name does not match id {clause.id}")
            if clause.id in seen:
                problems.append(
                    f"{name}: duplicate id {clause.id} (also in "
                    f"{seen[clause.id].relative_to(root).as_posix()})"
                )
                continue
            seen[clause.id] = path
            clauses.append(clause)
        if problems:
            raise RegistryError(problems)
        return cls(clauses)

    def get(self, clause_id: str) -> Clause:
        try:
            return self._by_id[clause_id]
        except KeyError:
            raise KeyError(f"no clause with id {clause_id}") from None

    def by_polarity(self, polarity: Polarity) -> tuple[Clause, ...]:
        return tuple(c for c in self._by_id.values() if c.polarity is polarity)

    def __iter__(self) -> Iterator[Clause]:
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)
