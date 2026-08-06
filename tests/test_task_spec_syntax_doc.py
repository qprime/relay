from __future__ import annotations
import re
from pathlib import Path

import pytest

from tools.validate_spec import validate_spec_file

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANUAL = _REPO_ROOT / "docs" / "task_spec_syntax.md"

_FENCE_RE = re.compile(r"^```yaml *(?P<info>\S*) *$\n(?P<body>.*?)^```$", re.M | re.S)
_KINDS = ("spec", "fragment")


def _blocks() -> list[tuple[str, str]]:
    text = _MANUAL.read_text()
    inline = re.sub(r"` ```yaml [a-z]+ `", "", text)
    return [(m.group("info"), m.group("body")) for m in _FENCE_RE.finditer(inline)]


def _specs() -> list[str]:
    return [body for info, body in _blocks() if info == "spec"]


class TestManualExamples:
    def test_every_yaml_fence_declares_its_kind(self):
        unmarked = [body for info, body in _blocks() if info not in _KINDS]
        assert unmarked == [], (
            "a ```yaml fence in docs/task_spec_syntax.md declares no kind; mark it "
            f"'yaml spec' (validated) or 'yaml fragment' (illustrative): {unmarked}"
        )

    def test_manual_carries_at_least_one_complete_spec(self):
        assert _specs()

    @pytest.mark.parametrize("index", range(len(_specs())))
    def test_complete_specs_validate(self, index, tmp_path):
        path = tmp_path / "spec.yaml"
        path.write_text(_specs()[index])
        assert validate_spec_file(path) == []
