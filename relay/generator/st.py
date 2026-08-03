from __future__ import annotations

from relay.generator.behavior import compile_plc, parse_triggers
from relay.spec.schema import TaskSpec


def compile_st_blocks(spec: TaskSpec) -> dict[str, str]:
    behavior = spec.behavior
    return {
        plc_id: compile_plc(parse_triggers(behavior.get(plc_id) or {}), spec)
        for plc_id in spec.plc_ids
    }
