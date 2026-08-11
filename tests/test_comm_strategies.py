from __future__ import annotations

import copy

import pytest

from relay.spec.schema import TaskSpec
from relay.strategies.comm import (
    AddressStrategy,
    CommSignal,
    RegisterBinding,
    TagStrategy,
    get_comm_strategy,
)


def _spec(plc_ids=("plc_a", "plc_b")) -> TaskSpec:
    raw = {
        "System": {
            "name": "test",
            "plcs": [{"id": pid, "role": "x"} for pid in plc_ids],
        },
        "Comm": {"strategy": "tag"},
        "Plant": {"type": "conveyor", "config": {}},
        "Behavior": {},
        "Assertions": [],
    }
    return TaskSpec(raw=raw)


def _register(**overrides) -> dict:
    register = {
        "name": "handoff_signal",
        "produced_by": "plc_a",
        "consumed_by": ["plc_b"],
        "table": "coil",
        "address": 0,
    }
    register.update(overrides)
    return register


def _address_block(*registers) -> dict:
    return {"strategy": "address", "registers": list(registers) or [_register()]}


class TestTagStrategy:
    def test_validates_unknown_consumer(self):
        spec = _spec(("plc_a", "plc_b"))
        block = {
            "strategy": "tag",
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_z"]}],
        }
        strat = TagStrategy()
        issues = strat.validate_config(block, spec)
        assert any("plc_z" in i for i in issues), issues

    def test_validates_unknown_producer(self):
        spec = _spec(("plc_a", "plc_b"))
        block = {
            "strategy": "tag",
            "tags": [{"name": "x", "produced_by": "plc_z", "consumed_by": ["plc_b"]}],
        }
        strat = TagStrategy()
        issues = strat.validate_config(block, spec)
        assert any("plc_z" in i for i in issues), issues

    def test_validates_duplicate_tag_names(self):
        spec = _spec()
        block = {
            "strategy": "tag",
            "tags": [
                {"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b"]},
                {"name": "x", "produced_by": "plc_b", "consumed_by": ["plc_a"]},
            ],
        }
        strat = TagStrategy()
        issues = strat.validate_config(block, spec)
        assert any("duplicated" in i for i in issues), issues

    def test_signals_projects_tags(self):
        block = {
            "strategy": "tag",
            "tags": [
                {"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b"]},
                {"name": "y", "produced_by": "plc_b", "consumed_by": ["plc_a"]},
            ],
        }
        assert TagStrategy().signals(block) == (
            CommSignal(name="x", produced_by="plc_a", consumed_by=("plc_b",)),
            CommSignal(name="y", produced_by="plc_b", consumed_by=("plc_a",)),
        )

    def test_signals_skips_malformed_entries(self):
        block = {
            "strategy": "tag",
            "tags": [
                "not-a-mapping",
                {"produced_by": "plc_a", "consumed_by": ["plc_b"]},
                {"name": "x", "consumed_by": ["plc_b"]},
                {"name": "ok", "produced_by": "plc_a", "consumed_by": ["plc_b"]},
            ],
        }
        assert TagStrategy().signals(block) == (
            CommSignal(name="ok", produced_by="plc_a", consumed_by=("plc_b",)),
        )

    def test_signals_reads_the_passed_block_not_self(self):
        block = {
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b"]}]
        }
        strat = TagStrategy()
        assert strat.signals(block) == (
            CommSignal(name="x", produced_by="plc_a", consumed_by=("plc_b",)),
        )


