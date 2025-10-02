import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enum import StrEnum as CursedStrEnum
else:
    from shared.cursed_enum import CursedStrEnum


OldClassification = enum.Enum("OldClassification", "unknown trap filler useful progression mcguffin")
ProgressionStatus = CursedStrEnum("ProgressionStatus", "unknown bk go soft_bk unblocked")
HintClassification = CursedStrEnum("HintClassification", "unset critical progression qol trash unknown")
HintUpdate = CursedStrEnum("HintUpdate", "none new found classified useless")
TrackerStatus = CursedStrEnum("TrackerStatus", "unknown disconnected connected ready playing goal_completed")
CompletionStatus = CursedStrEnum("CompletionStatus", "unknown incomplete all_checks goal done released")
