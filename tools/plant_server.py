from __future__ import annotations
import argparse
import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import relay.plant  # noqa: F401  -- ensure plant registrations are loaded
from relay.io_image import IOImage
from relay.spec.schema import load_spec
from relay.strategies.plant import PlantProtocol, get_plant


def _outputs_to_dict(outputs: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(outputs):
        return dataclasses.asdict(outputs)
    return dict(vars(outputs))


class PlantSession:
    def __init__(self, plant: PlantProtocol) -> None:
        self._plant = plant

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "read_actuators":
            images = {
                plc_id: IOImage(values=values)
                for plc_id, values in params["latest_outputs"].items()
            }
            return {"actuators": dict(self._plant.read_actuators(images))}
        if method == "step":
            outputs = self._plant.step(float(params["dt_ms"]), params["actuators"])
            return {"outputs": _outputs_to_dict(outputs)}
        if method == "route_to_plcs":
            current = SimpleNamespace(**params["current"])
            prior = (
                SimpleNamespace(**params["prior"])
                if params.get("prior") is not None
                else None
            )
            emitted = self._plant.route_to_plcs(current, prior)
            return {
                "routed": [
                    {"to_plc": to_plc, "as_key": as_key, "value": value}
                    for to_plc, as_key, value in emitted
                ]
            }
        raise ValueError(f"unknown method {method!r}")


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    make_plant: Callable[[], PlantProtocol],
) -> None:
    session = PlantSession(make_plant())
    try:
        while True:
            line = await reader.readline()
            if not line:
                return
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = {"id": None, "error": {"message": "malformed request line"}}
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
                return
            try:
                result = session.dispatch(request["method"], request.get("params") or {})
                response = {"id": request.get("id"), "result": result}
            except Exception as exc:
                response = {
                    "id": request.get("id"),
                    "error": {"message": f"{type(exc).__name__}: {exc}"},
                }
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
    finally:
        writer.close()


async def serve(spec_path: Path, host: str, port: int) -> None:
    spec = load_spec(spec_path)
    factory = get_plant(spec.plant_type)
    plant_block = spec.plant_block
    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, lambda: factory(plant_block)),
        host,
        port,
    )
    bound_port = server.sockets[0].getsockname()[1]
    print(f"READY {bound_port}", flush=True)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve a registered plant model over the plant socket protocol"
    )
    parser.add_argument("spec", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        asyncio.run(serve(args.spec, args.host, args.port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
