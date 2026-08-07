#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "relay_host/async.hpp"
#include "relay_host/clock.hpp"
#include "relay_host/comm_bus.hpp"
#include "relay_host/comm_strategy.hpp"
#include "relay_host/io_image.hpp"
#include "relay_host/signal_table.hpp"
#include "relay_host/st_eval.hpp"
#include "relay_host/st_validator.hpp"
#include "relay_host/trace.hpp"

namespace relay_host {

struct OutgoingMessage {
    std::uint32_t target_plc;
    Message msg;
};

struct OutgoingBuffer {
    std::array<OutgoingMessage, kMaxCellsPerScan> items;
    std::uint32_t count = 0;
};

struct PlcScanState {
    PlcScanState(std::uint32_t plc_index_arg, const ValidatedSt* block_arg,
                 std::uint32_t signal_count);

    std::uint32_t plc_index;
    const ValidatedSt* block;
    STContext ctx;
    std::vector<std::optional<Cell>> io_cells;
    std::array<CellSlot, kMaxCellsPerScan> outputs;
    std::uint32_t output_count = 0;
    std::vector<std::int64_t> send_counts;
};

[[nodiscard]] std::optional<ScanError> execute_one_scan(PlcScanState& state,
                                                        const CommBuffer& comm,
                                                        SimClock clock, double dt_ms,
                                                        ScanTraceEntry& entry,
                                                        OutgoingBuffer& outgoing);

enum class RunErrorKind {
    ScanFailed,
    PlantFailed,
};

struct RunError {
    RunErrorKind kind;
    std::uint32_t plc_index;
    std::optional<ScanError> scan_error;
    std::string message;
};

struct RunState {
    std::optional<RunError> error;
    bool stop = false;
    std::uint32_t plcs_done = 0;
};

struct PlcExecutionContext {
    std::uint32_t plc_index;
    std::int64_t max_scans;
    double scan_period_ms;
    CommBus* bus;
    PlcScanState* state;
    TraceRing* trace;
    const SignalTable* table;
    IOImage* latest_output;
    RunState* run;
};

[[nodiscard]] Task run_plc_scan_loop(PlcExecutionContext ctx);

}  // namespace relay_host
