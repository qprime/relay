from __future__ import annotations
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Mapping

from relay.io_image import IOImage
from relay.strategies.plant import register_plant

if TYPE_CHECKING:
    from relay.spec.schema import TaskSpec


PLANT_PROMPT_FRAGMENT = """\
Plant:
  type: conveyor
  config:
    belt_speed_m_per_s: <float>
    sensor_trigger_threshold_m: <float>
    actuator_latency_ms: <float>
  routes:
    - { sensor: sensor_a_exit_triggered, to_plc: <plc_id>, as_key: sensor_a_exit, trigger: edge }
    - { sensor: part_at_b, to_plc: <plc_id>, as_key: part_at_b, trigger: level }
  actuators:
    - { from_plc: <plc_id>, key: belt_b_enable, as: belt_b_enable_signal }
"""


@dataclass(frozen=True)
class ConveyorConfig:
    belt_speed_m_per_s: float
    sensor_trigger_threshold_m: float
    actuator_latency_ms: float


@dataclass(frozen=True)
class PartState:
    position_m: float
    on_belt_a: bool
    on_belt_b: bool


@dataclass(frozen=True)
class PlantState:
    part: PartState
    belt_a_running: bool
    belt_b_running: bool
    belt_b_enable_pending_ms: float

    @staticmethod
    def initial() -> PlantState:
        return PlantState(
            part=PartState(position_m=0.0, on_belt_a=True, on_belt_b=False),
            belt_a_running=True,
            belt_b_running=False,
            belt_b_enable_pending_ms=0.0,
        )


@dataclass(frozen=True)
class PlantOutputs:
    sensor_a_exit_triggered: bool
    part_at_b: bool


_VALID_TRIGGERS = frozenset({"edge", "level"})


