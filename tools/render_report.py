from __future__ import annotations
import argparse
import asyncio
import html
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from relay.clock import DEFAULT_SCAN_PERIOD_MS
from relay.generator.behavior import Trigger, parse_triggers
from relay.generator.st import compile_st_blocks
from relay.runtime.harness import simulate
from relay.spec.schema import TaskSpec, load_spec
from relay.strategies.assertions import ParsedAssertion, parse_assertion
from relay.trace import ScanRecord, TraceLog
from relay.trace_io import load_jsonl
from relay.verify.assertions import AssertionResult, evaluate_all

from tools.emit_host_inputs import emit_host_inputs
from tools.expectations import DEFAULT_MAX_SCANS
from tools.system_names import DuplicateSystemName, check_unique_system_names

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_HOST_BINARY = _REPO_ROOT / "host" / "build" / "relay_host_main"
_STANZA_MARKER = re.compile(r"\(\* trigger: (.+?) \*\)")

SIM_ONLY_NOTE = "sim only — host binary not built"

_CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 72rem;
       padding: 0 1rem; color: #1a1a1a; }
table { border-collapse: collapse; margin: 0.75rem 0; }
th, td { border: 1px solid #c8c8c8; padding: 0.35rem 0.6rem; text-align: left;
         vertical-align: top; }
