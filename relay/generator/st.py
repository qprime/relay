from __future__ import annotations

import anthropic

from relay.spec.schema import TaskSpec


_SYSTEM = """\
You are an IEC 61131-3 Structured Text programmer.
Given a task spec (YAML), generate one ST function block body per PLC.
Output ONLY a JSON object where keys are PLC IDs and values are ST source strings.
No markdown. No explanation. No PLCopen XML.

Rules for the ST subset:
- Variables: simple assignments (name := expr;)
- Conditionals: IF <expr> THEN ... END_IF;
- Timers: TON instances with IN := <bool>, PT := T#<N>ms
- Communication output: set variable _outgoing to a JSON-like list of [target_plc, key, value] triples
- No WHILE, no CASE, no arrays, no function calls except TON/TOF
- Keep it minimal — only what's needed to implement the Behavior section
"""


def generate_st_blocks(spec: TaskSpec) -> dict[str, str]:
    import json

    client = anthropic.Anthropic()
    spec_yaml = _spec_to_prompt(spec)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": spec_yaml}],
        system=_SYSTEM,
    )
    raw = message.content[0].text  # type: ignore[union-attr]
    return json.loads(raw)


def _spec_to_prompt(spec: TaskSpec) -> str:
    import yaml
    return yaml.dump(spec.raw, default_flow_style=False)
