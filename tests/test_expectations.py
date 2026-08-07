from __future__ import annotations
import json
from pathlib import Path

import pytest

import tools.regenerate_expectations as regen
from tools.expectations import build_expectations
from tools.regenerate_expectations import DuplicateSystemName, regenerate_all

REPO_ROOT = Path(__file__).resolve().parents[1]
CONVEYOR_SPEC = REPO_ROOT / "specs" / "conveyor_handoff.yaml"
CONVEYOR_GOLDEN = REPO_ROOT / "specs" / "expectations" / "conveyor_handoff.expected.json"
PULSE_SPEC = REPO_ROOT / "specs" / "conveyor_pulse_release.yaml"


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
                '"EVENTUALLY(part_at_b, within: 400ms)"',
                '"EVENTUALLY(signal_that_never_fires, within: 400ms)"',
            )
        )
        artifact = build_expectations(broken)
        entry = artifact["assertions"][0]
        assert entry["text"] == "EVENTUALLY(signal_that_never_fires, within: 400ms)"
        assert entry["passed"] is False
        assert "signal_that_never_fires" in entry["witness"]


class TestDuplicateSystemName:
    @staticmethod
    def _stage(tmp_path, monkeypatch, specs: dict[str, Path]) -> Path:
        specs_dir = tmp_path / "specs"
        out_dir = specs_dir / "expectations"
        specs_dir.mkdir()
        out_dir.mkdir()
        for filename, source in specs.items():
            (specs_dir / filename).write_text(source.read_text())
        monkeypatch.setattr(regen, "_SPECS_DIR", specs_dir)
        monkeypatch.setattr(regen, "_EXPECTATIONS_DIR", out_dir)
        return out_dir

    def test_two_specs_sharing_a_name_raise(self, tmp_path, monkeypatch):
        out_dir = self._stage(
            tmp_path,
            monkeypatch,
            {"a.yaml": CONVEYOR_SPEC, "z_copy.yaml": CONVEYOR_SPEC},
        )
        with pytest.raises(DuplicateSystemName) as excinfo:
            regenerate_all()
        message = str(excinfo.value)
        assert "conveyor_handoff" in message
        assert "a.yaml" in message and "z_copy.yaml" in message
        assert list(out_dir.iterdir()) == [], (
            "a name collision must abort before any artifact is written; a partial "
            "write leaves one spec's verdicts filed under another spec's name"
        )

    def test_distinct_names_still_regenerate(self, tmp_path, monkeypatch):
        out_dir = self._stage(
            tmp_path,
            monkeypatch,
            {"a.yaml": CONVEYOR_SPEC, "b.yaml": PULSE_SPEC},
        )
        written = regenerate_all()
        assert {p.name for p in written} == {
            "conveyor_handoff.expected.json",
            "conveyor_pulse_release.expected.json",
        }
        assert {p.name for p in out_dir.iterdir()} == {p.name for p in written}

    def test_main_reports_collision_without_a_traceback(
        self, tmp_path, monkeypatch, capsys
    ):
        self._stage(
            tmp_path,
            monkeypatch,
            {"a.yaml": CONVEYOR_SPEC, "z_copy.yaml": CONVEYOR_SPEC},
        )
        assert regen.main([]) == 1
        assert "conveyor_handoff" in capsys.readouterr().out
