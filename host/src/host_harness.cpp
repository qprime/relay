#include "relay_host/host_harness.hpp"

#include <algorithm>
#include <chrono>

#include "relay_host/st_parser.hpp"

namespace relay_host {

namespace {

constexpr int kCommChannelCapacity = 64;

}  // namespace

std::expected<std::unique_ptr<HostHarness>, InitError> HostHarness::try_create(
    ResolvedTaskSpec spec, std::vector<std::pair<std::string, std::string>> st_blocks,
    Config cfg) {
    if (st_blocks.size() != spec.plc_ids.size()) {
        return std::unexpected(InitError{
            "host_harness: st_blocks count " + std::to_string(st_blocks.size()) +
            " does not match plc_ids count " + std::to_string(spec.plc_ids.size())});
    }
    for (std::size_t index = 0; index < spec.plc_ids.size(); ++index) {
        if (st_blocks[index].first != spec.plc_ids[index]) {
            return std::unexpected(InitError{
                "host_harness: st_blocks[" + std::to_string(index) + "] is for '" +
                st_blocks[index].first + "', expected plc_id '" + spec.plc_ids[index] +
                "'"});
        }
    }
    if (cfg.scan_period_ms <= 0.0) {
        return std::unexpected(InitError{
            "host_harness: Config.scan_period_ms must be > 0, got " +
            std::to_string(cfg.scan_period_ms)});
    }
    if (cfg.max_scans < 1) {
        return std::unexpected(InitError{"host_harness: Config.max_scans must be >= 1, got " +
                                         std::to_string(cfg.max_scans)});
    }

    std::vector<StProgram> programs;
    programs.reserve(st_blocks.size());
    for (const auto& [plc_id, source] : st_blocks) {
        auto program = parse_st(source);
        if (!program) {
            return std::unexpected(InitError{
                "host_harness: ST for '" + plc_id + "' failed to parse at line " +
                std::to_string(program.error().line) + ": " + program.error().message});
        }
        programs.push_back(std::move(*program));
    }

    SignalTable table = build_signal_table(spec, programs);

    std::vector<ValidatedSt> blocks;
    blocks.reserve(programs.size());
    for (std::size_t index = 0; index < programs.size(); ++index) {
        auto validated =
            ValidatedSt::try_from(std::move(programs[index]), table, spec.plc_ids);
        if (!validated) {
            return std::unexpected(InitError{
                "host_harness: ST for '" + spec.plc_ids[index] +
                "' failed validation: " + validated.error().message});
        }
        blocks.push_back(std::move(*validated));
    }

    auto strategy = build_comm_strategy(spec, table);
    if (!strategy) {
        return std::unexpected(InitError{strategy.error().message});
    }

    auto plant = LocalStubPlant::try_create(spec.plant, spec.plc_ids, table);
    if (!plant) {
        return std::unexpected(InitError{plant.error().message});
    }

    const std::size_t needed =
        static_cast<std::size_t>(cfg.max_scans) * spec.plc_ids.size();
    const std::size_t capacity = std::min(cfg.trace_capacity, needed);
    if (capacity == 0) {
        return std::unexpected(InitError{"host_harness: Config.trace_capacity must be > 0"});
    }

    return std::unique_ptr<HostHarness>(
        new HostHarness(std::move(spec), cfg, std::move(table), std::move(blocks),
                        std::move(*strategy), std::move(*plant), capacity));
}

HostHarness::HostHarness(ResolvedTaskSpec spec, Config cfg, SignalTable table,
                         std::vector<ValidatedSt> blocks, CommStrategy strategy,
                         LocalStubPlant plant, std::size_t trace_capacity)
    : spec_(std::move(spec)),
      cfg_(cfg),
      table_(std::move(table)),
      blocks_(std::move(blocks)),
      strategy_(std::move(strategy)),
      plant_(std::move(plant)),
      bus_(static_cast<std::uint32_t>(spec_.plc_ids.size()), table_.size(),
           kCommChannelCapacity),
      trace_(trace_capacity),
      done_chan_(hce::chan<ScanDone>::make(kCommChannelCapacity)) {
    const std::uint32_t plc_count = static_cast<std::uint32_t>(spec_.plc_ids.size());
    states_.reserve(plc_count);
    clock_chans_.reserve(plc_count);
    for (std::uint32_t index = 0; index < plc_count; ++index) {
        states_.emplace_back(index, &blocks_[index], table_.size());
        clock_chans_.push_back(hce::chan<SimClock>::make(1));
        latest_outputs_.push_back(IOImage::empty());
        prior_outputs_.push_back(IOImage::empty());
    }
}

hce::co<void> HostHarness::run() {
    const std::uint32_t plc_count = static_cast<std::uint32_t>(spec_.plc_ids.size());
    std::vector<hce::awt<void>> joins;
    joins.reserve(plc_count);
    for (std::uint32_t index = 0; index < plc_count; ++index) {
        joins.push_back(hce::schedule(run_plc_scan_loop(PlcExecutionContext{
            index, cfg_.max_scans, cfg_.scan_period_ms, clock_chans_[index], done_chan_,
            &bus_, &states_[index], &trace_})));
    }

    const auto scan_period = std::chrono::duration_cast<hce::chrono::duration>(
        std::chrono::duration<double, std::milli>(cfg_.scan_period_ms));

    bool aborted = false;
    for (std::int64_t scan = 0; scan < cfg_.max_scans && !aborted; ++scan) {
        co_await hce::sleep(scan_period);

        const ActuatorState actuator_state = plant_.read_actuators(latest_outputs_);
        const PlantOutputs plant_out = plant_.step(cfg_.scan_period_ms, actuator_state);
        for (const RoutedPlantSignal& routed :
             plant_.route_to_plcs(plant_out, prior_plant_out_)) {
            co_await bus_.send(routed.to_plc_index,
                               Message{routed.signal_id, routed.value});
        }

        for (std::uint32_t index = 0; index < plc_count && !aborted; ++index) {
            co_await clock_chans_[index].send(clock_);
            ScanDone done{};
            if (!co_await done_chan_.recv(done)) {
                run_error_ = RunError{RunErrorKind::PlcExited, index, std::nullopt};
                aborted = true;
                break;
            }
            if (!done.ok) {
                run_error_ =
                    RunError{RunErrorKind::ScanFailed, done.plc_index, done.error};
                aborted = true;
                break;
            }
            IOImage outputs = IOImage::empty();
            const PlcScanState& state = states_[done.plc_index];
            for (std::uint32_t cell = 0; cell < state.output_count; ++cell) {
                outputs = outputs.with_value(table_.name_of(state.outputs[cell].signal_id),
                                             state.outputs[cell].value);
            }
            latest_outputs_[done.plc_index] = std::move(outputs);
        }
        if (aborted) {
            break;
        }

        for (std::uint32_t index = 0; index < plc_count; ++index) {
            const std::span<const Routed> routed = std::visit(
                [&](auto& strategy) {
                    return strategy.route(index, latest_outputs_[index],
                                          prior_outputs_[index]);
                },
                strategy_);
            for (const Routed& message : routed) {
                co_await bus_.send(message.consumer_index,
                                   Message{message.signal_id, message.value});
            }
        }

        prior_outputs_ = latest_outputs_;
        prior_plant_out_ = plant_out;
        clock_ = clock_.advance(cfg_.scan_period_ms);
    }

    for (hce::chan<SimClock>& channel : clock_chans_) {
        channel.close();
    }
    bus_.close();
    for (hce::awt<void>& join : joins) {
        co_await std::move(join);
    }
    done_chan_.close();
    co_return;
}

}  // namespace relay_host
