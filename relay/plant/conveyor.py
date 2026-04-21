from __future__ import annotations
from dataclasses import dataclass, replace


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


class ConveyorPlant:
    BELT_A_LENGTH_M = 0.15
    BELT_B_START_M = 0.15

    def __init__(self, config: ConveyorConfig) -> None:
        self._config = config
        self._state = PlantState.initial()

    def step(self, elapsed_ms: float, belt_b_enable_signal: bool) -> PlantOutputs:
        dt_s = elapsed_ms / 1000.0
        state = self._state

        pending = state.belt_b_enable_pending_ms
        if state.belt_b_running:
            belt_b_running = True
        elif pending > 0:
            pending = max(0.0, pending - elapsed_ms)
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

    @property
    def state(self) -> PlantState:
        return self._state
