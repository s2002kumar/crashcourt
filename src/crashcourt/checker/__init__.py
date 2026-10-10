"""The checker: trusted records in, result.json and checker.json out.

PRE-FREEZE / NOT AUTHORIZED FOR NEW-EVIDENCE CLASSIFICATION. See crashcourt.checker.status.
"""

from crashcourt.checker.engine import CheckerOutput, check_trial
from crashcourt.checker.status import CHECKER_VERSION, FREEZE_STATE, NotAuthorized

__all__ = ["CHECKER_VERSION", "FREEZE_STATE", "CheckerOutput", "NotAuthorized", "check_trial"]
