"""`relay_verify` links `relay_core` and nothing else.

The C++ analogue of `TestVerdictIOPurity`. The compiler already refuses an
`#include <asio.hpp>` in a `relay_verify` source, because the target carries no
include path for it — but that enforcement disappears the moment someone adds
the library to the link line to make a build error go away. This test is what
makes that edit visible.

See docs/invariants/host_verification_path_purity.md.
"""

from __future__ import annotations
import re
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parents[1] / "host"
CMAKE = HOST / "CMakeLists.txt"

PURE_SOURCES = ("src/verify/assertion_parser.cpp", "src/verify/verifier.cpp")
PURE_HEADERS = (
    "include/relay_host/verify/assertion_parser.hpp",
    "include/relay_host/verify/trace_model.hpp",
    "include/relay_host/verify/verdict.hpp",
)

ALLOWED_PROJECT_HEADERS = {
    "relay_host/io_image.hpp",
    "relay_host/json_text.hpp",
    "relay_host/verify/assertion_parser.hpp",
    "relay_host/verify/trace_model.hpp",
    "relay_host/verify/verdict.hpp",
}

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)


def _includes(relative: str) -> list[str]:
    return _INCLUDE_RE.findall((HOST / relative).read_text())


def _link_libraries(target: str) -> list[str]:
    match = re.search(
        rf"target_link_libraries\(\s*{target}\s+\w+\s+([^)]*)\)", CMAKE.read_text()
    )
    assert match, f"no target_link_libraries entry for {target}"
    return match.group(1).split()


@pytest.mark.parametrize("relative", PURE_SOURCES + PURE_HEADERS)
def test_pure_units_include_no_io_libraries(relative):
    offenders = [
        name
        for name in _includes(relative)
        if name.startswith(("asio", "nlohmann"))
        or (name.startswith("relay_host/") and name not in ALLOWED_PROJECT_HEADERS)
    ]
    assert offenders == [], (
        f"{relative} includes {offenders}. The verifier's purity is a property of "
        "the build graph; a convenience include turns a mechanical guarantee back "
        "into a review convention."
    )


def test_relay_verify_links_relay_core_only():
    assert _link_libraries("relay_verify") == ["relay_core"], (
        "relay_verify may link relay_core and nothing else. Adding a library "
        "here is how the compile-time enforcement gets switched off."
    )


def test_relay_verify_io_is_the_only_verify_target_with_nlohmann():
    assert "nlohmann_header" in _link_libraries("relay_verify_io"), (
        "the trace reader sits outside the purity boundary, exactly as "
        "relay/trace_io.py sits outside the Python invariant's"
    )
    assert "nlohmann_header" not in _link_libraries("relay_verify")


def test_relay_core_depends_on_nothing():
    """The extraction is what lets the verifier reuse `Cell` and
    `format_json_double` without inheriting the runtime's dependency set."""
    assert re.search(r"target_link_libraries\(\s*relay_core\b", CMAKE.read_text()) is None, (
        "relay_core must link nothing; anything it takes on, relay_verify "
        "inherits"
    )


def test_verify_sources_take_streams_not_paths():
    """Streams keep file-location policy with the caller, the same rule
    wire_format_serialization holds on the Python side."""
    for relative in PURE_SOURCES + PURE_HEADERS + (
        "include/relay_host/verify/trace_reader.hpp",
        "include/relay_host/verify/verdict_writer.hpp",
    ):
        text = (HOST / relative).read_text()
        assert "filesystem" not in text, f"{relative} names std::filesystem"
        assert "ifstream" not in text and "ofstream" not in text, (
            f"{relative} opens a file; the caller owns file-location policy"
        )
