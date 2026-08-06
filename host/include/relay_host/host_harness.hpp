#pragma once

#include <cstdint>
#include <expected>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "relay_host/async.hpp"
#include "relay_host/clock.hpp"
#include "relay_host/comm_bus.hpp"
#include "relay_host/comm_strategy.hpp"
#include "relay_host/plant_registry.hpp"
#include "relay_host/scan_executor.hpp"
#include "relay_host/spec_loader.hpp"
#include "relay_host/st_validator.hpp"
#include "relay_host/trace.hpp"

namespace relay_host {

struct InitError {
    std::string message;
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
        std::vector<std::pair<std::string, std::string>> st_blocks, Config cfg,
        Executor ex);

    HostHarness(const HostHarness&) = delete;
    HostHarness& operator=(const HostHarness&) = delete;

    [[nodiscard]] Task run();

    [[nodiscard]] const TraceRing& trace() const noexcept { return trace_; }
    [[nodiscard]] const SignalTable& signal_table() const noexcept { return table_; }
    [[nodiscard]] std::span<const std::string> plc_ids() const noexcept {
        return spec_.plc_ids;
    }
    [[nodiscard]] const std::optional<RunError>& run_error() const noexcept {
        return run_error_;
    }
    [[nodiscard]] std::int64_t dropped_sends(std::uint32_t plc_index) const noexcept {
        return bus_.dropped(plc_index);
    }
    [[nodiscard]] std::int64_t dropped_sends_total() const noexcept {
        return bus_.dropped_total();
    }

 private:
    HostHarness(ResolvedTaskSpec spec, Config cfg, SignalTable table,
                std::vector<ValidatedSt> blocks, CommStrategy strategy,
                PlantVariant plant, std::size_t trace_capacity, Executor ex);

    [[nodiscard]] Task run_plant_loop();

    ResolvedTaskSpec spec_;
    Config cfg_;
    SignalTable table_;
    std::vector<ValidatedSt> blocks_;
    CommStrategy strategy_;
    PlantVariant plant_;
    CommBus bus_;
    TraceRing trace_;
    std::vector<PlcScanState> states_;
    std::vector<IOImage> latest_outputs_;
    std::optional<PlantOutputs> prior_plant_out_;
    std::vector<std::int64_t> plant_send_counts_;
    RunState run_state_;
    std::optional<RunError> run_error_;
};

}  // namespace relay_host
