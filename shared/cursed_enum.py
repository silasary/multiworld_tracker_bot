from enum import StrEnum
from typing import Type, TypeVar

from interactions import get_logger

SELF = TypeVar("SELF")

def _log_type_mismatch(cls, value) -> None:
    get_logger().error(
        f"Class `{cls.__name__}` received an invalid and unexpected value `{value}`"
    )


def _return_cursed_enum(cls: Type[SELF], value) -> SELF:
    # log mismatch
    _log_type_mismatch(cls, value)

    if isinstance(value, int):
        new = int.__new__(cls)
        new._name_ = f"UNKNOWN-TYPE-{value}"
        new._value_ = value
    elif isinstance(value, str):
        new = str.__new__(cls)
        new._name_ = value
        new._value_ = value

    return cls._value2member_map_.setdefault(value, new)

class CursedStrEnum(StrEnum):
    @classmethod
    def _missing_(cls: Type[SELF], value):
        return _return_cursed_enum(cls, value)
