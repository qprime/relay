from __future__ import annotations
import copy
from pathlib import Path

import yaml

import relay.generator.spec
import relay.strategies.plant
from relay.strategies.plant import get_plant, register_plant
from tools.validate_spec import main, validate_spec_file


_REPO_ROOT = Path(__file__).parent.parent
_SPEC_PATH = _REPO_ROOT / "specs" / "conveyor_handoff.yaml"
_RELAY_SOURCES = sorted((_REPO_ROOT / "relay").rglob("*.py"))


def _write(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def _valid_raw() -> dict:
    return copy.deepcopy(yaml.safe_load(_SPEC_PATH.read_text()))


def _first_trigger(raw: dict) -> dict:
    plc_id = next(iter(raw["Behavior"]))
    return raw["Behavior"][plc_id]["triggers"][0]


def _with_bad_edge(raw: dict) -> dict:
    _first_trigger(raw)["when"]["edge"] = "sideways"
    return raw


class TestValidateSpecCLI:
    def test_valid_spec_returns_no_issues(self):
        assert validate_spec_file(_SPEC_PATH) == []

    def test_missing_behavior_triggers_reports_issue(self, tmp_path):
        raw = _valid_raw()
        plc_id = next(iter(raw["Behavior"]))
        raw["Behavior"][plc_id].pop("triggers")
        issues = validate_spec_file(_write(tmp_path, raw))
        assert any("triggers must be a list" in i for i in issues), issues

    def test_unknown_plant_type_is_an_issue_not_a_traceback(self, tmp_path):
        raw = _valid_raw()
        raw["Plant"]["type"] = "nonexistent"
        issues = validate_spec_file(_write(tmp_path, raw))
        assert any("nonexistent" in i for i in issues), issues

    def test_unknown_comm_strategy_is_an_issue_not_a_traceback(self, tmp_path):
        raw = _valid_raw()
        raw["Comm"]["strategy"] = "nonexistent"
        issues = validate_spec_file(_write(tmp_path, raw))
        assert len([i for i in issues if "nonexistent" in i]) == 1, issues

    def test_address_strategy_is_a_collected_issue_not_terminal(self, tmp_path):
        raw = _with_bad_edge(_valid_raw())
        raw["Comm"]["strategy"] = "address"
        issues = validate_spec_file(_write(tmp_path, raw))
        assert any("not yet implemented" in i for i in issues), issues
        assert any("sideways" in i for i in issues), issues

    def test_malformed_yaml_reports_issue(self, tmp_path):
        path = tmp_path / "spec.yaml"
        path.write_text("System: [unclosed\n")
        assert validate_spec_file(path)
        assert main([str(path)]) == 1

    def test_non_mapping_yaml_reports_issue(self, tmp_path):
        path = tmp_path / "spec.yaml"
        path.write_text("- a\n- b\n")
        assert validate_spec_file(path) == ["top-level YAML must be a mapping"]

    def test_missing_file_reports_issue(self, tmp_path):
        path = tmp_path / "absent.yaml"
        assert validate_spec_file(path)
        assert main([str(path)]) == 1

    def test_main_exit_code_zero_on_valid(self):
        assert main([str(_SPEC_PATH)]) == 0

    def test_main_exit_code_one_on_invalid(self, tmp_path):
        path = _write(tmp_path, _with_bad_edge(_valid_raw()))
        assert main([str(path)]) == 1

    def test_plant_registry_is_populated(self):
        raw = _valid_raw()
        assert get_plant(raw["Plant"]["type"]) is not None
        assert validate_spec_file(_SPEC_PATH) == []

    def test_multiple_independent_issues_all_reported(self, tmp_path):
        raw = _with_bad_edge(_valid_raw())
        raw["Assertions"].append("EVENTUALLY(no_such_signal, within: 500ms)")
        issues = validate_spec_file(_write(tmp_path, raw))
        assert any("sideways" in i for i in issues), issues
        assert any("no_such_signal" in i for i in issues), issues

    def test_bad_plant_config_accumulates_with_unrelated_issues(self, tmp_path):
        raw = _with_bad_edge(_valid_raw())
        raw["Plant"].pop("config")
        issues = validate_spec_file(_write(tmp_path, raw))
        assert any("Plant factory" in i for i in issues), issues
        assert any("sideways" in i for i in issues), issues

    def test_unresolvable_selector_is_terminal_and_says_so(self, tmp_path, capsys):
        raw = _with_bad_edge(_valid_raw())
        raw["Plant"]["type"] = "nonexistent"
        path = _write(tmp_path, raw)
        issues = validate_spec_file(path)
        assert len(issues) == 1 and "nonexistent" in issues[0], issues
        assert main([str(path)]) == 1
        assert "validation stopped early" in capsys.readouterr().out

    def test_missing_plant_block_is_not_terminal(self, tmp_path):
        raw = _with_bad_edge(_valid_raw())
        raw.pop("Plant")
        issues = validate_spec_file(_write(tmp_path, raw))
        assert any("Plant block is required" in i for i in issues), issues
        assert any("sideways" in i for i in issues), issues


class TestNoModelClientDependency:
    def test_relay_package_does_not_import_anthropic(self):
        offenders = [
            path.relative_to(_REPO_ROOT).as_posix()
            for path in _RELAY_SOURCES
            if "import anthropic" in path.read_text()
            or "from anthropic" in path.read_text()
        ]
        assert offenders == []

    def test_generate_spec_symbols_are_gone(self):
        for name in ("generate_spec", "generate_spec_yaml", "_compose_system_prompt"):
            assert getattr(relay.generator.spec, name, None) is None


class _StubPlant:
    def step(self, dt_ms, actuator_state):
        return self

    def route_to_plcs(self, plant_output, prior_output):
        return []

    def read_actuators(self, latest_outputs):
        return {}

    def validate_config(self, plant_block, spec):
        return []


class TestRegisterPlantSignature:
    def test_register_plant_accepts_two_positional_args(self):
        def factory(plant_block: dict) -> _StubPlant:
            return _StubPlant()

        register_plant("_test_plant", factory)
        try:
            assert get_plant("_test_plant") is factory
        finally:
            relay.strategies.plant._REGISTRY.pop("_test_plant", None)

    def test_prompt_fragment_accessor_is_gone(self):
        assert not hasattr(relay.strategies.plant, "get_plant_prompt_fragment")
