from __future__ import annotations
import argparse
import asyncio
from pathlib import Path

from relay.generator.st import compile_st_blocks
from relay.runtime.harness import simulate
from relay.spec.schema import load_spec
from relay.trace_io import load_jsonl
from relay.verify.assertions import evaluate_assertion

from tools.expectations import DEFAULT_MAX_SCANS


def _sim_trace(spec, max_scans: int):
    st_blocks = compile_st_blocks(spec)
    return asyncio.run(simulate(spec, st_blocks, max_scans=max_scans))


def _load_trace(trace_path: Path):
    with trace_path.open() as stream:
        return load_jsonl(stream)


def collect_timings(
    spec_path: Path,
    trace_paths: list[Path],
    *,
    max_scans: int = DEFAULT_MAX_SCANS,
) -> dict[str, dict[str, list[float | None]]]:
    spec = load_spec(spec_path)
    sources = (
        [("sim", _sim_trace(spec, max_scans))]
        if not trace_paths
        else [(str(p), _load_trace(p)) for p in trace_paths]
    )
    timings: dict[str, dict[str, list[float | None]]] = {
        text: {"witness_ms": [], "observed_gap_ms": []} for text in spec.assertions
    }
    for _, trace in sources:
        for text in spec.assertions:
            result = evaluate_assertion(text, trace)
            timings[text]["witness_ms"].append(result.witness_ms)
            timings[text]["observed_gap_ms"].append(result.observed_gap_ms)
    return timings


def _summarize(values: list[float | None]) -> str | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    missing = len(values) - len(present)
    summary = f"n={len(present)}  min={min(present):.1f}  max={max(present):.1f}"
    if missing:
        summary += f"  missing={missing}"
    return summary + "  values: " + ", ".join(f"{v:.1f}" for v in present)


def format_report(spec_path: Path, timings: dict[str, dict[str, list[float | None]]]) -> str:
    lines = [str(spec_path)]
    for text, fields in timings.items():
        lines.append(f"  {text}")
        reported = False
        for field in ("witness_ms", "observed_gap_ms"):
            summary = _summarize(fields[field])
            if summary is not None:
                lines.append(f"    {field}  {summary}")
                reported = True
        if not reported:
            lines.append("    no observed timing")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate observed assertion timings across trace files; "
            "with no traces, run the sim once"
        )
    )
    parser.add_argument("spec", type=Path)
    parser.add_argument("traces", type=Path, nargs="*")
    parser.add_argument("--max-scans", type=int, default=DEFAULT_MAX_SCANS)
    args = parser.parse_args(argv)
    timings = collect_timings(args.spec, args.traces, max_scans=args.max_scans)
    print(format_report(args.spec, timings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
