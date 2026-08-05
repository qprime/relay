from __future__ import annotations
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol

from relay.io_image import IOImage

if TYPE_CHECKING:
    from relay.spec.schema import TaskSpec


class PlantOutput(Protocol):
    """Marker protocol; per-plant dataclass. Harness does not inspect fields."""


class PlantProtocol(Protocol):
    def step(self, dt_ms: float, actuator_state: Mapping[str, Any]) -> PlantOutput: ...

    def route_to_plcs(
        self,
        plant_output: PlantOutput,
        prior_output: PlantOutput | None,
    ) -> list[tuple[str, str, Any]]: ...

    def read_actuators(
        self, latest_outputs: Mapping[str, IOImage]
    ) -> Mapping[str, Any]: ...

    def validate_config(self, plant_block: dict, spec: "TaskSpec") -> list[str]: ...


class UnknownPlantType(Exception):
    pass


PlantFactory = Callable[[dict], PlantProtocol]

_REGISTRY: dict[str, PlantFactory] = {}


def register_plant(name: str, factory: PlantFactory) -> None:
    _REGISTRY[name] = factory


def get_plant(name: str) -> PlantFactory:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise UnknownPlantType(f"unknown plant type {name!r}; known: {known}")
    return _REGISTRY[name]


def known_plants() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
