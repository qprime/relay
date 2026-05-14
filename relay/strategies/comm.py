from __future__ import annotations
from typing import TYPE_CHECKING, Any, Protocol

from relay.io_image import IOImage

if TYPE_CHECKING:
    from relay.spec.schema import TaskSpec


class CommStrategy(Protocol):
    name: str

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]: ...

    def route(
        self,
        producer_id: str,
        outputs: IOImage,
        prior_outputs: IOImage,
    ) -> list[tuple[str, str, Any]]: ...


_TAG_PROMPT_FRAGMENT = """\
Comm:
  strategy: tag
  tags:
    - { name: <signal>, produced_by: <plc_id>, consumed_by: [<plc_id>, ...] }
Declare every PLC-to-PLC signal as a tag. Plant-to-PLC signals are NOT tags.
Emission rule: assign _send_<consumer_plc>_<tag_name> := <value> for each tag this PLC produces; the runtime routes these via the comm bus.
"""


class TagStrategy:
    name = "tag"
    STRATEGY_PROMPT_FRAGMENT = _TAG_PROMPT_FRAGMENT

    def __init__(self, comm_block: dict | None = None) -> None:
        self._by_producer: dict[str, list[tuple[str, list[str]]]] = {}
        block = comm_block or {}
        for tag in block.get("tags", []) or []:
            producer = tag.get("produced_by")
            name = tag.get("name")
            consumers = list(tag.get("consumed_by") or [])
            if not producer or not name:
                continue
            self._by_producer.setdefault(producer, []).append((name, consumers))

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]:
        issues: list[str] = []
        tags = comm_block.get("tags", [])
        if not isinstance(tags, list):
            return ["Comm.tags must be a list"]
        seen_names: set[str] = set()
        plc_ids = set(spec.plc_ids)
        for i, tag in enumerate(tags):
            if not isinstance(tag, dict):
                issues.append(f"Comm.tags[{i}] must be a mapping")
                continue
            name = tag.get("name")
            if not name or not isinstance(name, str):
                issues.append(f"Comm.tags[{i}].name is required and must be a string")
            elif name in seen_names:
                issues.append(f"Comm.tags[{i}].name {name!r} is duplicated")
            else:
                seen_names.add(name)
            producer = tag.get("produced_by")
            if not producer:
                issues.append(f"Comm.tags[{i}].produced_by is required")
            elif producer not in plc_ids:
                issues.append(
                    f"Comm.tags[{i}].produced_by {producer!r} is not a declared plc_id"
                )
            consumers = tag.get("consumed_by")
            if not isinstance(consumers, list) or not consumers:
                issues.append(f"Comm.tags[{i}].consumed_by must be a non-empty list")
            else:
                for c in consumers:
                    if c not in plc_ids:
                        issues.append(
                            f"Comm.tags[{i}].consumed_by entry {c!r} is not a declared plc_id"
                        )
        return issues

    def route(
        self,
        producer_id: str,
        outputs: IOImage,
        prior_outputs: IOImage,
    ) -> list[tuple[str, str, Any]]:
        emitted: list[tuple[str, str, Any]] = []
        for tag_name, consumers in self._tags_for(producer_id):
            current = outputs.get(tag_name)
            prior = prior_outputs.get(tag_name)
            if current == prior:
                continue
            for consumer in consumers:
                emitted.append((consumer, tag_name, current))
        return emitted

    def _tags_for(self, producer_id: str) -> list[tuple[str, list[str]]]:
        return self._by_producer.get(producer_id, [])


_ADDRESS_PROMPT_FRAGMENT = """\
Comm:
  strategy: address
(address-based comm is not yet implemented in this build)
"""


class AddressStrategy:
    name = "address"
    STRATEGY_PROMPT_FRAGMENT = _ADDRESS_PROMPT_FRAGMENT

    def __init__(self, comm_block: dict | None = None) -> None:
        self._comm_block = comm_block or {}

    def validate_config(self, comm_block: dict, spec: "TaskSpec") -> list[str]:
        raise NotImplementedError("address-based comm not yet implemented")

    def route(
        self,
        producer_id: str,
        outputs: IOImage,
        prior_outputs: IOImage,
    ) -> list[tuple[str, str, Any]]:
        raise NotImplementedError("address-based comm not yet implemented")


_REGISTRY: dict[str, type] = {
    "tag": TagStrategy,
    "address": AddressStrategy,
}


def get_comm_strategy(name: str) -> CommStrategy:
    return build_comm_strategy(name, {})


def build_comm_strategy(name: str, comm_block: dict) -> CommStrategy:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"unknown comm strategy {name!r}; known: {known}")
    return _REGISTRY[name](comm_block)
