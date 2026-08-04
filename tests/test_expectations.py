from __future__ import annotations
import json
from pathlib import Path

from tools.expectations import build_expectations

REPO_ROOT = Path(__file__).resolve().parents[1]
CONVEYOR_SPEC = REPO_ROOT / "specs" / "conveyor_handoff.yaml"
CONVEYOR_GOLDEN = REPO_ROOT / "specs" / "expectations" / "conveyor_handoff.expected.json"


def _without_witnesses(artifact: dict) -> dict:
    stripped = {k: v for k, v in artifact.items() if k != "assertions"}
    stripped["assertions"] = [
        {k: v for k, v in entry.items() if k != "witness"}
        for entry in artifact["assertions"]
    ]
    return stripped


class TestExpectations:
    def test_conveyor_expectations_matches_golden(self):
        fresh = build_expectations(CONVEYOR_SPEC)
        committed = json.loads(CONVEYOR_GOLDEN.read_text())
        assert _without_witnesses(fresh) == _without_witnesses(committed), (
            "committed expectations artifact disagrees with a fresh sim run; "
            "regenerate with: python -m tools.regenerate_expectations"
        )

    def test_witness_is_non_empty_but_not_compared(self):
        committed = json.loads(CONVEYOR_GOLDEN.read_text())
        for entry in committed["assertions"]:
            assert isinstance(entry["witness"], str)
            assert entry["witness"]

    def test_failed_assertion_records_passed_false(self, tmp_path):
        broken = tmp_path / "broken.yaml"
        source = CONVEYOR_SPEC.read_text()
        broken.write_text(
            source.replace(
                '"EVENTUALLY(part_at_b, within: 500ms)"',
                '"EVENTUALLY(signal_that_never_fires, within: 500ms)"',
            )
        )
        artifact = build_expectations(broken)
        entry = artifact["assertions"][0]
        assert entry["text"] == "EVENTUALLY(signal_that_never_fires, within: 500ms)"
        assert entry["passed"] is False
        assert "signal_that_never_fires" in entry["witness"]
