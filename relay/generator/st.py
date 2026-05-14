from __future__ import annotations
import warnings

import anthropic

from relay.generator.errors import STGenerationFailed, STValidationError
from relay.spec.schema import TaskSpec
from relay.strategies.comm import get_comm_strategy
from relay.strategies.st_syntax import (
    assignment_lhs,
    static_parse_errors,
    ton_presets,
)


_PREAMBLE = """\
You are an IEC 61131-3 Structured Text programmer.
Given a task spec (YAML), generate one ST function block body per PLC.
Output ONLY a JSON object where keys are PLC IDs and values are ST source strings.
No markdown. No explanation. No PLCopen XML.

Rules for the ST subset:
- Variables: simple assignments (name := expr;)
- Conditionals: IF <expr> THEN ... END_IF;
- Timers: TON instances with IN := <bool>, PT := T#<N>ms
- No WHILE, no CASE, no arrays, no function calls except TON/TOF
- Keep it minimal — only what's needed to implement the Behavior section
"""


_SEND_PREFIX = "_send_"


def _compose_system_prompt(spec: TaskSpec) -> str:
    strategy = get_comm_strategy(spec.comm_strategy)
    fragment = getattr(strategy, "STRATEGY_PROMPT_FRAGMENT", "")
    return _PREAMBLE + "\n" + fragment + "\n"


def generate_st_blocks(
    spec: TaskSpec,
    *,
    prior_errors: list[str] | None = None,
) -> dict[str, str]:
    import json
    import yaml

    system_prompt = _compose_system_prompt(spec)
    spec_yaml = yaml.dump(spec.raw, default_flow_style=False)
    user_content = spec_yaml
    if prior_errors:
        user_content = (
            spec_yaml
            + "\nPrevious attempt failed validation with these errors. Fix them:\n  - "
            + "\n  - ".join(prior_errors)
        )

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": user_content}],
        system=system_prompt,
    )
    raw = message.content[0].text  # type: ignore[union-attr]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise STGenerationFailed(raw_output=raw, errors=[f"JSON parse error: {e}"])
    if not isinstance(parsed, dict):
        raise STGenerationFailed(raw_output=raw, errors=["top-level JSON must be an object"])
    return parsed


def validate_st_blocks(spec: TaskSpec, blocks: dict[str, str]) -> None:
    per_plc: dict[str, list[str]] = {}

    spec_ids = set(spec.plc_ids)
    block_ids = set(blocks)
    missing = spec_ids - block_ids
    extra = block_ids - spec_ids
    if missing:
        per_plc.setdefault("__spec__", []).append(
            f"missing ST blocks for plc_ids: {sorted(missing)}"
        )
    if extra:
        per_plc.setdefault("__spec__", []).append(
            f"unexpected ST blocks for unknown plc_ids: {sorted(extra)}"
        )

    plc_local_outputs: dict[str, set[str]] = {}
    plc_send_assignments: dict[str, set[str]] = {}
    for plc_id, source in blocks.items():
        if plc_id not in spec_ids:
            continue
        syntax_errors = static_parse_errors(source)
        if syntax_errors:
            per_plc.setdefault(plc_id, []).extend(
                f"ST parse error: {e}" for e in syntax_errors
            )
            continue
        local: set[str] = set()
        sends: set[str] = set()
        for name in assignment_lhs(source):
            if name.startswith(_SEND_PREFIX):
                sends.add(name)
            else:
                local.add(name)
        plc_local_outputs[plc_id] = local
        plc_send_assignments[plc_id] = sends

        for ton_name, preset in ton_presets(source):
            if preset == 0.0:
                warnings.warn(
                    f"{plc_id}: TON {ton_name!r} declared with PT := T#0ms",
                    UserWarning,
                )

    all_local: set[str] = set()
    for s in plc_local_outputs.values():
        all_local.update(s)

    plant_keys: set[str] = set()
    plant_block = spec.plant_block
    for r in plant_block.get("routes", []) or []:
        as_key = r.get("as_key")
        if as_key:
            plant_keys.add(as_key)

    tag_names: set[str] = set()
    tags = spec.comm_block.get("tags", []) or []
    for t in tags:
        n = t.get("name")
        if n:
            tag_names.add(n)

    for signal in spec.assertion_signals:
        covered = signal in all_local or signal in plant_keys or signal in tag_names
        if not covered:
            per_plc.setdefault("__spec__", []).append(
                f"assertion signal {signal!r} not covered by any PLC output, "
                "plant route, or tag declaration"
            )

    for tag in tags:
        producer = tag.get("produced_by")
        name = tag.get("name")
        consumers = tag.get("consumed_by") or []
        if not producer or not name:
            continue
        sends = plc_send_assignments.get(producer, set())
        expected = {f"_send_{c}_{name}" for c in consumers}
        if not (sends & expected):
            per_plc.setdefault(producer, []).append(
                f"declared tag {name!r} (consumed_by {list(consumers)}) is never emitted; "
                f"expected at least one of: {sorted(expected)}"
            )

    if per_plc:
        raise STValidationError(per_plc=per_plc)
