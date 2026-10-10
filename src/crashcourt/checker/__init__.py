"""The checker: trusted records in, result.json and checker.json out.

Checker v1, frozen: FROZEN.md records it. See crashcourt.checker.status.
"""

from crashcourt.checker.engine import CheckerOutput, check_trial
from crashcourt.checker.status import CHECKER_VERSION, FREEZE_STATE, NotAuthorized

__all__ = ["CHECKER_VERSION", "FREEZE_STATE", "CheckerOutput", "NotAuthorized", "check_trial"]
