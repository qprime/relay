from __future__ import annotations

from relay.strategies.comm import CommSignal, CommTransportConfig, RegisterBinding, register
from relay.strategies.comm.tag import TagStrategy

TABLES = ("coil", "discrete_input", "input_register", "holding_register")


@register
class AddressStrategy(TagStrategy):
    name = "address"

    def validate_config(self, comm_block: dict, spec) -> list[str]:
        registers = comm_block.get("registers")
        if not isinstance(registers, list) or not registers:
            return ["Comm.registers must be a non-empty list"]
        issues = super().validate_config({"tags": registers}, spec)
        issues = [issue.replace("Comm.tags", "Comm.registers") for issue in issues]
        seen: set[tuple[str, int]] = set()
        for i, entry in enumerate(registers):
            if not isinstance(entry, dict):
                continue
            table, address = entry.get("table"), entry.get("address")
            if table not in TABLES:
                issues.append(
                    f"Comm.registers[{i}].table must be one of {list(TABLES)}, got {table!r}"
                )
            elif table in ("discrete_input", "input_register"):
                issues.append(f"Comm.registers[{i}].table {table!r} is read-only; use 'coil'")
            elif table != "coil":
                issues.append(f"Comm.registers[{i}].table {table!r} carries a word; use 'coil'")
            if (
                isinstance(address, bool)
                or not isinstance(address, int)
                or not 0 <= address <= 65535
            ):
                issues.append(
                    f"Comm.registers[{i}].address must be an integer in [0, 65535], got {address!r}"
                )
            elif isinstance(table, str):
                if (table, address) in seen:
                    issues.append(
                        f"Comm.registers[{i}] binds {table}:{address}, which another entry already binds"
                    )
                seen.add((table, address))
        return issues

    def signals(self, comm_block: dict) -> tuple[CommSignal, ...]:
        from relay.strategies.comm import project_entries

        return project_entries(comm_block.get("registers"))

    def bindings(self, comm_block: dict) -> dict[str, RegisterBinding]:
        result = {}
        for entry in (
            comm_block.get("registers", []) if isinstance(comm_block.get("registers"), list) else []
        ):
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and isinstance(entry.get("table"), str)
                and isinstance(entry.get("address"), int)
                and not isinstance(entry.get("address"), bool)
            ):
                result[entry["name"]] = RegisterBinding(entry["table"], entry["address"])
        return result

    def transport_config(self, comm_block: dict) -> CommTransportConfig:
        return CommTransportConfig("in_process")
