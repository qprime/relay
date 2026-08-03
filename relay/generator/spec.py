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
from relay.generator.behavior import EDGES, MODES
from relay.spec.schema import TaskSpec
from relay.strategies.comm import get_comm_strategy, build_comm_strategy
from relay.strategies.st_syntax import SCRATCH_PREFIX, SEND_PREFIX
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
Assertions is a list of strings using EVENTUALLY(signal, within: Nms) or PRECEDES(a, b) forms.

Behavior maps each PLC id to a 'triggers' list. This is a structured IR compiled
directly to ST — there is no free-form rule text. Each trigger is:

  - id: <snake_case, unique within this PLC>
    when:
      signal: <a Plant route as_key targeting this PLC, or a Comm tag it consumes>
      edge: rising | falling | level
      debounce_ms: <int >= 0, optional; source must hold stable this long>
    emit:
      tag: <a Comm tag this PLC produces>      # exactly one of tag / output
      output: <snake_case local output name>
      mode: latched | pulse | steady
      duration_ms: <int > 0, required iff mode is pulse>

edge 'rising' fires on the false-to-true transition, 'falling' on true-to-false,
'level' fires while the signal is true. mode 'latched' sets the target once and
holds it, 'steady' follows the condition down, 'pulse' asserts for duration_ms.
One trigger per emit target. Express every behavior with these primitives; do
not invent fields.
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

    emit_targets = _validate_behavior(spec, plc_ids, issues)

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

    _validate_assertion_coverage(spec, emit_targets, issues)

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


def _plant_route_keys(spec: TaskSpec) -> dict[str, set[str]]:
    by_plc: dict[str, set[str]] = {}
    for r in spec.plant_block.get("routes", []) or []:
        if not isinstance(r, dict):
            continue
        as_key, to_plc = r.get("as_key"), r.get("to_plc")
        if as_key and to_plc:
            by_plc.setdefault(to_plc, set()).add(as_key)
    return by_plc


def _tag_index(spec: TaskSpec) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    produced: dict[str, set[str]] = {}
    consumed: dict[str, set[str]] = {}
    for t in spec.comm_block.get("tags", []) or []:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        producer = t.get("produced_by")
        if producer:
            produced.setdefault(producer, set()).add(name)
        for c in t.get("consumed_by") or []:
            consumed.setdefault(c, set()).add(name)
    return produced, consumed


def _validate_behavior(
    spec: TaskSpec, plc_ids: tuple[str, ...], issues: list[str]
) -> set[str]:
    emit_targets: set[str] = set()
    behavior = spec.raw.get("Behavior", {})
    if not isinstance(behavior, dict):
        issues.append("Behavior block must be a mapping")
        return emit_targets

    routes_by_plc = _plant_route_keys(spec)
    produced_by_plc, consumed_by_plc = _tag_index(spec)
    valid_ids = set(plc_ids)

    for plc_id, entry in behavior.items():
        if plc_id not in valid_ids:
            issues.append(f"Behavior.{plc_id} is not a declared plc_id")
        if not isinstance(entry, dict):
            issues.append(f"Behavior.{plc_id} must be a mapping")
            continue

        if "on" in entry:
            issues.append(
                f"Behavior.{plc_id}.on is no longer supported; replace the prose rule "
                "with a 'triggers:' list of {id, when: {signal, edge}, emit: {tag|output, mode}}"
            )
        if "owns" in entry:
            issues.append(
                f"Behavior.{plc_id}.owns has been removed; signal ownership derives from "
                "Plant.routes[].to_plc and Comm.tags[].produced_by/consumed_by"
            )

        triggers = entry.get("triggers")
        if not isinstance(triggers, list):
            issues.append(f"Behavior.{plc_id}.triggers must be a list")
            continue

        readable = routes_by_plc.get(plc_id, set()) | consumed_by_plc.get(plc_id, set())
        producible = produced_by_plc.get(plc_id, set())
        seen_ids: set[str] = set()
        seen_targets: set[str] = set()

        for i, raw in enumerate(triggers):
            where = f"Behavior.{plc_id}.triggers[{i}]"
            if not isinstance(raw, dict):
                issues.append(f"{where} must be a mapping")
                continue
            _validate_trigger(
                raw, where, readable, producible, seen_ids, seen_targets,
                emit_targets, issues,
            )

    return emit_targets


