"""The checker's freeze state, and the guard that kept the pre-freeze checker off new evidence.

FROZEN: checker v1, recorded in FROZEN.md (my freeze of 2026-10-10, after the landmark audit on
T-09's real fault-free histories). Before the freeze, the checker was developed and run only
against synthetic histories, artifact mutants, harness controls, the three already-published
replications (langgraph#8039, #8796, #8764), and T-09's fault-free histories, and this module
refused in code any trial that declared itself new evidence or named a matrix cell outside that
set. The freeze commit must be an ancestor of the first new-evidence batch. Frozen, the checker
classifies any trial, but only after verifying itself against FROZEN.md (crashcourt.checker.engine);
any change to it is v2.
"""

from __future__ import annotations

from typing import Literal

#: The checker version results are stamped with. A change after the freeze makes v2.
CHECKER_VERSION = "v1"

FreezeState = Literal["PRE-FREEZE", "FROZEN"]
#: Set by the freeze commit, together with FROZEN.md.
FREEZE_STATE: FreezeState = "FROZEN"

EvidenceClass = Literal["synthetic", "artifact-mutant", "control", "replication", "new-evidence"]

#: What a pre-freeze checker may classify.
PRE_FREEZE_EVIDENCE: frozenset[str] = frozenset(
    {"synthetic", "artifact-mutant", "control", "replication"}
)

#: The published results a replication cell may reproduce before the freeze. The #9185 shape stays
#: prior art for T-12 and is not a pre-freeze input.
PUBLISHED_REPLICATIONS: frozenset[str] = frozenset(
    {"langgraph#8039", "langgraph#8796", "langgraph#8764"}
)

#: First-matrix cells that are not controls or published replications: the new-evidence cells,
#: K1b (the crash form of the pending-writes claim, not a published result), and the durability
#: variants (E1 among them, though it left the first matrix before the freeze). A pre-freeze
#: checker refused a trial naming one of them whatever else it declared.
PRE_FREEZE_REFUSED_CELLS: frozenset[str] = frozenset(
    {
        "K1b",
        *(f"N{n}" for n in range(1, 13)),
        "N12b",
        "A1",
        "A2",
        "A3",
        "A4",
        "E1",
    }
)


class NotAuthorized(Exception):
    """The pre-freeze checker was asked to classify a trial it may not see."""


def authorize(evidence: EvidenceClass, replication_of: str | None, matrix_cell: str | None) -> None:
    """Raise NotAuthorized unless a checker in the current freeze state may classify this trial."""
    if FREEZE_STATE == "FROZEN":
        return
    if evidence not in PRE_FREEZE_EVIDENCE:
        raise NotAuthorized(
            f"PRE-FREEZE checker: {evidence} trials may not be classified before the freeze"
        )
    if matrix_cell is not None and matrix_cell in PRE_FREEZE_REFUSED_CELLS:
        raise NotAuthorized(
            f"PRE-FREEZE checker: cell {matrix_cell} is not a control or a published replication"
        )
    if evidence == "replication" and replication_of not in PUBLISHED_REPLICATIONS:
        raise NotAuthorized(
            f"PRE-FREEZE checker: replication of {replication_of!r} is not one of the published "
            f"results allowed before the freeze ({', '.join(sorted(PUBLISHED_REPLICATIONS))})"
        )
    if evidence != "replication" and replication_of is not None:
        raise NotAuthorized("only a replication trial names the result it replicates")
