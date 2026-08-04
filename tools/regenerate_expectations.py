from __future__ import annotations
import argparse
from pathlib import Path

from tools.expectations import build_expectations, write_expectations

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPECS_DIR = _REPO_ROOT / "specs"
_EXPECTATIONS_DIR = _SPECS_DIR / "expectations"


def regenerate_all() -> list[Path]:
    written: list[Path] = []
    for spec_path in sorted(_SPECS_DIR.glob("*.yaml")):
        artifact = build_expectations(spec_path)
        out_path = _EXPECTATIONS_DIR / f"{artifact['system_name']}.expected.json"
        write_expectations(artifact, out_path)
        written.append(out_path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate every expectations artifact under specs/expectations/"
    )
    parser.parse_args(argv)
    for out_path in regenerate_all():
        print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
