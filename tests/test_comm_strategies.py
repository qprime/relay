from __future__ import annotations

import copy

import pytest

from relay.spec.schema import TaskSpec
from relay.strategies.comm import (
    AddressStrategy,
    CommSignal,
    TagStrategy,
    get_comm_strategy,
)


def _spec(plc_ids=("plc_a", "plc_b"), behavior=None) -> TaskSpec:
    raw = {
        "System": {
            "name": "test",
            "plcs": [{"id": pid, "role": "x"} for pid in plc_ids],
        },
        "Comm": {"strategy": "tag"},
        "Plant": {"type": "conveyor", "config": {}},
        "Behavior": behavior or {},
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
        block = _address_block(
            _register(), _register(table="discrete_input", address=1)
        )
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

    def test_same_address_in_different_tables_is_legal(self):
        block = _address_block(
            _register(), _register(name="other_signal", table="discrete_input")
        )
        issues = AddressStrategy().validate_config(block, _spec())
        assert issues == []

    def test_rejects_word_table_for_emitted_signal(self):
        behavior = {
            "plc_a": {
                "triggers": [
                    {
                        "id": "t",
                        "when": {"signal": "s", "edge": "rising"},
                        "emit": {"tag": "handoff_signal", "mode": "latched"},
                    }
                ]
            }
        }
        block = _address_block(_register(table="holding_register"))
        issues = AddressStrategy().validate_config(
            block, _spec(behavior=behavior)
        )
        assert any("word table" in i for i in issues), issues

    def test_address_never_appears_in_projection(self):
        block = _address_block()
        (signal,) = AddressStrategy().signals(block)
        assert not hasattr(signal, "address")
        assert not hasattr(signal, "table")


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