class ConveyorPlant:
    BELT_A_LENGTH_M = 0.15
    BELT_B_START_M = 0.15

    def __init__(self, plant_block: dict | None = None) -> None:
        block = plant_block or {}
        config = block.get("config", {})
        self._config = ConveyorConfig(**{
            k: config[k]
            for k in ("belt_speed_m_per_s", "sensor_trigger_threshold_m", "actuator_latency_ms")
            if k in config
        })
        self._routes: list[dict] = list(block.get("routes") or [])
        self._actuators: list[dict] = list(block.get("actuators") or [])
        self._state = PlantState.initial()

    def step(self, dt_ms: float, actuator_state: Mapping[str, Any]) -> PlantOutputs:
        dt_s = dt_ms / 1000.0
        state = self._state
        belt_b_enable_signal = bool(actuator_state.get("belt_b_enable_signal", False))

        pending = state.belt_b_enable_pending_ms
        if state.belt_b_running:
            belt_b_running = True
        elif pending > 0:
            pending = max(0.0, pending - dt_ms)
            belt_b_running = pending == 0.0
        elif belt_b_enable_signal:
            pending = self._config.actuator_latency_ms
            belt_b_running = False
        else:
            belt_b_running = False

        new_pos = state.part.position_m
        if state.belt_a_running and state.part.on_belt_a:
            advance = self._config.belt_speed_m_per_s * dt_s
            if not belt_b_running:
                advance = min(advance, max(0.0, self.BELT_A_LENGTH_M - state.part.position_m - 0.001))
            new_pos += advance
        if belt_b_running and state.part.on_belt_b:
            new_pos += self._config.belt_speed_m_per_s * dt_s

        on_belt_a = new_pos < self.BELT_A_LENGTH_M
        on_belt_b = new_pos >= self.BELT_B_START_M

        part = replace(
            state.part,
            position_m=new_pos,
            on_belt_a=on_belt_a,
            on_belt_b=on_belt_b,
        )

        self._state = replace(
            state,
            part=part,
            belt_b_running=belt_b_running,
            belt_b_enable_pending_ms=pending,
        )

        sensor_a_exit = (
            abs(new_pos - self.BELT_A_LENGTH_M) < self._config.sensor_trigger_threshold_m
        )
        part_at_b = on_belt_b

        return PlantOutputs(
            sensor_a_exit_triggered=sensor_a_exit,
            part_at_b=part_at_b,
        )

    def route_to_plcs(
        self,
        plant_output: PlantOutputs,
        prior_output: PlantOutputs | None,
    ) -> list[tuple[str, str, Any]]:
        emitted: list[tuple[str, str, Any]] = []
        for route in self._routes:
            sensor = route["sensor"]
            to_plc = route["to_plc"]
            as_key = route["as_key"]
            trigger = route.get("trigger", "level")
            current = getattr(plant_output, sensor, False)
            prior = getattr(prior_output, sensor, False) if prior_output is not None else False
            if trigger == "level":
                if current:
                    emitted.append((to_plc, as_key, current))
            elif trigger == "edge":
                if current and not prior:
                    emitted.append((to_plc, as_key, current))
        return emitted

    def read_actuators(
        self, latest_outputs: Mapping[str, IOImage]
    ) -> Mapping[str, Any]:
        result: dict[str, Any] = {}
        for entry in self._actuators:
            from_plc = entry["from_plc"]
            key = entry["key"]
            alias = entry["as"]
            io = latest_outputs.get(from_plc)
            value = io.get(key, False) if io is not None else False
            result[alias] = value
        return result

    def validate_config(self, plant_block: dict, spec: "TaskSpec") -> list[str]:
        issues: list[str] = []
        config = plant_block.get("config")
        if not isinstance(config, dict):
            issues.append("Plant.config must be a mapping")
        else:
            for key in ("belt_speed_m_per_s", "sensor_trigger_threshold_m", "actuator_latency_ms"):
                if key not in config:
                    issues.append(f"Plant.config.{key} is required")

        routes = plant_block.get("routes")
        plc_ids = set(spec.plc_ids)
        if routes is None:
            issues.append("Plant.routes is required")
        elif not isinstance(routes, list):
            issues.append("Plant.routes must be a list")
        else:
            for i, r in enumerate(routes):
                if not isinstance(r, dict):
                    issues.append(f"Plant.routes[{i}] must be a mapping")
                    continue
                for field in ("sensor", "to_plc", "as_key", "trigger"):
                    if field not in r:
                        issues.append(f"Plant.routes[{i}].{field} is required")
                to_plc = r.get("to_plc")
                if to_plc is not None and to_plc not in plc_ids:
                    issues.append(
                        f"Plant.routes[{i}].to_plc {to_plc!r} is not a declared plc_id"
                    )
                trigger = r.get("trigger")
                if trigger is not None and trigger not in _VALID_TRIGGERS:
                    issues.append(
                        f"Plant.routes[{i}].trigger {trigger!r} must be one of {sorted(_VALID_TRIGGERS)}"
                    )

        actuators = plant_block.get("actuators")
        if actuators is None:
            issues.append("Plant.actuators is required")
        elif not isinstance(actuators, list):
            issues.append("Plant.actuators must be a list")
        else:
            for i, a in enumerate(actuators):
                if not isinstance(a, dict):
                    issues.append(f"Plant.actuators[{i}] must be a mapping")
                    continue
                for field in ("from_plc", "key", "as"):
                    if field not in a:
                        issues.append(f"Plant.actuators[{i}].{field} is required")
                from_plc = a.get("from_plc")
                if from_plc is not None and from_plc not in plc_ids:
                    issues.append(
                        f"Plant.actuators[{i}].from_plc {from_plc!r} is not a declared plc_id"
                    )
        return issues

    @property
    def state(self) -> PlantState:
        return self._state


register_plant(
    "conveyor",
    lambda plant_block: ConveyorPlant(plant_block),
    prompt_fragment=PLANT_PROMPT_FRAGMENT,
)