def _validate_trigger(
    raw: dict,
    where: str,
    readable: set[str],
    producible: set[str],
    seen_ids: set[str],
    seen_targets: set[str],
    emit_targets: set[str],
    issues: list[str],
) -> None:
    trigger_id = raw.get("id")
    if not trigger_id or not isinstance(trigger_id, str):
        issues.append(f"{where}.id is required and must be a string")
    elif not _NAME_RE.fullmatch(trigger_id):
        issues.append(f"{where}.id {trigger_id!r} must match [a-z][a-z0-9_]*")
    elif trigger_id in seen_ids:
        issues.append(f"{where}.id {trigger_id!r} is duplicated within this PLC")
    else:
        seen_ids.add(trigger_id)

    when = raw.get("when")
    if not isinstance(when, dict):
        issues.append(f"{where}.when must be a mapping")
    else:
        signal = when.get("signal")
        if not signal or not isinstance(signal, str):
            issues.append(f"{where}.when.signal is required and must be a string")
        elif signal not in readable:
            known = ", ".join(sorted(readable)) or "(none)"
            issues.append(
                f"{where}.when.signal {signal!r} does not resolve to a Plant route "
                f"as_key for this PLC or a Comm tag it consumes; readable signals: {known}"
            )
        edge = when.get("edge")
        if edge not in EDGES:
            issues.append(f"{where}.when.edge must be one of {list(EDGES)}, got {edge!r}")
        debounce = when.get("debounce_ms", 0)
        if not isinstance(debounce, int) or isinstance(debounce, bool) or debounce < 0:
            issues.append(f"{where}.when.debounce_ms must be an integer >= 0, got {debounce!r}")

    emit = raw.get("emit")
    if not isinstance(emit, dict):
        issues.append(f"{where}.emit must be a mapping")
        return

    has_tag, has_output = "tag" in emit, "output" in emit
    if has_tag and has_output:
        issues.append(f"{where}.emit must set exactly one of 'tag' or 'output', not both")
    elif not (has_tag or has_output):
        issues.append(f"{where}.emit must set exactly one of 'tag' or 'output'")
    elif has_tag:
        tag = emit.get("tag")
        if tag not in producible:
            known = ", ".join(sorted(producible)) or "(none)"
            issues.append(
                f"{where}.emit.tag {tag!r} is not a Comm tag produced by this PLC; "
                f"produced tags: {known}"
            )
        _note_target(tag, where, seen_targets, emit_targets, issues)
    else:
        output = emit.get("output")
        if not output or not isinstance(output, str):
            issues.append(f"{where}.emit.output must be a string")
        elif output.startswith(SEND_PREFIX) or output.startswith(SCRATCH_PREFIX):
            issues.append(
                f"{where}.emit.output {output!r} uses a reserved prefix "
                f"({SEND_PREFIX!r} routes comm tags, {SCRATCH_PREFIX!r} is compiler bookkeeping)"
            )
        elif not _NAME_RE.fullmatch(output):
            issues.append(f"{where}.emit.output {output!r} must match [a-z][a-z0-9_]*")
        else:
            _note_target(output, where, seen_targets, emit_targets, issues)

    mode = emit.get("mode")
    if mode not in MODES:
        issues.append(f"{where}.emit.mode must be one of {list(MODES)}, got {mode!r}")
    duration = emit.get("duration_ms")
    if mode == "pulse":
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            issues.append(
                f"{where}.emit.duration_ms is required for mode 'pulse' and must be > 0, "
                f"got {duration!r}"
            )
    elif duration is not None:
        issues.append(f"{where}.emit.duration_ms is only valid for mode 'pulse'")


def _note_target(
    target: Any,
    where: str,
    seen_targets: set[str],
    emit_targets: set[str],
    issues: list[str],
) -> None:
    if not isinstance(target, str):
        return
    if target in seen_targets:
        issues.append(
            f"{where} emits {target!r}, which another trigger on this PLC already emits; "
            "one trigger per target"
        )
    seen_targets.add(target)
    emit_targets.add(target)


def _validate_assertion_coverage(
    spec: TaskSpec, emit_targets: set[str], issues: list[str]
) -> None:
    plant_keys = {
        r.get("as_key")
        for r in spec.plant_block.get("routes", []) or []
        if isinstance(r, dict) and r.get("as_key")
    }
    tag_names = {
        t.get("name")
        for t in spec.comm_block.get("tags", []) or []
        if isinstance(t, dict) and t.get("name")
    }
    for signal in sorted(spec.assertion_signals):
        if signal not in emit_targets and signal not in plant_keys and signal not in tag_names:
            issues.append(
                f"assertion signal {signal!r} is not covered by any trigger emit target, "
                "plant route, or tag declaration"
            )