class TestAddressStrategy:
    def test_valid_register_map_has_no_issues(self):
        block = _address_block()
        assert AddressStrategy().validate_config(block, _spec()) == []

    def test_signals_projects_registers(self):
        block = _address_block()
        assert AddressStrategy().signals(block) == (
            CommSignal(
                name="handoff_signal", produced_by="plc_a", consumed_by=("plc_b",)
            ),
        )

    def test_rejects_missing_registers_block(self):
        block = {"strategy": "address"}
        issues = AddressStrategy().validate_config(block, _spec())
        assert issues == ["Comm.registers must be a non-empty list"]

    def test_rejects_unknown_producer(self):
        block = _address_block(_register(produced_by="plc_z"))
        issues = AddressStrategy().validate_config(block, _spec())
        assert any("plc_z" in i and "produced_by" in i for i in issues), issues

    def test_rejects_unknown_consumer(self):
        block = _address_block(_register(consumed_by=["plc_z"]))
        issues = AddressStrategy().validate_config(block, _spec())
        assert any("plc_z" in i and "consumed_by" in i for i in issues), issues

    def test_rejects_empty_consumed_by(self):
        block = _address_block(_register(consumed_by=[]))
        issues = AddressStrategy().validate_config(block, _spec())
        assert any("consumed_by must be a non-empty list" in i for i in issues), issues

    def test_rejects_duplicate_signal_names(self):
        block = _address_block(_register(), _register(address=1))
        issues = AddressStrategy().validate_config(block, _spec())
        assert any("duplicated" in i for i in issues), issues

    def test_rejects_unknown_table_kind(self):
        block = _address_block(_register(table="flux_capacitor"))
        issues = AddressStrategy().validate_config(block, _spec())
        assert any("flux_capacitor" in i and "table" in i for i in issues), issues

    def test_rejects_address_out_of_range(self):
        for bad in (-1, 65536):
            block = _address_block(_register(address=bad))
            issues = AddressStrategy().validate_config(block, _spec())
            assert any("address" in i and str(bad) in i for i in issues), (bad, issues)

    def test_rejects_bool_address(self):
        block = _address_block(_register(address=True))
        issues = AddressStrategy().validate_config(block, _spec())
        assert any("address must be an integer" in i for i in issues), issues

    def test_rejects_duplicate_table_address_pair(self):
        block = _address_block(
            _register(), _register(name="other_signal")
        )
        issues = AddressStrategy().validate_config(block, _spec())
        assert any("another entry already binds" in i for i in issues), issues

    def test_address_never_appears_in_projection(self):
        block = _address_block()
        (signal,) = AddressStrategy().signals(block)
        assert not hasattr(signal, "address")
        assert not hasattr(signal, "table")


class TestAddressTableRules:
    def _table_issues(self, table: str) -> list[str]:
        block = _address_block(_register(table=table))
        return [
            issue
            for issue in AddressStrategy().validate_config(block, _spec())
            if "table" in issue
        ]

    def test_discrete_input_rejected_as_read_only(self):
        issues = self._table_issues("discrete_input")
        assert any("read-only" in i for i in issues), issues

    def test_input_register_rejected_as_read_only(self):
        issues = self._table_issues("input_register")
        assert any("read-only" in i for i in issues), issues

    def test_holding_register_rejected_because_no_trigger_emits_a_word(self):
        issues = self._table_issues("holding_register")
        assert any("word" in i for i in issues), issues
        assert not any("read-only" in i for i in issues), issues

    def test_the_two_rejections_give_different_messages(self):
        read_only = self._table_issues("discrete_input")
        word = self._table_issues("holding_register")
        assert read_only and word
        assert read_only != word, (
            "a writable-but-wrong-width table and a read-only one fail for "
            "different reasons and each deserves its own message"
        )

    def test_coil_is_accepted(self):
        assert self._table_issues("coil") == []

    def test_the_grammar_still_names_every_table_in_the_diagnostic(self):
        issues = self._table_issues("flux_capacitor")
        assert any("holding_register" in i for i in issues), issues


class TestRegisterBindings:
    def test_address_strategy_projects_the_register_map(self):
        assert AddressStrategy().bindings(_address_block()) == {
            "handoff_signal": RegisterBinding(table="coil", address=0)
        }

    def test_tag_strategy_has_no_bindings(self):
        block = {
            "strategy": "tag",
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b"]}],
        }
        assert TagStrategy().bindings(block) == {}

    def test_bindings_skip_malformed_entries_without_raising(self):
        block = _address_block(
            "not-a-mapping",
            _register(name=None),
            _register(name="no_table", table=None),
            _register(name="bool_address", address=True),
            _register(name="ok", address=3),
        )
        assert AddressStrategy().bindings(block) == {
            "ok": RegisterBinding(table="coil", address=3)
        }

    def test_bindings_read_the_passed_block_not_self(self):
        block = {"registers": [_register()]}
        assert AddressStrategy().bindings(block) == {
            "handoff_signal": RegisterBinding(table="coil", address=0)
        }


class TestStrategyRegistry:
    def test_address_validate_config_no_longer_raises(self):
        strat = get_comm_strategy("address")
        assert isinstance(strat, AddressStrategy)
        issues = strat.validate_config({}, _spec())
        assert issues == ["Comm.registers must be a non-empty list"]

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="unknown comm strategy"):
            get_comm_strategy("nonsense")

    def test_resolved_strategy_validates_the_passed_block(self):
        block = {
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_z"]}]
        }
        strat = get_comm_strategy("tag")
        issues = strat.validate_config(block, _spec())
        assert any("plc_z" in i for i in issues), issues

    def test_validate_config_does_not_mutate_the_block(self):
        block = _address_block()
        snapshot = copy.deepcopy(block)
        AddressStrategy().validate_config(block, _spec())
        assert block == snapshot
