from __future__ import annotations
import argparse
from pathlib import Path
from typing import Any

import yaml

import relay.plant  # noqa: F401  -- trigger conveyor registration
from relay.generator.errors import SpecValidationError
from relay.generator.spec import validate_spec
from relay.spec.schema import TaskSpec
from relay.strategies.comm import get_comm_strategy
from relay.strategies.plant import UnknownPlantType, get_plant

_TERMINAL_NOTE = (
    "validation stopped early: the declared strategy or plant type must resolve "
    "before any other check is meaningful, so no other issues were collected"
)


def _declared(block: Any, key: str) -> str | None:
    value = block.get(key) if isinstance(block, dict) else None
    return value if isinstance(value, str) else None


def _unresolvable_selector(raw: dict[str, Any]) -> str | None:
    comm_name = _declared(raw.get("Comm"), "strategy")
    plant_name = _declared(raw.get("Plant"), "type")
    try:
        if comm_name is not None:
            get_comm_strategy(comm_name)
        if plant_name is not None:
            get_plant(plant_name)
    except (ValueError, UnknownPlantType) as e:
        return str(e)
    return None


def _validate(spec_path: Path | str) -> tuple[list[str], bool]:
    try:
        text = Path(spec_path).read_text()
    except OSError as e:
        return [f"cannot read {spec_path}: {e}"], False
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"], False
    if not isinstance(raw, dict):
        return ["top-level YAML must be a mapping"], False

    terminal = _unresolvable_selector(raw)
    if terminal is not None:
        return [terminal], True

    try:
        validate_spec(TaskSpec(raw=raw))
    except SpecValidationError as e:
        return list(e.issues), False
    return [], False


def validate_spec_file(spec_path: Path | str) -> list[str]:
    return _validate(spec_path)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a task spec YAML against the full spec validator"
    )
    parser.add_argument("spec", type=Path)
    args = parser.parse_args(argv)
    issues, terminal = _validate(args.spec)
    if not issues:
        print(f"{args.spec}: ok")
        return 0
    for issue in issues:
        print(issue)
    if terminal:
        print(_TERMINAL_NOTE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
