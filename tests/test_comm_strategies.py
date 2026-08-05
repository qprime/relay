from __future__ import annotations

import pytest

from relay.io_image import IOImage
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

    def test_routes_on_change(self):
        block = {
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b"]}]
        }
        strat = TagStrategy(block)
        outputs = IOImage(values={"x": True})
        prior = IOImage(values={"x": False})
        emitted = strat.route("plc_a", outputs, prior)
        assert emitted == [("plc_b", "x", True)]

        # next scan: no change → nothing emitted
        emitted = strat.route("plc_a", outputs, outputs)
        assert emitted == []

    def test_routes_deassertion(self):
        block = {
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b"]}]
        }
        strat = TagStrategy(block)
        outputs = IOImage(values={"x": False})
        prior = IOImage(values={"x": True})
        emitted = strat.route("plc_a", outputs, prior)
        assert emitted == [("plc_b", "x", False)]

    def test_routes_to_all_consumers(self):
        block = {
            "tags": [
                {"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b", "plc_c"]}
            ]
        }
        strat = TagStrategy(block)
        outputs = IOImage(values={"x": True})
        prior = IOImage.empty()
        emitted = strat.route("plc_a", outputs, prior)
        assert sorted(emitted) == [("plc_b", "x", True), ("plc_c", "x", True)]

    def test_present_to_absent_emits_nothing(self):
        block = {
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b"]}]
        }
        strat = TagStrategy(block)
        prior = IOImage(values={"x": True})
        emitted = strat.route("plc_a", IOImage.empty(), prior)
        assert emitted == []

    def test_first_scan_treats_prior_as_empty(self):
        block = {
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b"]}]
        }
        strat = TagStrategy(block)
        outputs = IOImage(values={"x": True})
        emitted = strat.route("plc_a", outputs, IOImage.empty())
        assert emitted == [("plc_b", "x", True)]


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
            "tags": [{"name": "x", "produced_by": "plc_a", "consumed_by": ["plc_b"]}]
        }
        strat = build_comm_strategy("tag", block)
        outputs = IOImage(values={"x": True})
        emitted = strat.route("plc_a", outputs, IOImage.empty())
        assert emitted == [("plc_b", "x", True)]
