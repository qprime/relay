from __future__ import annotations
import re

import anthropic
import yaml

from relay.generator.errors import (
    SpecGenerationFailed,
    SpecValidationError,
    UnknownCommStrategy,
    UnknownPlantType,
)
from relay.spec.schema import TaskSpec
from relay.strategies.comm import get_comm_strategy, build_comm_strategy
from relay.strategies.plant import (
    UnknownPlantType as _PlantNotRegistered,
    get_plant,
    get_plant_prompt_fragment,
)
from relay.strategies.assertions import parse_assertion


_PREAMBLE = """\
You are an IEC 61131-3 control systems engineer.
Given a natural language description of a control task, produce a structured YAML task spec.
Output ONLY valid YAML. No explanation, no markdown fences.

The YAML must have these top-level keys: System, Comm, Plant, Behavior, Assertions.
System.name is a short identifier (snake_case) for the scenario.
System.plcs is a list of {id, role}.
Behavior maps each PLC id to: owns (list of I/O names), on (event-action rule as string).
Assertions is a list of strings using EVENTUALLY(signal, within: Nms) or PRECEDES(a, b) forms.
"""


_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")


def _strategy_fragment(name: str) -> str:
    try:
        strat = get_comm_strategy(name)
    except ValueError as e:
        raise UnknownCommStrategy(str(e)) from None
    return getattr(strat, "STRATEGY_PROMPT_FRAGMENT", "")


def _plant_fragment(name: str) -> str:
    try:
        get_plant(name)
    except _PlantNotRegistered as e:
        raise UnknownPlantType(str(e)) from None
    return get_plant_prompt_fragment(name)


def _compose_system_prompt(comm_strategy: str, plant_type: str) -> str:
    return (
        _PREAMBLE
        + "\n"
        + _strategy_fragment(comm_strategy)
        + "\n"
        + _plant_fragment(plant_type)
    )


def generate_spec_yaml(
    intent: str,
    *,
    comm_strategy: str,
    plant_type: str,
    prior_errors: list[str] | None = None,
) -> str:
    system_prompt = _compose_system_prompt(comm_strategy, plant_type)
    user_content = intent
    if prior_errors:
        user_content = (
            intent
            + "\n\nThe previous attempt failed validation with these errors. Fix them:\n  - "
            + "\n  - ".join(prior_errors)
        )
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": user_content}],
        system=system_prompt,
    )
    return message.content[0].text  # type: ignore[union-attr]


def generate_spec(
    intent: str,
    *,
    comm_strategy: str,
    plant_type: str,
    prior_errors: list[str] | None = None,
) -> TaskSpec:
    raw_yaml = generate_spec_yaml(
        intent,
        comm_strategy=comm_strategy,
        plant_type=plant_type,
        prior_errors=prior_errors,
    )
    try:
        raw = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        raise SpecGenerationFailed(raw_output=raw_yaml, errors=[f"YAML parse error: {e}"])
    if not isinstance(raw, dict):
        raise SpecGenerationFailed(raw_output=raw_yaml, errors=["top-level YAML must be a mapping"])
    return TaskSpec(raw=raw)


def validate_spec(spec: TaskSpec) -> None:
    issues: list[str] = []

    system = spec.raw.get("System")
    if not isinstance(system, dict):
        issues.append("System block is required")
        system = {}

    name = system.get("name")
    if not name or not isinstance(name, str):
        issues.append("System.name is required and must be a string")
    elif not _NAME_RE.fullmatch(name):
        issues.append(f"System.name {name!r} must match [a-z][a-z0-9_]*")

    plcs = system.get("plcs")
    plc_ids: tuple[str, ...] = ()
    if not isinstance(plcs, list) or not plcs:
        issues.append("System.plcs must be a non-empty list")
    else:
        plc_ids = tuple(p.get("id") for p in plcs if isinstance(p, dict))
        for i, p in enumerate(plcs):
            if not isinstance(p, dict):
                issues.append(f"System.plcs[{i}] must be a mapping")
                continue
            pid = p.get("id")
            if not pid or not isinstance(pid, str):
                issues.append(f"System.plcs[{i}].id is required")
            elif not _NAME_RE.fullmatch(pid):
                issues.append(f"System.plcs[{i}].id {pid!r} must match [a-z][a-z0-9_]*")
            if "role" not in p or not isinstance(p["role"], str):
                issues.append(f"System.plcs[{i}].role is required")

    comm_block = spec.raw.get("Comm")
    comm_strategy_name = None
    if not isinstance(comm_block, dict):
        issues.append("Comm block is required")
    else:
        comm_strategy_name = comm_block.get("strategy")
        if not comm_strategy_name:
            issues.append("Comm.strategy is required")

    plant_block = spec.raw.get("Plant")
    plant_type_name = None
    if not isinstance(plant_block, dict):
        issues.append("Plant block is required")
    else:
        plant_type_name = plant_block.get("type")
        if not plant_type_name:
            issues.append("Plant.type is required")

    behavior = spec.raw.get("Behavior", {})
    if not isinstance(behavior, dict):
        issues.append("Behavior block must be a mapping")
    else:
        valid_ids = set(plc_ids)
        for key, entry in behavior.items():
            if key not in valid_ids:
                issues.append(f"Behavior.{key} is not a declared plc_id")
            if not isinstance(entry, dict):
                issues.append(f"Behavior.{key} must be a mapping")
                continue
            owns = entry.get("owns")
            if not isinstance(owns, list):
                issues.append(f"Behavior.{key}.owns must be a list")
            on = entry.get("on")
            if not isinstance(on, str):
                issues.append(f"Behavior.{key}.on must be a string")

    assertions = spec.raw.get("Assertions")
    if not isinstance(assertions, list) or not assertions:
        issues.append("Assertions must be a non-empty list")
    else:
        for i, a in enumerate(assertions):
            if not isinstance(a, str):
                issues.append(f"Assertions[{i}] must be a string")
                continue
            if parse_assertion(a) is None:
                issues.append(
                    f"Assertions[{i}] {a!r} is not a recognized form "
                    "(use EVENTUALLY or PRECEDES)"
                )

    if comm_strategy_name:
        try:
            strategy = build_comm_strategy(comm_strategy_name, comm_block or {})
        except ValueError as e:
            raise UnknownCommStrategy(str(e)) from None
        else:
            try:
                strat_issues = strategy.validate_config(comm_block or {}, spec)
            except NotImplementedError as e:
                issues.append(f"Comm strategy {comm_strategy_name!r}: {e}")
            else:
                issues.extend(strat_issues)

    if plant_type_name:
        try:
            factory = get_plant(plant_type_name)
        except _PlantNotRegistered as e:
            raise UnknownPlantType(str(e)) from None
        try:
            plant = factory(plant_block or {})
        except Exception as e:  # noqa: BLE001
            issues.append(f"Plant factory {plant_type_name!r} raised: {e}")
        else:
            issues.extend(plant.validate_config(plant_block or {}, spec))

    if issues:
        raise SpecValidationError(issues=issues)
