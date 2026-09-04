from __future__ import annotations

from relay.strategies.comm import (
    CanBinding,
    CommSignal,
    CommTransportConfig,
    RegisterBinding,
    project_entries,
    register,
)
from relay.strategies.comm.tag import TagStrategy


@register
class CanStrategy(TagStrategy):
    name = "can"

    def validate_config(self, comm_block: dict, spec) -> list[str]:
        frames = comm_block.get("frames")
        if not isinstance(frames, list) or not frames:
            return ["Comm.frames must be a non-empty list"]
        issues = super().validate_config({"tags": frames}, spec)
        issues = [issue.replace("Comm.tags", "Comm.frames") for issue in issues]
        baud = comm_block.get("baud_rate")
        if isinstance(baud, bool) or not isinstance(baud, int) or baud <= 0:
            issues.append(f"Comm.baud_rate must be a positive integer, got {baud!r}")
        seen_ids: set[int] = set()
        for i, frame in enumerate(frames):
            if not isinstance(frame, dict):
                continue
            can_id = frame.get("can_id")
            if isinstance(can_id, bool) or not isinstance(can_id, int) or not 0 <= can_id <= 0x7FF:
                issues.append(
                    f"Comm.frames[{i}].can_id must be an integer in [0, 2047], got {can_id!r}"
                )
            elif can_id in seen_ids:
                issues.append(f"Comm.frames[{i}].can_id {can_id:#x} is duplicated")
            else:
                seen_ids.add(can_id)
        return issues

    def signals(self, comm_block: dict) -> tuple[CommSignal, ...]:
        return project_entries(comm_block.get("frames"))

    def bindings(self, comm_block: dict) -> dict[str, RegisterBinding]:
        return {}

    def can_bindings(self, comm_block: dict) -> dict[str, CanBinding]:
        result = {}
        for entry in (
            comm_block.get("frames", []) if isinstance(comm_block.get("frames"), list) else []
        ):
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and isinstance(entry.get("can_id"), int)
                and not isinstance(entry.get("can_id"), bool)
            ):
                result[entry["name"]] = CanBinding(entry["can_id"])
        return result

    def transport_config(self, comm_block: dict) -> CommTransportConfig:
        baud = comm_block.get("baud_rate")
        return CommTransportConfig(
            "can", baud if isinstance(baud, int) and not isinstance(baud, bool) else None
        )
