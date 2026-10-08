"""String-valued enums with identical behavior on supported Python versions."""

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return str(self.value)

    def __format__(self, format_spec: str) -> str:
        return format(self.value, format_spec)
