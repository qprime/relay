#include <gtest/gtest.h>

#include <cmath>
#include <sstream>

#include <nlohmann/json.hpp>

#include "harness_helpers.hpp"
#include "relay_host/scan_executor.hpp"
#include "relay_host/st_parser.hpp"
#include "relay_host/trace.hpp"

namespace relay_host {
namespace {

struct TraceFixture {
    SignalTable table;
    std::vector<std::string> plc_ids{"plc_a"};
};

TraceFixture make_fixture() {
    TraceFixture fixture;
    fixture.table.add("sensor");
    fixture.table.add("motor");
    return fixture;
}

ScanTraceEntry make_entry(std::int64_t tick, double elapsed_ms, Cell io_value,
                          Cell output_value) {
    ScanTraceEntry entry{};
    entry.plc_index = 0;
    entry.clock = SimClock{tick, elapsed_ms};
    entry.input_count = 1;
    entry.input_cells[0] = CellSlot{0, io_value};
    entry.output_count = 1;
    entry.output_cells[0] = CellSlot{1, output_value};
    return entry;
}

TEST(TestRingBufferTrace, test_dump_jsonl_round_trip) {
    const TraceFixture fixture = make_fixture();
    TraceRing ring(4);
    ring.next_entry() = make_entry(0, 0.0, Cell{true}, Cell{std::int64_t{42}});
    ring.next_entry() = make_entry(1, 10.0, Cell{false}, Cell{2.5});
    std::ostringstream stream;
    ASSERT_TRUE(ring.dump_to_jsonl(stream, fixture.table, fixture.plc_ids).has_value());

    std::istringstream lines(stream.str());
    std::string line;
    std::size_t parsed = 0;
    while (std::getline(lines, line)) {
        const nlohmann::json record = nlohmann::json::parse(line);
        EXPECT_EQ(record["plc_id"], "plc_a");
        EXPECT_EQ(record["tick"], static_cast<std::int64_t>(parsed));
        ++parsed;
    }
    ASSERT_EQ(parsed, 2u);
    const nlohmann::json second =
        nlohmann::json::parse(stream.str().substr(stream.str().find('\n') + 1));
    EXPECT_EQ(second["io_snapshot"]["sensor"], false);
    EXPECT_EQ(second["outputs"]["motor"], 2.5);
}

TEST(TestRingBufferTrace, test_keys_emitted_in_sorted_order) {
    const TraceFixture fixture = make_fixture();
    TraceRing ring(2);
    ring.next_entry() = make_entry(0, 0.0, Cell{true}, Cell{false});
    std::ostringstream stream;
    ASSERT_TRUE(ring.dump_to_jsonl(stream, fixture.table, fixture.plc_ids).has_value());
    const std::string line = stream.str();
    const std::size_t elapsed_pos = line.find("\"elapsed_ms\"");
    const std::size_t io_pos = line.find("\"io_snapshot\"");
    const std::size_t outputs_pos = line.find("\"outputs\"");
    const std::size_t plc_pos = line.find("\"plc_id\"");
    const std::size_t tick_pos = line.find("\"tick\"");
    ASSERT_NE(elapsed_pos, std::string::npos);
    EXPECT_LT(elapsed_pos, io_pos);
    EXPECT_LT(io_pos, outputs_pos);
    EXPECT_LT(outputs_pos, plc_pos);
    EXPECT_LT(plc_pos, tick_pos);
}

TEST(TestRingBufferTrace, test_non_finite_double_rejected_at_dump) {
    const TraceFixture fixture = make_fixture();
    TraceRing ring(2);
    ring.next_entry() =
        make_entry(0, 0.0, Cell{std::nan("")}, Cell{false});
    std::ostringstream stream;
    const auto dumped = ring.dump_to_jsonl(stream, fixture.table, fixture.plc_ids);
    ASSERT_FALSE(dumped.has_value());
    EXPECT_NE(dumped.error().message.find("non-finite"), std::string::npos);
}

TEST(TestRingBufferTrace, test_integral_double_formats_with_trailing_point) {
    EXPECT_EQ(format_json_double(990.0), "990.0");
    EXPECT_EQ(format_json_double(0.0), "0.0");
    EXPECT_EQ(format_json_double(-0.0), "-0.0");
    EXPECT_EQ(format_json_double(10.5), "10.5");
    EXPECT_EQ(format_json_double(0.0001), "0.0001");
    EXPECT_EQ(format_json_double(0.00001), "1e-05");
    EXPECT_EQ(format_json_double(1e16), "1e+16");
    EXPECT_EQ(format_json_double(1e15), "1000000000000000.0");
    EXPECT_EQ(format_json_double(-290.0), "-290.0");
}

TEST(TestRingBufferTrace, test_capacity_rollover_drops_oldest_and_warns) {
    const TraceFixture fixture = make_fixture();
    TraceRing ring(2);
    ring.next_entry() = make_entry(0, 0.0, Cell{true}, Cell{false});
    ring.next_entry() = make_entry(1, 10.0, Cell{true}, Cell{false});
    ring.next_entry() = make_entry(2, 20.0, Cell{true}, Cell{false});
    EXPECT_EQ(ring.size(), 2u);
    EXPECT_EQ(ring.dropped(), 1u);
    EXPECT_EQ(ring.at(0).clock.tick, 1);
    EXPECT_EQ(ring.at(1).clock.tick, 2);
}

TEST(TestRingBufferTrace, test_cell_overflow_records_scan_error) {
    ResolvedTaskSpec spec = testing::minimal_two_plc_spec();
    auto program = parse_st("");
    ASSERT_TRUE(program.has_value());
    SignalTable table;
    for (std::size_t index = 0; index < kMaxCellsPerScan + 1; ++index) {
        table.add("signal_" + std::to_string(index));
    }
    auto block = ValidatedSt::try_from(std::move(*program), table, spec.plc_ids);
    ASSERT_TRUE(block.has_value());
    PlcScanState state(0, &*block, table.size());
    CommBuffer comm(table.size());
    for (std::uint32_t id = 0; id < table.size(); ++id) {
        comm.set(id, Cell{true});
    }
    ScanTraceEntry entry{};
    OutgoingBuffer outgoing;
    const auto error =
        execute_one_scan(state, comm, SimClock::zero(), 10.0, entry, outgoing);
    ASSERT_TRUE(error.has_value());
    EXPECT_EQ(error->kind, ScanErrorKind::CellOverflow);
    ASSERT_TRUE(entry.error.has_value());
    EXPECT_EQ(entry.error->kind, ScanErrorKind::CellOverflow);
}

}  // namespace
}  // namespace relay_host
