#include <gtest/gtest.h>

#include <atomic>
#include <cstdlib>
#include <new>

#include "harness_helpers.hpp"
#include "relay_host/scan_executor.hpp"
#include "relay_host/st_parser.hpp"

namespace {

std::atomic<bool> g_count_allocations{false};
std::atomic<std::size_t> g_allocation_count{0};

void note_allocation() {
    if (g_count_allocations.load(std::memory_order_relaxed)) {
        g_allocation_count.fetch_add(1, std::memory_order_relaxed);
    }
}

}  // namespace

// GCC pairs the inlined malloc in this counting operator new with the free in
// operator delete and reports a spurious mismatch; the pair is consistent.
#pragma GCC diagnostic ignored "-Wmismatched-new-delete"

void* operator new(std::size_t size) {
    note_allocation();
    void* ptr = std::malloc(size == 0 ? 1 : size);
    if (ptr == nullptr) {
        throw std::bad_alloc();
    }
    return ptr;
}

void* operator new[](std::size_t size) {
    note_allocation();
    void* ptr = std::malloc(size == 0 ? 1 : size);
    if (ptr == nullptr) {
        throw std::bad_alloc();
    }
    return ptr;
}

void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete[](void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }
void operator delete[](void* ptr, std::size_t) noexcept { std::free(ptr); }

namespace relay_host {
namespace {

using testing::conveyor_blocks;
using testing::conveyor_spec;
using testing::run_harness;

TEST(TestScanExecutor, test_conveyor_runs_to_completion) {
    const auto harness = run_harness(conveyor_spec(), conveyor_blocks(),
                                     HostHarness::Config{10.0, 100, 100000});
    ASSERT_FALSE(harness->run_error().has_value());
    const TraceRing& trace = harness->trace();
    ASSERT_EQ(trace.size(), 200u);
    const SignalTable& table = harness->signal_table();
    bool part_arrived = false;
    for (std::size_t index = 0; index < trace.size() && !part_arrived; ++index) {
        const ScanTraceEntry& entry = trace.at(index);
        for (std::uint32_t cell = 0; cell < entry.input_count; ++cell) {
            if (table.name_of(entry.input_cells[cell].signal_id) == "part_at_b" &&
                is_truthy(entry.input_cells[cell].value)) {
                part_arrived = true;
            }
        }
    }
    EXPECT_TRUE(part_arrived);
}

TEST(TestScanExecutor, test_no_allocation_in_scan_body) {
    const ResolvedTaskSpec spec = conveyor_spec();
    auto program = parse_st(testing::kConveyorPlcA);
    ASSERT_TRUE(program.has_value());
    SignalTable table = build_signal_table(spec, std::span(&*program, 1));
    auto block = ValidatedSt::try_from(std::move(*program), table, spec.plc_ids);
    ASSERT_TRUE(block.has_value());

    PlcScanState state(0, &*block, table.size());
    CommBuffer comm(table.size());
    comm.set(*table.find_id("sensor_a_exit"), Cell{true}, 1);
    ScanTraceEntry entry{};
    OutgoingBuffer outgoing;
    SimClock clock = SimClock::zero();

    ASSERT_FALSE(
        execute_one_scan(state, comm, clock, 10.0, entry, outgoing).has_value());

    g_allocation_count.store(0);
    g_count_allocations.store(true);
    for (int scan = 0; scan < 10; ++scan) {
        clock = clock.advance(10.0);
        const auto error = execute_one_scan(state, comm, clock, 10.0, entry, outgoing);
        EXPECT_FALSE(error.has_value());
    }
    g_count_allocations.store(false);
    EXPECT_EQ(g_allocation_count.load(), 0u)
        << "execute_one_scan allocated in the scan body";
}

}  // namespace
}  // namespace relay_host
