from __future__ import annotations
import sys
from pathlib import Path

import pytest

import tools.render_report as render_report
from relay.generator.st import compile_st_blocks
from relay.spec.schema import TaskSpec, load_spec
from tools.render_report import (
    Lane,
    build_lanes,
    event_records,
    render_detail,
    render_index,
    st_stanzas,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_BINARY = REPO_ROOT / "host" / "build" / "relay_host_main"
CONVEYOR_SPEC = REPO_ROOT / "specs" / "conveyor_handoff.yaml"
PULSE_SPEC = REPO_ROOT / "specs" / "conveyor_pulse_release.yaml"

SKIP_REASON = (
    f"C++ host binary absent at {HOST_BINARY}; build it with "
    "cmake -S host -B host/build && cmake --build host/build"
)


@pytest.fixture(scope="module")
def conveyor_spec():
    return load_spec(CONVEYOR_SPEC)


@pytest.fixture(scope="module")
def pulse_spec():
    return load_spec(PULSE_SPEC)


@pytest.fixture(scope="module")
def conveyor_lanes(conveyor_spec):
    return build_lanes(conveyor_spec, host_binary=None)


@pytest.fixture(scope="module")
def pulse_lanes(pulse_spec):
    return build_lanes(pulse_spec, host_binary=None)


@pytest.fixture(scope="module")
def conveyor_page(conveyor_spec, conveyor_lanes):
    return render_detail(conveyor_spec, conveyor_lanes, compile_st_blocks(conveyor_spec))


class TestRenderReport:
    def test_index_lists_every_spec_with_green_summary(
        self, conveyor_spec, pulse_spec, conveyor_lanes, pulse_lanes
    ):
        page = render_index([(conveyor_spec, conveyor_lanes), (pulse_spec, pulse_lanes)])
        assert 'href="conveyor_handoff.html"' in page
        assert 'href="conveyor_pulse_release.html"' in page
        assert "sim 3/3" in page
        assert "sim 2/2" in page
        assert 'class="fail"' not in page

    def test_detail_verdict_table_carries_sim_numbers(self, conveyor_page):
        assert "290.0ms" in conveyor_page
        assert "gap 10.0ms" in conveyor_page

    def test_event_filter_keeps_activation_scans_drops_idle(self, conveyor_lanes):
        trace = conveyor_lanes[0].trace
        assert trace is not None
        kept = {(record.plc_id, record.clock.tick) for record in event_records(trace)}
        assert ("plc_a", 10) in kept
        assert ("plc_b", 11) in kept
        assert ("plc_a", 3) not in kept
        assert len(kept) < len(trace.records) // 10

    def test_st_stanza_sliced_by_trigger_marker(self, conveyor_spec):
        blocks = compile_st_blocks(conveyor_spec)
        assert st_stanzas(blocks["plc_a"]) == {"handoff_on_exit": blocks["plc_a"]}
        assert st_stanzas(blocks["plc_b"]) == {"belt_on_handoff": blocks["plc_b"]}
        merged = blocks["plc_a"] + "\n" + blocks["plc_b"]
        assert st_stanzas(merged) == {
            "handoff_on_exit": blocks["plc_a"],
            "belt_on_handoff": blocks["plc_b"],
        }

    def test_failing_assertion_renders_failed_cell(self, tmp_path):
        broken = tmp_path / "broken.yaml"
        broken.write_text(
            CONVEYOR_SPEC.read_text().replace(
                '"EVENTUALLY(part_at_b, within: 400ms)"',
                '"EVENTUALLY(signal_that_never_fires, within: 400ms)"',
            )
        )
        spec = load_spec(broken)
        lanes = build_lanes(spec, host_binary=None)
        page = render_detail(spec, lanes, compile_st_blocks(spec))
        assert '<td class="fail">✗' in page
        assert "signal_that_never_fires" in page
        assert "never true within 400.0ms" in page

    def test_missing_host_binary_marks_sim_only(self, conveyor_page):
        assert "sim only — host binary not built" in conveyor_page
        assert "<th>host</th>" not in conveyor_page
        assert "host-socket" not in conveyor_page

    def test_html_is_self_contained(self, conveyor_page):
        assert "http://" not in conveyor_page
        assert "https://" not in conveyor_page
        assert "src=" not in conveyor_page

    def test_signal_names_are_escaped(self):
        raw = {
            "System": {"name": "escape_probe", "plcs": [{"id": "plc_a"}]},
            "Comm": {"strategy": "tag", "tags": []},
            "Behavior": {
                "plc_a": {
                    "triggers": [
                        {
                            "id": "<script>alert(1)</script>",
                            "when": {"signal": "s<b>", "edge": "rising"},
                            "emit": {"output": "o", "mode": "steady"},
                        }
                    ]
                }
            },
        }
        spec = TaskSpec(raw=raw)
        page = render_detail(spec, [], compile_st_blocks(spec))
        assert "<script>" not in page
        assert "&lt;script&gt;" in page

    def test_same_inputs_produce_identical_bytes(self, conveyor_spec):
        st_blocks = compile_st_blocks(conveyor_spec)
        first = render_detail(
            conveyor_spec, build_lanes(conveyor_spec, host_binary=None), st_blocks
        )
        second = render_detail(
            conveyor_spec, build_lanes(conveyor_spec, host_binary=None), st_blocks
        )
        assert first == second

    def test_zero_trigger_plc_renders_no_triggers(self):
        raw = {
            "System": {
                "name": "zero_trigger_probe",
                "plcs": [{"id": "plc_a"}, {"id": "plc_b"}],
            },
            "Comm": {"strategy": "tag", "tags": []},
            "Behavior": {
                "plc_a": {
                    "triggers": [
                        {
                            "id": "t1",
                            "when": {"signal": "s", "edge": "rising"},
                            "emit": {"output": "o", "mode": "steady"},
                        }
                    ]
                }
            },
        }
        spec = TaskSpec(raw=raw)
        page = render_detail(spec, [], compile_st_blocks(spec))
        assert "no triggers" in page
        assert page.count("what this compiled to") == 1

    def test_pulse_detail_page_has_no_gap_content(self, pulse_spec, pulse_lanes):
        page = render_detail(pulse_spec, pulse_lanes, compile_st_blocks(pulse_spec))
        assert "290.0ms" in page
        assert "gap" not in page

    def test_failed_lane_renders_failed_header_and_cells(self, conveyor_spec, conveyor_lanes):
        lanes = conveyor_lanes + [Lane("host", None, [])]
        page = render_detail(conveyor_spec, lanes, compile_st_blocks(conveyor_spec))
        assert "<th>host (failed)</th>" in page
        assert page.count("lane failed to run") == 4
        assert "sim only" not in page


class TestRenderReportMain:
    def test_duplicate_system_name_aborts_before_any_write(self, tmp_path, capsys):
        first = tmp_path / "a.yaml"
        second = tmp_path / "z_copy.yaml"
        first.write_text(CONVEYOR_SPEC.read_text())
        second.write_text(CONVEYOR_SPEC.read_text())
        out = tmp_path / "report"
        assert render_report.main([str(first), str(second), "--out", str(out)]) == 1
        message = capsys.readouterr().out
        assert "conveyor_handoff" in message
        assert "a.yaml" in message and "z_copy.yaml" in message
        assert not out.exists()

    def test_failed_host_run_marks_lanes_failed_and_exits_nonzero(self, tmp_path):
        out = tmp_path / "report"
        code = render_report.main(
            [str(CONVEYOR_SPEC), "--out", str(out), "--host-binary", "/bin/false"]
        )
        assert code == 1
        page = (out / "conveyor_handoff.html").read_text()
        assert "<th>host (failed)</th>" in page
        assert "<th>host-socket (failed)</th>" in page
        assert "lane failed to run" in page
        assert (out / "index.html").exists()

    def test_missing_default_binary_renders_sim_only_and_exits_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(render_report, "_DEFAULT_HOST_BINARY", tmp_path / "absent")
        out = tmp_path / "report"
        assert render_report.main([str(CONVEYOR_SPEC), "--out", str(out)]) == 0
        page = (out / "conveyor_handoff.html").read_text()
        assert "sim only — host binary not built" in page
        assert "<th>host</th>" not in page
        assert (out / "index.html").exists()

    def test_pdf_flag_writes_a_sibling_pdf_per_page(self, tmp_path, monkeypatch):
        pytest.importorskip("weasyprint")
        monkeypatch.setattr(render_report, "_DEFAULT_HOST_BINARY", tmp_path / "absent")
        out = tmp_path / "report"
        assert render_report.main([str(CONVEYOR_SPEC), "--out", str(out), "--pdf"]) == 0
        for name in ("conveyor_handoff", "index"):
            pdf = out / f"{name}.pdf"
            assert pdf.exists(), f"{name}.pdf was not written"
            assert pdf.read_bytes().startswith(b"%PDF"), f"{name}.pdf is not a PDF"

    def test_pdf_without_weasyprint_reports_the_install_command(self, tmp_path, monkeypatch):
        monkeypatch.setattr(render_report, "_DEFAULT_HOST_BINARY", tmp_path / "absent")
        monkeypatch.setitem(sys.modules, "weasyprint", None)
        out = tmp_path / "report"
        assert render_report.main([str(CONVEYOR_SPEC), "--out", str(out), "--pdf"]) == 1
        assert (out / "conveyor_handoff.html").exists(), (
            "a missing PDF dependency must not discard the HTML that already rendered"
        )
        assert not (out / "conveyor_handoff.pdf").exists()

    def test_explicit_missing_host_binary_is_an_error(self, tmp_path):
        with pytest.raises(SystemExit) as excinfo:
            render_report.main(
                [
                    str(CONVEYOR_SPEC),
                    "--out",
                    str(tmp_path / "report"),
                    "--host-binary",
                    str(tmp_path / "typo"),
                ]
            )
        assert excinfo.value.code == 2
        assert not (tmp_path / "report").exists()


@pytest.mark.skipif(not HOST_BINARY.exists(), reason=SKIP_REASON)
class TestRenderReportHostLanes:
    def test_host_lanes_render_and_agree_on_verdicts(self, conveyor_spec):
        lanes = build_lanes(conveyor_spec, host_binary=HOST_BINARY)
        assert [lane.name for lane in lanes] == ["sim", "host", "host-socket"]
        for lane in lanes:
            assert lane.trace is not None, f"{lane.name} lane failed to run"
            assert all(result.passed for result in lane.results), (
                f"{lane.name}: {[r.reason for r in lane.results if not r.passed]}"
            )
        page = render_detail(conveyor_spec, lanes, compile_st_blocks(conveyor_spec))
        assert "<th>host</th>" in page
        assert "<th>host-socket</th>" in page
        assert "✗" not in page
