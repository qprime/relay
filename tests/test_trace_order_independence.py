from __future__ import annotations
import random
from pathlib import Path

from relay.spec.schema import load_spec
from relay.trace import TraceLog
from relay.trace_io import load_jsonl
from relay.verify.assertions import evaluate_all

REPO_ROOT = Path(__file__).resolve().parents[1]
CONVEYOR_SPEC = REPO_ROOT / "specs" / "conveyor_handoff.yaml"
GOLDEN_TRACE = Path(__file__).parent / "golden" / "conveyor_trace.jsonl"


def _interleave_preserving_per_plc_order(records, seed: int):
    queues: dict[str, list] = {}
    for record in records:
        queues.setdefault(record.plc_id, []).append(record)
    rng = random.Random(seed)
    merged = []
    while queues:
        plc_id = rng.choice(sorted(queues))
        merged.append(queues[plc_id].pop(0))
        if not queues[plc_id]:
            del queues[plc_id]
    return merged


class TestTraceOrderIndependence:
    def test_verdicts_stable_under_record_permutation(self):
        spec = load_spec(CONVEYOR_SPEC)
        with GOLDEN_TRACE.open() as stream:
            trace = load_jsonl(stream)
        baseline = [
            (r.assertion, r.passed) for r in evaluate_all(spec.assertions, trace)
        ]
        assert baseline, "conveyor spec must declare assertions"
        for seed in range(25):
            shuffled = TraceLog(
                _interleave_preserving_per_plc_order(trace.records, seed)
            )
            outcome = [
                (r.assertion, r.passed) for r in evaluate_all(spec.assertions, shuffled)
            ]
            assert outcome == baseline, (
                f"seed {seed}: verdicts changed under record interleaving; "
                "free-running append order is completion order, so any order "
                "sensitivity here is a verifier defect"
            )
