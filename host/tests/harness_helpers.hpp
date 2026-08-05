#pragma once

#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "relay_host/async.hpp"
#include "relay_host/host_harness.hpp"
#include "relay_host/spec_loader.hpp"

namespace relay_host::testing {

inline constexpr const char* kConveyorPlcA = R"((* trigger: handoff_on_exit *)
_scratch_edge_handoff_on_exit := sensor_a_exit AND NOT _scratch_prev_handoff_on_exit;
_scratch_prev_handoff_on_exit := sensor_a_exit;
IF _scratch_edge_handoff_on_exit THEN
_scratch_latched_handoff_on_exit := TRUE;
END_IF;
_send_plc_b_handoff_signal := _scratch_latched_handoff_on_exit;)";

inline constexpr const char* kConveyorPlcB = R"((* trigger: belt_on_handoff *)
IF handoff_signal THEN
_scratch_latched_belt_on_handoff := TRUE;
END_IF;
belt_b_enable := _scratch_latched_belt_on_handoff;)";

inline ResolvedTaskSpec conveyor_spec() {
    ResolvedTaskSpec spec;
    spec.system_name = "conveyor_handoff";
    spec.plc_ids = {"plc_a", "plc_b"};
    spec.scan_period_ms = 10.0;
    spec.max_scans = 100;
    spec.comm.strategy = "tag";
    spec.comm.tags = {ResolvedTag{"handoff_signal", "plc_a", {"plc_b"}}};
    spec.plant.type = "conveyor";
    spec.plant.config = ResolvedPlantConfig{0.5, 0.1, 50.0};
    spec.plant.routes = {
        ResolvedRoute{"sensor_a_exit_triggered", "plc_a", "sensor_a_exit", "edge"},
        ResolvedRoute{"part_at_b", "plc_b", "part_at_b", "level"},
    };
    spec.plant.actuators = {
        ResolvedActuator{"plc_b", "belt_b_enable", "belt_b_enable_signal"}};
    spec.assertions = {"EVENTUALLY(part_at_b, within: 500ms)",
                       "PRECEDES(handoff_signal, belt_b_enable, within: 500ms)"};
    return spec;
}

inline std::vector<std::pair<std::string, std::string>> conveyor_blocks() {
    return {{"plc_a", kConveyorPlcA}, {"plc_b", kConveyorPlcB}};
}

inline ResolvedTaskSpec minimal_two_plc_spec() {
    ResolvedTaskSpec spec;
    spec.system_name = "minimal";
    spec.plc_ids = {"plc_a", "plc_b"};
    spec.scan_period_ms = 1.0;
    spec.max_scans = 20;
    spec.comm.strategy = "tag";
    spec.plant.type = "conveyor";
    spec.plant.config = ResolvedPlantConfig{0.5, 0.1, 50.0};
    return spec;
}

struct HarnessRun {
    std::unique_ptr<asio::io_context> io;
    std::unique_ptr<HostHarness> harness;

    HostHarness* operator->() const noexcept { return harness.get(); }
};

inline HarnessRun run_harness(ResolvedTaskSpec spec,
                              std::vector<std::pair<std::string, std::string>> blocks,
                              HostHarness::Config cfg) {
    auto io = std::make_unique<asio::io_context>();
    auto harness = HostHarness::try_create(std::move(spec), std::move(blocks), cfg,
                                           io->get_executor());
    if (!harness) {
        throw std::runtime_error("harness_helpers: try_create failed: " +
                                 harness.error().message);
    }
    asio::co_spawn(*io, (*harness)->run(), asio::detached);
    io->run();
    return HarnessRun{std::move(io), std::move(*harness)};
}

}  // namespace relay_host::testing
