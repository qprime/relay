from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any

from relay.clock import DEFAULT_SCAN_PERIOD_MS
from relay.generator.st import compile_st_blocks
from relay.spec.schema import TaskSpec, load_spec

DEFAULT_MAX_SCANS = 100


def resolved_spec_dict(
    spec: TaskSpec,
    *,
    scan_period_ms: float = DEFAULT_SCAN_PERIOD_MS,
    max_scans: int = DEFAULT_MAX_SCANS,
) -> dict[str, Any]:
    routes = [
        {
            "sensor": route["sensor"],
            "to_plc": route["to_plc"],
            "as_key": route["as_key"],
            "trigger": route.get("trigger", "level"),
        }
        for route in spec.plant_block.get("routes") or []
    ]
    actuators = [
        {
            "from_plc": actuator["from_plc"],
            "key": actuator["key"],
            "as": actuator["as"],
        }
        for actuator in spec.plant_block.get("actuators") or []
    ]
    return {
        "system_name": spec.system_name,
        "plc_ids": list(spec.plc_ids),
        "scan_period_ms": scan_period_ms,
        "max_scans": max_scans,
        "comm": {
            "strategy": spec.comm_strategy,
            "tags": [
                {
                    "name": tag["name"],
                    "produced_by": tag["produced_by"],
                    "consumed_by": list(tag.get("consumed_by") or []),
                }
                for tag in spec.comm_block.get("tags") or []
            ],
        },
        "plant": {
            "type": spec.plant_type,
            "config": dict(spec.plant_config),
            "routes": routes,
            "actuators": actuators,
        },
        "assertions": list(spec.assertions),
    }


def emit_host_inputs(
    spec_path: Path,
    out_dir: Path,
    *,
    scan_period_ms: float = DEFAULT_SCAN_PERIOD_MS,
    max_scans: int = DEFAULT_MAX_SCANS,
) -> tuple[Path, Path]:
    spec = load_spec(spec_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_path = out_dir / "resolved_spec.json"
    blocks_path = out_dir / "st_blocks.json"
    resolved_path.write_text(
        json.dumps(
            resolved_spec_dict(spec, scan_period_ms=scan_period_ms, max_scans=max_scans),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    blocks_path.write_text(
        json.dumps(compile_st_blocks(spec), indent=2, sort_keys=True) + "\n"
    )
    return resolved_path, blocks_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit resolved-spec and ST-blocks JSON for the C++ host"
    )
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--scan-period-ms", type=float, default=DEFAULT_SCAN_PERIOD_MS)
    parser.add_argument("--max-scans", type=int, default=DEFAULT_MAX_SCANS)
    args = parser.parse_args(argv)
    resolved_path, blocks_path = emit_host_inputs(
        args.spec,
        args.out_dir,
        scan_period_ms=args.scan_period_ms,
        max_scans=args.max_scans,
    )
    print(resolved_path)
    print(blocks_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
