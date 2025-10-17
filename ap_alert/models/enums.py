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


class Filters(enum.Flag):
    none = 0
    trap = 1
    filler = 2
    useful = 4
    progression = 8
    mcguffin = 16
    unset = 32

    everything = trap | filler | useful | progression | mcguffin
    useful_plus = useful | progression | mcguffin
    useful_plus_progression = useful | progression
    progression_plus = progression | mcguffin


class HintFilters(enum.Flag):
    none = 0
    receiver = 1
    finder = 2
    unset = 4

    all = receiver | finder
