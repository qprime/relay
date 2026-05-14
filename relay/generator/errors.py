from __future__ import annotations
from dataclasses import dataclass, field


class GeneratorError(Exception):
    pass


@dataclass
class SpecGenerationFailed(GeneratorError):
    raw_output: str
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"spec generation failed: {self.errors}"


@dataclass
class SpecValidationError(GeneratorError):
    issues: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return "spec validation failed:\n  - " + "\n  - ".join(self.issues)


@dataclass
class STGenerationFailed(GeneratorError):
    raw_output: str
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"ST generation failed: {self.errors}"


@dataclass
class STValidationError(GeneratorError):
    per_plc: dict[str, list[str]] = field(default_factory=dict)

    def __str__(self) -> str:
        chunks = []
        for plc, issues in self.per_plc.items():
            for issue in issues:
                chunks.append(f"[{plc}] {issue}")
        return "ST validation failed:\n  - " + "\n  - ".join(chunks)


class UnknownCommStrategy(GeneratorError):
    pass


class UnknownPlantType(GeneratorError):
    pass
