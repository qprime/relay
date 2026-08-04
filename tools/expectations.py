from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from relay.clock import DEFAULT_SCAN_PERIOD_MS
from relay.generator.st import compile_st_blocks
from relay.runtime.harness import simulate
from relay.spec.schema import load_spec
from relay.verify.assertions import evaluate_assertion

DEFAULT_MAX_SCANS = 100

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _repo_relative(spec_path: Path) -> str:
    resolved = spec_path.resolve()
    try:
        return resolved.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return str(spec_path)


def build_expectations(
    spec_path: Path,
    *,
    scan_period_ms: float = DEFAULT_SCAN_PERIOD_MS,
    max_scans: int = DEFAULT_MAX_SCANS,
) -> dict[str, Any]:
    spec = load_spec(spec_path)
    st_blocks = compile_st_blocks(spec)
    trace = asyncio.run(
        simulate(spec, st_blocks, max_scans=max_scans, scan_period_ms=scan_period_ms)
    )
    entries = []
    for text in spec.assertions:
        result = evaluate_assertion(text, trace)
        entries.append(
            {
                "text": text,
                "passed": result.passed,
                "witness": result.reason,
                "observed_gap_ms": result.observed_gap_ms,
            }
        )
    return {
        "system_name": spec.system_name,
        "generated_from": _repo_relative(spec_path),
        "scan_period_ms": scan_period_ms,
        "max_scans": max_scans,
        "assertions": entries,
    }


def write_expectations(artifact: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the sim, evaluate assertions, and write the expectations artifact"
    )
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--scan-period-ms", type=float, default=DEFAULT_SCAN_PERIOD_MS)
    parser.add_argument("--max-scans", type=int, default=DEFAULT_MAX_SCANS)
    args = parser.parse_args(argv)
    artifact = build_expectations(
        args.spec, scan_period_ms=args.scan_period_ms, max_scans=args.max_scans
    )
    if args.out is None:
        print(json.dumps(artifact, indent=2, sort_keys=True))
    else:
        write_expectations(artifact, args.out)
        print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
