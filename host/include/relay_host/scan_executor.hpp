#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <vector>

#include <hce.hpp>

#include "relay_host/clock.hpp"
#include "relay_host/comm_bus.hpp"
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
};

[[nodiscard]] std::optional<ScanError> execute_one_scan(PlcScanState& state,
                                                        const CommBuffer& comm,
                                                        SimClock clock, double dt_ms,
                                                        ScanTraceEntry& entry,
                                                        OutgoingBuffer& outgoing);

struct ScanDone {
    std::uint32_t plc_index;
    bool ok;
    std::optional<ScanError> error;
};

struct PlcExecutionContext {
    std::uint32_t plc_index;
    std::int64_t max_scans;
    double scan_period_ms;
    hce::chan<SimClock> clock_chan;
    hce::chan<ScanDone> done_chan;
    CommBus* bus;
    PlcScanState* state;
    TraceRing* trace;
};

[[nodiscard]] hce::co<void> run_plc_scan_loop(PlcExecutionContext ctx);

}  // namespace relay_host
