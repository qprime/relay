from __future__ import annotations

import anthropic

from relay.spec.schema import TaskSpec


_SYSTEM = """\
You are an IEC 61131-3 control systems engineer.
Given a natural language description of a control task, produce a structured YAML task spec.
Output ONLY valid YAML. No explanation, no markdown fences.

The YAML must have these top-level keys: System, Plant, Behavior, Assertions.
System.name is a short identifier (snake_case) for the scenario.
System.plcs is a list of {id, role}.
System.comm names the inter-PLC comm strategy (e.g. modbus_tcp).
Plant describes physical parameters.
Behavior maps each PLC id to: owns (list of I/O names), on (event → action rules as strings).
Assertions is a list of strings using EVENTUALLY(signal, within: Nms) or PRECEDES(a, b) forms.
"""


def generate_spec_yaml(intent: str) -> str:
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": intent}],
        system=_SYSTEM,
    )
    return message.content[0].text  # type: ignore[union-attr]


def generate_spec(intent: str) -> TaskSpec:
    import yaml
    from relay.spec.schema import TaskSpec

    raw_yaml = generate_spec_yaml(intent)
    raw = yaml.safe_load(raw_yaml)
    return TaskSpec(raw=raw)
