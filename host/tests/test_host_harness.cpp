#include <gtest/gtest.h>

#include <map>
#include <sstream>
#include <vector>

#include "harness_helpers.hpp"

namespace relay_host {
namespace {

using testing::conveyor_blocks;
using testing::conveyor_spec;
using testing::minimal_two_plc_spec;
using testing::run_harness;

HostHarness::Config fast_config(std::int64_t max_scans) {
    return HostHarness::Config{1.0, max_scans, 100000};
}

std::optional<Cell> io_value(const ScanTraceEntry& entry, const SignalTable& table,
                             std::string_view name) {
    for (std::uint32_t index = 0; index < entry.input_count; ++index) {
        if (table.name_of(entry.input_cells[index].signal_id) == name) {
            return entry.input_cells[index].value;
        }
    }
    return std::nullopt;
}

bool ever_delivered(const TraceRing& trace, const SignalTable& table,
                    std::uint32_t plc_index, std::string_view name) {
    for (std::size_t index = 0; index < trace.size(); ++index) {
        const ScanTraceEntry& entry = trace.at(index);
        if (entry.plc_index != plc_index) {
            continue;
        }
        const std::optional<Cell> value = io_value(entry, table, name);
        if (value.has_value() && is_truthy(*value)) {
            return true;
        }
    }
    return false;
}

TEST(TestHostHarness, test_fb_outgoing_send_is_delivered) {
    const auto harness = run_harness(
        minimal_two_plc_spec(),
        {{"plc_a", "_send_plc_b_flag := TRUE;"}, {"plc_b", ""}}, fast_config(4));
    ASSERT_FALSE(harness->run_error().has_value());
    EXPECT_TRUE(ever_delivered(harness->trace(), harness->signal_table(), 1, "flag"));
}

TEST(TestHostHarness, test_each_plc_produces_own_monotonic_clock) {
    const auto harness =
        run_harness(minimal_two_plc_spec(), {{"plc_a", ""}, {"plc_b", ""}},
                    fast_config(20));
    ASSERT_FALSE(harness->run_error().has_value());
    const TraceRing& trace = harness->trace();
    ASSERT_EQ(trace.size(), 40u);
    std::map<std::uint32_t, std::int64_t> next_tick;
    for (std::size_t index = 0; index < trace.size(); ++index) {
        const ScanTraceEntry& entry = trace.at(index);
        const std::int64_t expected = next_tick[entry.plc_index];
        EXPECT_EQ(entry.clock.tick, expected)
            << "plc_index " << entry.plc_index << " skipped or repeated a tick";
        EXPECT_DOUBLE_EQ(entry.clock.elapsed_ms,
                         static_cast<double>(entry.clock.tick) * 1.0)
            << "elapsed_ms must derive from the PLC's own scan period, not wall clock";
        next_tick[entry.plc_index] = expected + 1;
    }
    for (const auto& [plc, count] : next_tick) {
        EXPECT_EQ(count, 20) << "plc_index " << plc;
    }
}

TEST(TestHostHarness, test_trace_dump_order_is_sorted_by_tick_then_plc) {
    const auto harness = run_harness(conveyor_spec(), conveyor_blocks(),
                                     HostHarness::Config{1.0, 100, 100000});
    ASSERT_FALSE(harness->run_error().has_value());
    std::ostringstream out;
    const auto dumped = harness->trace().dump_to_jsonl(out, harness->signal_table(),
                                                       harness->plc_ids());
    ASSERT_TRUE(dumped.has_value());

    std::istringstream lines(out.str());
    std::string line;
    std::int64_t prior_tick = -1;
    std::string prior_plc;
    std::size_t count = 0;
    while (std::getline(lines, line)) {
        const nlohmann::json record = nlohmann::json::parse(line);
        const std::int64_t tick = record["tick"].get<std::int64_t>();
        const std::string plc = record["plc_id"].get<std::string>();
        ASSERT_GE(tick, prior_tick) << "dump order must sort by tick";
        if (tick == prior_tick) {
            ASSERT_GT(plc, prior_plc)
                << "dump order must break tick ties by plc_ids order";
        }
        prior_tick = tick;
        prior_plc = plc;
        ++count;
    }
    EXPECT_EQ(count, 200u);
}

TEST(TestHostHarness, test_plant_route_to_stopped_plc_does_not_hang) {
    ResolvedTaskSpec spec = conveyor_spec();
    spec.plant.routes = {
        ResolvedRoute{"sensor_a_exit_triggered", "plc_a", "sensor_a_exit", "level"},
        ResolvedRoute{"part_at_b", "plc_b", "part_at_b", "level"},
    };
    spec.plant.actuators = {};

    const auto harness = run_harness(spec, conveyor_blocks(),
                                     HostHarness::Config{1.0, 400, 100000});
    ASSERT_FALSE(harness->run_error().has_value())
        << "with belt B never enabled the part parks at A's exit and the "
           "level-triggered route fires every plant scan; the run must still "
           "complete rather than park in bus_.send";
    EXPECT_EQ(harness->trace().size(), 800u);
}

TEST(TestHostHarness, test_dead_plc_coroutine_surfaces_error_not_hang) {
    const auto harness = run_harness(
        minimal_two_plc_spec(), {{"plc_a", "x := 1 / 0;"}, {"plc_b", ""}},
        fast_config(20));
    ASSERT_TRUE(harness->run_error().has_value());
    EXPECT_EQ(harness->run_error()->kind, RunErrorKind::ScanFailed);
    EXPECT_EQ(harness->run_error()->plc_index, 0u);
    ASSERT_TRUE(harness->run_error()->scan_error.has_value());
    EXPECT_EQ(harness->run_error()->scan_error->kind,
              ScanErrorKind::EvalDivisionByZero);
}

}  // namespace
}  // namespace relay_host
