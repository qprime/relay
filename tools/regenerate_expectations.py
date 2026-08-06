from __future__ import annotations
import argparse
from pathlib import Path
from typing import Any

from tools.expectations import build_expectations, write_expectations

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPECS_DIR = _REPO_ROOT / "specs"
_EXPECTATIONS_DIR = _SPECS_DIR / "expectations"


class DuplicateSystemName(Exception):
    pass


def _check_unique_system_names(specs: list[tuple[Path, dict[str, Any]]]) -> None:
    by_name: dict[str, list[Path]] = {}
    for spec_path, artifact in specs:
        by_name.setdefault(artifact["system_name"], []).append(spec_path)
    collisions = {
        name: paths for name, paths in by_name.items() if len(paths) > 1
    }
    if not collisions:
        return
    raise DuplicateSystemName(
        "; ".join(
            f"System.name {name!r} is declared by "
            + ", ".join(p.name for p in sorted(paths))
            + f", which would file both under {name}.expected.json"
            for name, paths in sorted(collisions.items())
        )
        + "; rename one so each spec owns its own artifact"
    )


def regenerate_all() -> list[Path]:
    specs = [
        (spec_path, build_expectations(spec_path))
        for spec_path in sorted(_SPECS_DIR.glob("*.yaml"))
    ]
    _check_unique_system_names(specs)
    written: list[Path] = []
    for _, artifact in specs:
        out_path = _EXPECTATIONS_DIR / f"{artifact['system_name']}.expected.json"
        write_expectations(artifact, out_path)
        written.append(out_path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate every expectations artifact under specs/expectations/"
    )
    parser.parse_args(argv)
    try:
        written = regenerate_all()
    except DuplicateSystemName as e:
        print(e)
        return 1
    for out_path in written:
        print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
