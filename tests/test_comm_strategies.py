from __future__ import annotations

import pytest

from relay.spec.schema import TaskSpec
from relay.strategies.comm import (
    AddressStrategy,
    TagStrategy,
    build_comm_strategy,
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
        "Assertions": [],
    }
    return TaskSpec(raw=raw)


class TestTagStrategy:
    def test_validates_unknown_consumer(self):
        spec = _spec(("plc_a", "plc_b"))
        block = {
            "strategy": "tag",
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_z"]}],
        }
        strat = TagStrategy(block)
        issues = strat.validate_config(block, spec)
        assert any("plc_z" in i for i in issues), issues

    def test_validates_unknown_producer(self):
        spec = _spec(("plc_a", "plc_b"))
        block = {
            "strategy": "tag",
            "tags": [{"name": "x", "produced_by": "plc_z", "consumed_by": ["plc_b"]}],
        }
        strat = TagStrategy(block)
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
        strat = TagStrategy(block)
        issues = strat.validate_config(block, spec)
        assert any("duplicated" in i for i in issues), issues


class TestStrategyRegistry:
    def test_address_registered_but_validate_raises(self):
        strat = get_comm_strategy("address")
        assert isinstance(strat, AddressStrategy)
        with pytest.raises(NotImplementedError):
            strat.validate_config({}, _spec())

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="unknown comm strategy"):
            get_comm_strategy("nonsense")

    def test_build_comm_strategy_passes_block_to_tag(self):
        block = {
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_z"]}]
        }
        strat = build_comm_strategy("tag", block)
        issues = strat.validate_config(block, _spec())
        assert any("plc_z" in i for i in issues), issues
