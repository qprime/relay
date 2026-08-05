#pragma once

#include <cstdint>
#include <expected>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include <hce.hpp>

#include "relay_host/clock.hpp"
#include "relay_host/comm_bus.hpp"
#include "relay_host/comm_strategy.hpp"
#include "relay_host/plant_adapter.hpp"
#include "relay_host/scan_executor.hpp"
#include "relay_host/spec_loader.hpp"
#include "relay_host/st_validator.hpp"
#include "relay_host/trace.hpp"

namespace relay_host {

struct InitError {
    std::string message;
};

// Blocks the calling non-coroutine thread until the scheduled coroutine
// completes: an hce::awt joins in its destructor, which runs when the
// by-value parameter goes out of scope here.
inline void join_scheduled(hce::awt<void>) {}

enum class RunErrorKind {
    ScanFailed,
    PlcExited,
};

struct RunError {
    RunErrorKind kind;
    std::uint32_t plc_index;
    std::optional<ScanError> scan_error;
};

class HostHarness {
 public:
    struct Config {
        double scan_period_ms;
        std::int64_t max_scans;
        std::size_t trace_capacity;
    };

    [[nodiscard]] static std::expected<std::unique_ptr<HostHarness>, InitError> try_create(
        ResolvedTaskSpec spec,
        std::vector<std::pair<std::string, std::string>> st_blocks, Config cfg);

    HostHarness(const HostHarness&) = delete;
    HostHarness& operator=(const HostHarness&) = delete;

    [[nodiscard]] hce::co<void> run();

    [[nodiscard]] const TraceRing& trace() const noexcept { return trace_; }
    [[nodiscard]] const SignalTable& signal_table() const noexcept { return table_; }
    [[nodiscard]] std::span<const std::string> plc_ids() const noexcept {
        return spec_.plc_ids;
    }
    [[nodiscard]] const std::optional<RunError>& run_error() const noexcept {
        return run_error_;
    }

 private:
    HostHarness(ResolvedTaskSpec spec, Config cfg, SignalTable table,
                std::vector<ValidatedSt> blocks, CommStrategy strategy,
                LocalStubPlant plant, std::size_t trace_capacity);

    ResolvedTaskSpec spec_;
    Config cfg_;
    SignalTable table_;
    std::vector<ValidatedSt> blocks_;
    CommStrategy strategy_;
    LocalStubPlant plant_;
    CommBus bus_;
    TraceRing trace_;
    std::vector<PlcScanState> states_;
    std::vector<hce::chan<SimClock>> clock_chans_;
    hce::chan<ScanDone> done_chan_;
    std::vector<IOImage> latest_outputs_;
    std::vector<IOImage> prior_outputs_;
    std::optional<PlantOutputs> prior_plant_out_;
    SimClock clock_ = SimClock::zero();
    std::optional<RunError> run_error_;
};

}  // namespace relay_host