th { background: #f0f0f0; }
code, pre { font-family: ui-monospace, monospace; font-size: 0.9em; }
pre { background: #f6f6f6; padding: 0.6rem; overflow-x: auto; }
.pass { color: #10701c; }
.fail { color: #a11212; }
.plain { display: block; }
.formal { color: #555; }
.lane-note { background: #fff4d6; border: 1px solid #d8b74a; display: inline-block;
             padding: 0.4rem 0.7rem; }
details { margin: 0.4rem 0; }
summary { cursor: pointer; color: #555; }
.trace td { font-family: ui-monospace, monospace; font-size: 0.85em; }
"""

_PRINT_CSS = """
@page { size: letter landscape; margin: 1.2cm; }
body { max-width: none; margin: 0; }
tr { break-inside: avoid; }
h1, h2, h3 { break-after: avoid; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
table.trace td { font-size: 0.78em; }
table.trace td:nth-child(-n+3) { white-space: nowrap; }
table.trace td:nth-child(n+4) { overflow-wrap: anywhere; }
.verdicts td { overflow-wrap: break-word; }
a { text-decoration: none; }
"""


@dataclass(frozen=True)
class Lane:
    name: str
    trace: TraceLog | None
    results: list[AssertionResult]


def build_lanes(
    spec: TaskSpec,
    *,
    host_binary: Path | None,
    max_scans: int = DEFAULT_MAX_SCANS,
    scan_period_ms: float = DEFAULT_SCAN_PERIOD_MS,
) -> list[Lane]:
    st_blocks = compile_st_blocks(spec)
    sim_trace = asyncio.run(
        simulate(spec, st_blocks, max_scans=max_scans, scan_period_ms=scan_period_ms)
    )
    lanes = [Lane("sim", sim_trace, evaluate_all(spec.assertions, sim_trace))]
    if host_binary is None:
        return lanes
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        spec_path = workdir / "spec.yaml"
        spec_path.write_text(yaml.safe_dump(spec.raw, sort_keys=False))
        resolved_path, blocks_path = emit_host_inputs(
            spec_path, workdir, scan_period_ms=scan_period_ms, max_scans=max_scans
        )
        for name, plant_spec in (("host", None), ("host-socket", spec_path)):
            lanes.append(
                _host_lane(
                    name,
                    spec,
                    host_binary,
                    resolved_path,
                    blocks_path,
                    workdir / f"{name}_trace.jsonl",
                    plant_spec,
                )
            )
    return lanes


def _spawn_plant_server(spec_path: Path) -> tuple[subprocess.Popen, int] | None:
    server = subprocess.Popen(
        [sys.executable, "-m", "tools.plant_server", str(spec_path), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert server.stdout is not None
    ready = server.stdout.readline().split()
    if not ready or ready[0] != "READY":
        server.terminate()
        server.wait(timeout=10)
        return None
    return server, int(ready[1])


def _host_lane(
    name: str,
    spec: TaskSpec,
    host_binary: Path,
    resolved_path: Path,
    blocks_path: Path,
    trace_path: Path,
    plant_spec: Path | None,
) -> Lane:
    command = [
        str(host_binary),
        "--spec", str(resolved_path),
        "--st-blocks", str(blocks_path),
        "--out", str(trace_path),
    ]
    server = None
    if plant_spec is not None:
        spawned = _spawn_plant_server(plant_spec)
        if spawned is None:
            print(f"{name} lane failed: plant server did not report READY", file=sys.stderr)
            return Lane(name, None, [])
        server, port = spawned
        command += ["--plant-endpoint", f"127.0.0.1:{port}"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(f"{name} lane failed: host run exceeded 120s", file=sys.stderr)
        return Lane(name, None, [])
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    if completed.returncode != 0:
        print(
            f"{name} lane failed (exit {completed.returncode}):\n{completed.stderr}",
            file=sys.stderr,
        )
        return Lane(name, None, [])
    with trace_path.open() as stream:
        trace = load_jsonl(stream)
    return Lane(name, trace, evaluate_all(spec.assertions, trace))


def event_records(trace: TraceLog) -> list[ScanRecord]:
    """A record is an event when something changed on its PLC since that PLC's
    previous record; each PLC's first record anchors the comparison.

    The receipt criterion is a rising one — truthy now, not truthy in the
    previous record — for the same reason the send criterion is. A latched
    producer emits its tag every scan, so after the first delivery the consumer
    records a truthy receipt on every remaining scan; keeping every truthy
    receipt would keep the idle tail this filter exists to elide. The
    activation is the rise, and the steady repetition after it says nothing new.
    """
    kept: list[ScanRecord] = []
    previous: dict[str, ScanRecord] = {}
    for record in trace.records:
        prior = previous.get(record.plc_id)
        previous[record.plc_id] = record
        if prior is None or _is_event(record, prior):
            kept.append(record)
    return kept


def _is_event(record: ScanRecord, prior: ScanRecord) -> bool:
    if dict(record.outputs.values) != dict(prior.outputs.values):
        return True
    if dict(record.io.values) != dict(prior.io.values):
        return True
    for key, send in record.sends.items():
        before = prior.sends.get(key)
        if send.value and not (before is not None and before.value):
            return True
    for key, receipt in record.recvs.items():
        before = prior.recvs.get(key)
        if receipt.value and not (before is not None and before.value):
            return True
    return False


def plain_words(parsed: ParsedAssertion) -> str | None:
    if parsed.form == "EVENTUALLY":
        return (
            f"{parsed.signals[0]} becomes true within {parsed.within_ms:g}ms "
            "of simulation start"
        )
    if parsed.form == "PRECEDES":
        first, second = parsed.signals
        return (
            f"{first} becomes true no later than {second}, "
            f"with a gap of at most {parsed.within_ms:g}ms"
        )
    if parsed.form == "CAUSES":
        cause, effect = parsed.signals
        return f"{effect} first activates because a received message carried {cause}"
    return None


def st_stanzas(st_block: str) -> dict[str, str]:
    stanzas: dict[str, str] = {}
    trigger_id: str | None = None
    lines: list[str] = []
    for line in st_block.splitlines():
        match = _STANZA_MARKER.fullmatch(line.strip())
        if match:
            if trigger_id is not None:
                stanzas[trigger_id] = "\n".join(lines)
            trigger_id = match.group(1)
            lines = [line]
        elif trigger_id is not None:
            lines.append(line)
    if trigger_id is not None:
        stanzas[trigger_id] = "\n".join(lines)
    return stanzas


def _h(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{_h(title)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _verdict_cell(result: AssertionResult, parsed: ParsedAssertion | None) -> str:
    if not result.passed:
        return f'<td class="fail">✗ {_h(result.reason)}</td>'
    if (
        parsed is not None
        and parsed.form == "EVENTUALLY"
        and parsed.within_ms is not None
        and result.witness_ms is not None
    ):
        margin = parsed.within_ms - result.witness_ms
        return (
            f'<td class="pass">✓ {result.witness_ms:.1f}ms '
            f"(budget {parsed.within_ms:g}ms, margin {margin:.1f}ms)</td>"
        )
    if (
        parsed is not None
        and parsed.form == "PRECEDES"
        and parsed.within_ms is not None
        and result.observed_gap_ms is not None
    ):
        margin = parsed.within_ms - result.observed_gap_ms
        return (
            f'<td class="pass">✓ gap {result.observed_gap_ms:.1f}ms '
            f"(budget {parsed.within_ms:g}ms, margin {margin:.1f}ms)</td>"
        )
    return f'<td class="pass">✓ {_h(result.reason)}</td>'


def _verdict_table(spec: TaskSpec, lanes: list[Lane]) -> str:
    header = "".join(
        f"<th>{_h(lane.name)}{' (failed)' if lane.trace is None else ''}</th>"
        for lane in lanes
    )
    rows: list[str] = []
    for index, text in enumerate(spec.assertions):
        parsed = parse_assertion(text)
        label = plain_words(parsed) if parsed else "unrecognized assertion form"
        cells: list[str] = []
        for lane in lanes:
            if lane.trace is None:
                cells.append('<td class="fail">✗ lane failed to run</td>')
            else:
                cells.append(_verdict_cell(lane.results[index], parsed))
        head = f'<span class="plain">{_h(label)}</span>' if label else ""
        rows.append(
            "<tr><td>"
            f'{head}<code class="formal">{_h(text)}</code>'
            "</td>" + "".join(cells) + "</tr>"
        )
    if not rows:
        rows.append(f'<tr><td colspan="{1 + len(lanes)}">no assertions</td></tr>')
    return (
        '<table class="verdicts">\n'
        f"<tr><th>assertion</th>{header}</tr>\n" + "\n".join(rows) + "\n</table>"
    )


def _trigger_words(trigger: Trigger) -> str:
    when = trigger.when
    if when.edge == "level":
        condition = f"while {when.signal} is true"
    else:
        condition = f"on the {when.edge} edge of {when.signal}"
    if when.debounce_ms > 0:
        condition += f" (stable for {when.debounce_ms}ms)"
    emit = trigger.emit
    if emit.mode == "latched":
        action = f"latch {emit.target_kind} {emit.target} on"
    elif emit.mode == "pulse":
        action = f"pulse {emit.target_kind} {emit.target} for {emit.duration_ms}ms"
    else:
        action = f"drive {emit.target_kind} {emit.target}"
    return f"{condition}, {action}"


def _scenario_section(spec: TaskSpec, st_blocks: dict[str, str]) -> str:
    parts = ["<h2>Scenario</h2>", "<h3>PLCs</h3>", "<ul>"]
    for plc in spec.plcs:
        role = plc.get("role")
        suffix = f" — {_h(role)}" if role else ""
        parts.append(f"<li><code>{_h(plc.get('id'))}</code>{suffix}</li>")
    parts.append("</ul>")

    comm = spec.comm_block
    parts.append("<h3>Comm</h3>")
    parts.append(f"<p>strategy: <code>{_h(comm.get('strategy'))}</code></p>")
    tags = comm.get("tags") or []
    if tags:
        parts.append("<ul>")
        for tag in tags:
            consumers = ", ".join(tag.get("consumed_by") or [])
            parts.append(
                f"<li><code>{_h(tag.get('name'))}</code> produced by "
                f"<code>{_h(tag.get('produced_by'))}</code>, consumed by "
                f"<code>{_h(consumers)}</code></li>"
            )
        parts.append("</ul>")

    plant = spec.plant_block
    parts.append("<h3>Plant</h3>")
    parts.append(f"<p>type: <code>{_h(plant.get('type'))}</code></p>")
    parts.append("<ul>")
    for key, value in (plant.get("config") or {}).items():
        parts.append(f"<li><code>{_h(key)}</code> = {_h(value)}</li>")
    for route in plant.get("routes") or []:
        parts.append(
            f"<li>sensor <code>{_h(route.get('sensor'))}</code> feeds "
            f"<code>{_h(route.get('to_plc'))}</code> as "
            f"<code>{_h(route.get('as_key'))}</code> "
            f"({_h(route.get('trigger', 'level'))})</li>"
        )
    for actuator in plant.get("actuators") or []:
        parts.append(
            f"<li>output <code>{_h(actuator.get('key'))}</code> from "
            f"<code>{_h(actuator.get('from_plc'))}</code> drives "
            f"<code>{_h(actuator.get('as'))}</code></li>"
        )
    parts.append("</ul>")

    parts.append("<h3>Behavior</h3>")
    for plc_id in spec.plc_ids:
        parts.append(f"<h4><code>{_h(plc_id)}</code></h4>")
        triggers = parse_triggers(spec.behavior.get(plc_id) or {})
        if not triggers:
            parts.append("<p>no triggers</p>")
            continue
        stanzas = st_stanzas(st_blocks.get(plc_id, ""))
        parts.append("<ul>")
        for trigger in triggers:
            item = [f"<li><code>{_h(trigger.id)}</code>: {_h(_trigger_words(trigger))}"]
            stanza = stanzas.get(trigger.id)
            if stanza is not None:
                item.append(
                    "<details><summary>what this compiled to</summary>"
                    f"<pre>{_h(stanza)}</pre></details>"
                )
            item.append("</li>")
            parts.append("".join(item))
        parts.append("</ul>")
    return "\n".join(parts)


def _values_cell(values: Mapping[str, Any]) -> str:
    return " ".join(f"{key}={value}" for key, value in sorted(values.items()))


def _record_row(record: ScanRecord) -> str:
    sends = " ".join(
        f"{key}#{send.count}={send.value}"
        for key, send in sorted(record.sends.items())
    )
    recvs = " ".join(
        f"{key}#{receipt.seq}={receipt.value} from {receipt.sender or '(unattributed)'}"
        for key, receipt in sorted(record.recvs.items())
    )
    return (
        f"<tr><td>{_h(record.plc_id)}</td><td>{record.clock.tick}</td>"
        f"<td>{record.clock.elapsed_ms:.1f}</td>"
        f"<td>{_h(_values_cell(record.io.values))}</td>"
        f"<td>{_h(_values_cell(record.outputs.values))}</td>"
        f"<td>{_h(sends)}</td><td>{_h(recvs)}</td></tr>"
    )


_TRACE_HEADER = (
    "<tr><th>plc</th><th>tick</th><th>ms</th><th>inputs</th>"
    "<th>outputs</th><th>sends</th><th>recvs</th></tr>"
)


def _trace_table(records: list[ScanRecord]) -> str:
    rows = "\n".join(_record_row(record) for record in records)
    return f'<table class="trace">\n{_TRACE_HEADER}\n{rows}\n</table>'


def _evidence_section(lanes: list[Lane]) -> str:
    parts = ["<h2>Evidence</h2>"]
    for lane in lanes:
        parts.append(f"<h3>{_h(lane.name)}</h3>")
        if lane.trace is None:
            parts.append('<p class="fail">lane failed to run; no trace captured</p>')
            continue
        if lane.results:
            parts.append("<ul>")
            for result in lane.results:
                mark, cls = ("✓", "pass") if result.passed else ("✗", "fail")
                parts.append(
                    f"<li><code>{_h(result.assertion)}</code> "
                    f'<span class="{cls}">{mark} {_h(result.reason)}</span></li>'
                )
            parts.append("</ul>")
        events = event_records(lane.trace)
        total = len(lane.trace.records)
        parts.append(
            f"<p>{len(events)} event scans of {total} recorded; idle scans elided</p>"
        )
        parts.append(_trace_table(events))
        parts.append(
            f"<details><summary>full trace ({total} records)</summary>"
            f"{_trace_table(lane.trace.records)}</details>"
        )
    return "\n".join(parts)


def render_detail(spec: TaskSpec, lanes: list[Lane], st_blocks: dict[str, str]) -> str:
    parts = [f"<h1>{_h(spec.system_name)}</h1>"]
    if not any(lane.name.startswith("host") for lane in lanes):
        parts.append(f'<p class="lane-note">{_h(SIM_ONLY_NOTE)}</p>')
    parts.append("<h2>Verdicts</h2>")
    parts.append(_verdict_table(spec, lanes))
    parts.append(_scenario_section(spec, st_blocks))
    parts.append(_evidence_section(lanes))
    return _page(spec.system_name, "\n".join(parts))


def render_index(entries: list[tuple[TaskSpec, list[Lane]]]) -> str:
    rows: list[str] = []
    for spec, lanes in entries:
        summaries: list[str] = []
        for lane in lanes:
            if lane.trace is None:
                summaries.append(f'<span class="fail">{_h(lane.name)} failed</span>')
                continue
            passed = sum(1 for result in lane.results if result.passed)
            total = len(lane.results)
            cls = "pass" if passed == total else "fail"
            summaries.append(f'<span class="{cls}">{_h(lane.name)} {passed}/{total}</span>')
        name = spec.system_name
        rows.append(
            f'<tr><td><a href="{_h(name)}.html">{_h(name)}</a></td>'
            f"<td>{' '.join(summaries)}</td></tr>"
        )
    body = (
        "<h1>relay checkpoint report</h1>\n<table>\n"
        "<tr><th>scenario</th><th>verdicts</th></tr>\n" + "\n".join(rows) + "\n</table>"
    )
    return _page("relay checkpoint report", body)


class MissingPdfSupport(Exception):
    pass


def write_pdfs(html_paths: list[Path]) -> list[Path]:
    """Render each page to a sibling PDF under `_PRINT_CSS`.

    The screen stylesheet is wrong on paper in two ways that lose content
    silently. `pre` and the trace table scroll under `overflow-x: auto` in a
    browser; paper has no scrollbar, so the ST stanzas clip at the right margin
    and the trace's `recvs` column runs off the page entirely. Landscape plus
    per-column wrapping fits all seven trace columns, and the first three are
    pinned `nowrap` because wrapping `plc_a` down three lines is unreadable.

    Every `<details>` prints expanded, so a PDF carries the full trace for each
    lane and runs to roughly fifty pages. That is deliberate: a reader cannot
    expand a disclosure widget on paper, and dropping the evidence half of the
    report would misrepresent it as shorter than it is.
    """
    try:
        from weasyprint import CSS, HTML
    except ImportError as exc:
        raise MissingPdfSupport(
            "PDF rendering needs weasyprint, which is not installed; "
            "run `uv sync` or `pip install 'relay[pdf]'`"
        ) from exc

    stylesheet = CSS(string=_PRINT_CSS)
    written: list[Path] = []
    for html_path in html_paths:
        pdf_path = html_path.with_suffix(".pdf")
        HTML(filename=str(html_path)).write_pdf(pdf_path, stylesheets=[stylesheet])
        written.append(pdf_path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render each task spec plus its execution traces as self-contained HTML"
    )
    parser.add_argument("specs", type=Path, nargs="*")
    parser.add_argument("--out", type=Path, default=Path("report"))
    parser.add_argument("--host-binary", type=Path, default=None)
    parser.add_argument("--max-scans", type=int, default=DEFAULT_MAX_SCANS)
    parser.add_argument("--scan-period-ms", type=float, default=DEFAULT_SCAN_PERIOD_MS)
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="also write a sibling PDF per page (needs the 'pdf' extra)",
    )
    args = parser.parse_args(argv)

    if args.host_binary is None:
        host_binary = _DEFAULT_HOST_BINARY if _DEFAULT_HOST_BINARY.exists() else None
    elif args.host_binary.exists():
        host_binary = args.host_binary
    else:
        parser.error(f"--host-binary {args.host_binary} does not exist")

    spec_paths = list(args.specs) or sorted((_REPO_ROOT / "specs").glob("*.yaml"))
    loaded = [(path, load_spec(path)) for path in spec_paths]
    try:
        check_unique_system_names(
            [(path, spec.system_name) for path, spec in loaded], ".html"
        )
    except DuplicateSystemName as e:
        print(e)
        return 1

    entries: list[tuple[TaskSpec, list[Lane]]] = []
    lane_failed = False
    for _, spec in loaded:
        lanes = build_lanes(
            spec,
            host_binary=host_binary,
            max_scans=args.max_scans,
            scan_period_ms=args.scan_period_ms,
        )
        lane_failed = lane_failed or any(lane.trace is None for lane in lanes)
        entries.append((spec, lanes))

    args.out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for spec, lanes in entries:
        out_path = args.out / f"{spec.system_name}.html"
        out_path.write_text(render_detail(spec, lanes, compile_st_blocks(spec)))
        written.append(out_path)
    index_path = args.out / "index.html"
    index_path.write_text(render_index(entries))
    written.append(index_path)
    for path in written:
        print(path)

    if args.pdf:
        try:
            for path in write_pdfs(written):
                print(path)
        except MissingPdfSupport as e:
            print(e, file=sys.stderr)
            return 1
    return 1 if lane_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
