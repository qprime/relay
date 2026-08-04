#include <gtest/gtest.h>

#include <map>
#include <set>

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

TEST(TestHostHarness, test_scan_synchrony_assumption_holds) {
    // INTERIM ASSUMPTION GUARD (register row 1): every PLC occupies the same
    // scan index at every tick. Lifting the shared barrier (#14) must fail
    // here first, by name, not as a byte diff against the golden trace.
    const auto harness =
        run_harness(minimal_two_plc_spec(), {{"plc_a", ""}, {"plc_b", ""}},
                    fast_config(20));
    ASSERT_FALSE(harness->run_error().has_value());
    const TraceRing& trace = harness->trace();
    const std::size_t plc_count = harness->plc_ids().size();
    ASSERT_EQ(trace.size(), 20u * plc_count);
    for (std::size_t index = 0; index < trace.size(); index += plc_count) {
        std::set<std::uint32_t> plcs_in_group;
        for (std::size_t offset = 0; offset < plc_count; ++offset) {
            const ScanTraceEntry& entry = trace.at(index + offset);
            EXPECT_EQ(entry.clock.tick, static_cast<std::int64_t>(index / plc_count));
            plcs_in_group.insert(entry.plc_index);
        }
        EXPECT_EQ(plcs_in_group.size(), plc_count);
    }
}

TEST(TestHostHarness, test_barrier_holds_all_plcs_to_same_scan) {
    const auto harness =
        run_harness(minimal_two_plc_spec(), {{"plc_a", ""}, {"plc_b", ""}},
                    fast_config(20));
    std::map<std::uint32_t, std::int64_t> last_tick;
    const TraceRing& trace = harness->trace();
    for (std::size_t index = 0; index < trace.size(); ++index) {
        const ScanTraceEntry& entry = trace.at(index);
        for (const auto& [plc, tick] : last_tick) {
            EXPECT_LE(entry.clock.tick - tick, 1)
                << "plc_index " << entry.plc_index << " reached tick "
                << entry.clock.tick << " while plc_index " << plc << " was at " << tick;
        }
        last_tick[entry.plc_index] = entry.clock.tick;
    }
}

TEST(TestHostHarness, test_comm_routing_happens_after_barrier) {
    ResolvedTaskSpec spec = minimal_two_plc_spec();
    spec.comm.tags = {ResolvedTag{"relayed", "plc_a", {"plc_b"}}};
    const auto harness = run_harness(
        spec, {{"plc_a", "relayed := TRUE;"}, {"plc_b", ""}}, fast_config(4));
    ASSERT_FALSE(harness->run_error().has_value());
    const TraceRing& trace = harness->trace();
    const SignalTable& table = harness->signal_table();
    EXPECT_FALSE(io_value(trace.at(1), table, "relayed").has_value())
        << "strategy-routed value arrived in the same scan; routing must happen "
           "after the barrier";
    ASSERT_TRUE(io_value(trace.at(3), table, "relayed").has_value());
    EXPECT_TRUE(is_truthy(*io_value(trace.at(3), table, "relayed")));
}

TEST(TestHostHarness, test_fb_outgoing_delivery_is_same_scan_in_plc_ids_order) {
    const auto harness = run_harness(
        minimal_two_plc_spec(),
        {{"plc_a", "_send_plc_b_flag := TRUE;"}, {"plc_b", ""}}, fast_config(4));
    ASSERT_FALSE(harness->run_error().has_value());
    const TraceRing& trace = harness->trace();
    const SignalTable& table = harness->signal_table();
    const ScanTraceEntry& plc_b_scan_0 = trace.at(1);
    ASSERT_EQ(plc_b_scan_0.plc_index, 1u);
    ASSERT_EQ(plc_b_scan_0.clock.tick, 0);
    ASSERT_TRUE(io_value(plc_b_scan_0, table, "flag").has_value())
        << "producer earlier in plc_ids must deliver to a later consumer within "
           "the SAME scan";
    EXPECT_TRUE(is_truthy(*io_value(plc_b_scan_0, table, "flag")));
}

TEST(TestHostHarness, test_clock_advances_exactly_once_per_scan) {
    const auto harness =
        run_harness(minimal_two_plc_spec(), {{"plc_a", ""}, {"plc_b", ""}},
                    fast_config(20));
    const TraceRing& trace = harness->trace();
    std::map<std::int64_t, std::size_t> per_tick;
    for (std::size_t index = 0; index < trace.size(); ++index) {
        ++per_tick[trace.at(index).clock.tick];
    }
    ASSERT_EQ(per_tick.size(), 20u);
    for (std::int64_t tick = 0; tick < 20; ++tick) {
        EXPECT_EQ(per_tick[tick], 2u) << "tick " << tick;
    }
}

TEST(TestHostHarness, test_elapsed_ms_is_deterministic_across_runs) {
    std::vector<double> first;
    std::vector<double> second;
    for (std::vector<double>* elapsed : {&first, &second}) {
        const auto harness =
            run_harness(minimal_two_plc_spec(), {{"plc_a", ""}, {"plc_b", ""}},
                        fast_config(20));
        const TraceRing& trace = harness->trace();
        for (std::size_t index = 0; index < trace.size(); ++index) {
            elapsed->push_back(trace.at(index).clock.elapsed_ms);
        }
    }
    EXPECT_EQ(first, second);
}

TEST(TestHostHarness, test_trace_record_order_is_plc_ids_order) {
    const auto harness = run_harness(conveyor_spec(), conveyor_blocks(),
                                     HostHarness::Config{1.0, 100, 100000});
    const TraceRing& trace = harness->trace();
    ASSERT_EQ(trace.size(), 200u);
    for (std::size_t index = 0; index < trace.size(); ++index) {
        EXPECT_EQ(trace.at(index).plc_index, index % 2)
            << "trace entry " << index
            << " out of plc_ids order; scan executors must not append on "
               "completion order";
    }
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
