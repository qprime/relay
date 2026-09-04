from __future__ import annotations

from typing import TYPE_CHECKING

from relay.strategies.comm import (
    CommSignal,
    CommTransportConfig,
    RegisterBinding,
    register,
    project_entries,
)

if TYPE_CHECKING:
    from relay.spec.schema import TaskSpec


@register
class TagStrategy:
    name = "tag"

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]:
        tags = comm_block.get("tags", [])
        if not isinstance(tags, list):
            return ["Comm.tags must be a list"]
        issues: list[str] = []
        seen: set[str] = set()
        plc_ids = set(spec.plc_ids)
        for i, tag in enumerate(tags):
            if not isinstance(tag, dict):
                issues.append(f"Comm.tags[{i}] must be a mapping")
                continue
            name = tag.get("name")
            if not isinstance(name, str) or not name:
                issues.append(f"Comm.tags[{i}].name is required and must be a string")
            elif name in seen:
                issues.append(f"Comm.tags[{i}].name {name!r} is duplicated")
            else:
                seen.add(name)
            producer = tag.get("produced_by")
            if not isinstance(producer, str) or producer not in plc_ids:
                issues.append(f"Comm.tags[{i}].produced_by {producer!r} is not a declared plc_id")
            consumers = tag.get("consumed_by")
            if not isinstance(consumers, list) or not consumers:
                issues.append(f"Comm.tags[{i}].consumed_by must be a non-empty list")
            else:
                seen_consumers: set[str] = set()
                for consumer in consumers:
                    if not isinstance(consumer, str) or consumer not in plc_ids:
                        issues.append(
                            f"Comm.tags[{i}].consumed_by entry {consumer!r} is not a declared plc_id"
                        )
                    elif consumer in seen_consumers:
                        issues.append(
                            f"Comm.tags[{i}].consumed_by entry {consumer!r} is duplicated"
                        )
                    else:
                        seen_consumers.add(consumer)
                if producer in seen_consumers:
                    issues.append(f"Comm.tags[{i}].produced_by cannot also consume the signal")
        return issues

    def signals(self, comm_block: dict) -> tuple[CommSignal, ...]:
        return project_entries(comm_block.get("tags"))

    def bindings(self, comm_block: dict) -> dict[str, RegisterBinding]:
        return {}

    def can_bindings(self, comm_block: dict) -> dict:
        return {}

    def transport_config(self, comm_block: dict) -> CommTransportConfig:
        return CommTransportConfig("in_process")
