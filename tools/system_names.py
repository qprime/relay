from __future__ import annotations
from pathlib import Path


class DuplicateSystemName(Exception):
    pass


def check_unique_system_names(named: list[tuple[Path, str]], suffix: str) -> None:
    by_name: dict[str, list[Path]] = {}
    for path, name in named:
        by_name.setdefault(name, []).append(path)
    collisions = {name: paths for name, paths in by_name.items() if len(paths) > 1}
    if not collisions:
        return
    raise DuplicateSystemName(
        "; ".join(
            f"System.name {name!r} is declared by "
            + ", ".join(p.name for p in sorted(paths))
            + f", which would file both under {name}{suffix}"
            for name, paths in sorted(collisions.items())
        )
        + "; rename one so each spec owns its own artifact"
    )
