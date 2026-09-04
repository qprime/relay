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


def _outcome(results):
    return [
        (r.assertion, r.passed, r.witness_ms, r.observed_gap_ms, r.attribution) for r in results
    ]


class TestTraceOrderIndependence:
    def test_verdicts_stable_under_record_permutation(self):
        spec = load_spec(CONVEYOR_SPEC)
        with GOLDEN_TRACE.open() as stream:
            trace = load_jsonl(stream)
        baseline = _outcome(evaluate_all(spec.assertions, trace))
        assert baseline, "conveyor spec must declare assertions"
        for seed in range(25):
            shuffled = TraceLog(_interleave_preserving_per_plc_order(trace.records, seed))
            outcome = _outcome(evaluate_all(spec.assertions, shuffled))
            assert outcome == baseline, (
                f"seed {seed}: verdicts changed under record interleaving; "
                "free-running append order is completion order, so any order "
                "sensitivity here is a verifier defect. Witness times and "
                "attribution are compared too: a positional selection rule can "
                "hold pass/fail while moving the scan it names"
            )

    def test_verdicts_stable_under_full_record_shuffle(self):
        """Per-PLC order is a property of how the trace was appended, not of the
        trace's content. Selection minimises `(elapsed_ms, plc_id)`, which is
        content, so even a total shuffle must not move a verdict."""
        spec = load_spec(CONVEYOR_SPEC)
        with GOLDEN_TRACE.open() as stream:
            trace = load_jsonl(stream)
        baseline = _outcome(evaluate_all(spec.assertions, trace))
        for seed in range(25):
            records = list(trace.records)
            random.Random(seed).shuffle(records)
            outcome = _outcome(evaluate_all(spec.assertions, TraceLog(records)))
            assert outcome == baseline, f"seed {seed}: verdicts changed under shuffle"
