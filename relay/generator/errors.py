from __future__ import annotations
from dataclasses import dataclass, field


class GeneratorError(Exception):
    pass


@dataclass
class SpecValidationError(GeneratorError):
    issues: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return "spec validation failed:\n  - " + "\n  - ".join(self.issues)


class UnknownCommStrategy(GeneratorError):
    pass


class UnknownPlantType(GeneratorError):
    pass
