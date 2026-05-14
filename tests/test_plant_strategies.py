from __future__ import annotations

import pytest

import relay.plant  # noqa: F401  -- triggers conveyor registration
from relay.io_image import IOImage
from relay.plant.conveyor import ConveyorPlant, PlantOutputs
from relay.strategies.plant import UnknownPlantType, get_plant


def _conveyor_block(routes=None, actuators=None) -> dict:
    return {
        "type": "conveyor",
        "config": {
            "belt_speed_m_per_s": 0.5,
            "sensor_trigger_threshold_m": 0.1,
            "actuator_latency_ms": 50.0,
        },
        "routes": routes or [],
        "actuators": actuators or [],
    }


class TestConveyorRegistration:
    def test_conveyor_self_registers_on_plant_import(self):
        factory = get_plant("conveyor")
        assert callable(factory)

    def test_factory_constructs_from_spec_config(self):
        factory = get_plant("conveyor")
        plant = factory(_conveyor_block())
        assert isinstance(plant, ConveyorPlant)
        assert plant._config.belt_speed_m_per_s == 0.5  # noqa: SLF001

    def test_unknown_plant_type_raises(self):
        with pytest.raises(UnknownPlantType):
            get_plant("imaginary")


class TestConveyorRoutingTriggers:
    def test_level_trigger_emits_every_scan_while_true(self):
        plant = ConveyorPlant(
            _conveyor_block(
                routes=[
                    {"sensor": "part_at_b", "to_plc": "plc_b", "as_key": "part_at_b", "trigger": "level"}
                ]
            )
        )
        out = PlantOutputs(sensor_a_exit_triggered=False, part_at_b=True)
        first = plant.route_to_plcs(out, None)
        second = plant.route_to_plcs(out, out)
        assert first == [("plc_b", "part_at_b", True)]
        assert second == [("plc_b", "part_at_b", True)]

    def test_edge_trigger_emits_only_on_transition(self):
        plant = ConveyorPlant(
            _conveyor_block(
                routes=[
                    {"sensor": "sensor_a_exit_triggered", "to_plc": "plc_a", "as_key": "sensor_a_exit", "trigger": "edge"}
                ]
            )
        )
        out = PlantOutputs(sensor_a_exit_triggered=True, part_at_b=False)
        first = plant.route_to_plcs(out, None)
        second = plant.route_to_plcs(out, out)
        assert first == [("plc_a", "sensor_a_exit", True)]
        assert second == []

    def test_edge_trigger_first_scan_treated_as_rising(self):
        plant = ConveyorPlant(
            _conveyor_block(
                routes=[
                    {"sensor": "sensor_a_exit_triggered", "to_plc": "plc_a", "as_key": "sensor_a_exit", "trigger": "edge"}
                ]
            )
        )
        out = PlantOutputs(sensor_a_exit_triggered=True, part_at_b=False)
        emitted = plant.route_to_plcs(out, None)
        assert emitted == [("plc_a", "sensor_a_exit", True)]


class TestConveyorActuatorRead:
    def test_read_actuators_projects_belt_b_enable(self):
        plant = ConveyorPlant(
            _conveyor_block(
                actuators=[
                    {"from_plc": "plc_b", "key": "belt_b_enable", "as": "belt_b_enable_signal"}
                ]
            )
        )
        latest = {"plc_b": IOImage(values={"belt_b_enable": True})}
        state = plant.read_actuators(latest)
        assert state == {"belt_b_enable_signal": True}

    def test_read_actuators_defaults_to_false_when_missing(self):
        plant = ConveyorPlant(
            _conveyor_block(
                actuators=[
                    {"from_plc": "plc_b", "key": "belt_b_enable", "as": "belt_b_enable_signal"}
                ]
            )
        )
        latest = {"plc_b": IOImage.empty()}
        state = plant.read_actuators(latest)
        assert state == {"belt_b_enable_signal": False}
