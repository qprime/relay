#include <gtest/gtest.h>

#include <array>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <new>
#include <thread>

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

struct FreeRunRig {
    explicit FreeRunRig(ResolvedTaskSpec spec_arg,
                        std::array<const char*, 2> sources = {"", ""})
        : spec(std::move(spec_arg)) {
        std::array<StProgram, 2> programs{*parse_st(sources[0]), *parse_st(sources[1])};
        table = build_signal_table(spec, programs);
        blocks.reserve(2);
        states.reserve(2);
        for (std::uint32_t index = 0; index < 2; ++index) {
            blocks.push_back(
                *ValidatedSt::try_from(std::move(programs[index]), table, spec.plc_ids));
        }
        for (std::uint32_t index = 0; index < 2; ++index) {
            states.emplace_back(index, &blocks[index], table.size());
        }
        bus.emplace(io.get_executor(), 2, table.size(), 64);
    }

    PlcExecutionContext context(std::uint32_t index, std::int64_t max_scans,
                                double period_ms) {
        return PlcExecutionContext{index, max_scans,      period_ms,
                                   &*bus, &states[index], &trace,
                                   &table, &latest[index], &run};
    }

    ResolvedTaskSpec spec;
    SignalTable table;
    std::vector<ValidatedSt> blocks;
    std::vector<PlcScanState> states;
    asio::io_context io;
    std::optional<CommBus> bus;
    TraceRing trace{1000};
    std::array<IOImage, 2> latest{IOImage::empty(), IOImage::empty()};
    RunState run;
};

std::optional<std::size_t> append_position(const TraceRing& trace,
                                           std::uint32_t plc_index,
                                           std::int64_t tick) {
    for (std::size_t index = 0; index < trace.size(); ++index) {
        const ScanTraceEntry& entry = trace.at(index);
        if (entry.plc_index == plc_index && entry.clock.tick == tick) {
            return index;
        }
    }
    return std::nullopt;
}

TEST(TestScanExecutor, test_plcs_reach_different_ticks) {
    FreeRunRig rig(testing::minimal_two_plc_spec());
    asio::co_spawn(rig.io, run_plc_scan_loop(rig.context(0, 40, 1.0)), asio::detached);
    asio::co_spawn(rig.io, run_plc_scan_loop(rig.context(1, 4, 40.0)), asio::detached);
    rig.io.run();

    const auto fast_last = append_position(rig.trace, 0, 39);
    const auto slow_late = append_position(rig.trace, 1, 2);
    ASSERT_TRUE(fast_last.has_value()) << "fast PLC never reached tick 39";
    ASSERT_TRUE(slow_late.has_value()) << "slow PLC never reached tick 2";
    EXPECT_LT(*fast_last, *slow_late)
        << "independently paced PLCs must not be held to a shared scan index; a "
           "reintroduced barrier would force them to advance together";
}

TEST(TestScanExecutor, test_send_to_exited_plc_drops_instead_of_hanging) {
    ResolvedTaskSpec spec = testing::minimal_two_plc_spec();
    FreeRunRig rig(spec, {"_send_plc_b_flag := TRUE;", ""});

    const std::int64_t sender_scans = 300;
    asio::co_spawn(rig.io, run_plc_scan_loop(rig.context(0, sender_scans, 1.0)),
                   asio::detached);
    asio::co_spawn(rig.io, run_plc_scan_loop(rig.context(1, 2, 1.0)), asio::detached);
    rig.io.run();

    ASSERT_TRUE(append_position(rig.trace, 0, sender_scans - 1).has_value())
        << "the sender must run its full scan budget; before the fix it parked in "
           "bus->send once plc_b's 64-slot channel filled and never woke";
    EXPECT_FALSE(rig.bus->receiver_open(1));
    EXPECT_GT(rig.bus->dropped(1), 0)
        << "messages sent to an exited PLC must be counted, not silently lost";
    EXPECT_EQ(rig.bus->dropped(0), 0);
}

TEST(TestScanExecutor, test_scan_overrun_does_not_accumulate_drift) {
    FreeRunRig rig(testing::minimal_two_plc_spec());

    auto stall = [&]() -> asio::awaitable<void> {
        asio::steady_timer timer(co_await asio::this_coro::executor);
        timer.expires_after(std::chrono::milliseconds(35));
        co_await timer.async_wait(asio::use_awaitable);
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    };

    asio::co_spawn(rig.io, run_plc_scan_loop(rig.context(0, 30, 10.0)), asio::detached);
    asio::co_spawn(rig.io, stall(), asio::detached);
    const auto start = std::chrono::steady_clock::now();
    rig.io.run();
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start);

    for (std::int64_t tick = 0; tick < 30; ++tick) {
        EXPECT_TRUE(append_position(rig.trace, 0, tick).has_value())
            << "an overrun must run late, not skip tick " << tick;
    }
    EXPECT_LT(elapsed.count(), 380)
        << "deadlines are absolute multiples of the period from loop start; a "
           "100ms stall must be absorbed by catch-up, not appended to every "
           "subsequent scan";
    EXPECT_GT(elapsed.count(), 280);
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
    comm.set(*table.find_id("sensor_a_exit"), Cell{true}, kNoSender, 1);
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
