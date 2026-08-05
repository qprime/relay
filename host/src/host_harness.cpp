#include "relay_host/host_harness.hpp"

#include <algorithm>
#include <chrono>

#include <asio/experimental/promise.hpp>
#include <asio/experimental/use_promise.hpp>

#include "relay_host/st_parser.hpp"

namespace relay_host {

namespace {

constexpr int kCommChannelCapacity = 64;

}  // namespace

std::expected<std::unique_ptr<HostHarness>, InitError> HostHarness::try_create(
    ResolvedTaskSpec spec, std::vector<std::pair<std::string, std::string>> st_blocks,
    Config cfg, Executor ex) {
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

    auto plant = build_plant(spec.plant, spec.plc_ids, table, ex);
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
                        std::move(*strategy), std::move(*plant), capacity,
                        std::move(ex)));
}

HostHarness::HostHarness(ResolvedTaskSpec spec, Config cfg, SignalTable table,
                         std::vector<ValidatedSt> blocks, CommStrategy strategy,
                         PlantVariant plant, std::size_t trace_capacity, Executor ex)
    : spec_(std::move(spec)),
      cfg_(cfg),
      table_(std::move(table)),
      blocks_(std::move(blocks)),
      strategy_(std::move(strategy)),
      plant_(std::move(plant)),
      bus_(ex, static_cast<std::uint32_t>(spec_.plc_ids.size()), table_.size(),
           kCommChannelCapacity),
      trace_(trace_capacity),
      plant_send_counts_(table_.size(), 0) {
    const std::uint32_t plc_count = static_cast<std::uint32_t>(spec_.plc_ids.size());
    states_.reserve(plc_count);
    for (std::uint32_t index = 0; index < plc_count; ++index) {
        states_.emplace_back(index, &blocks_[index], table_.size());
        latest_outputs_.push_back(IOImage::empty());
    }
}

Task HostHarness::run_plant_loop() {
    const std::uint32_t plc_count = static_cast<std::uint32_t>(spec_.plc_ids.size());
    asio::steady_timer timer(co_await asio::this_coro::executor);
    const auto period = std::chrono::duration_cast<asio::steady_timer::duration>(
        std::chrono::duration<double, std::milli>(cfg_.scan_period_ms));
    auto deadline = asio::steady_timer::clock_type::now();

    for (std::int64_t scan = 0;
         scan < cfg_.max_scans && !run_state_.stop && run_state_.plcs_done < plc_count;
         ++scan) {
        deadline += period;
        timer.expires_at(deadline);
        const auto [wait_ec] =
            co_await timer.async_wait(asio::as_tuple(asio::use_awaitable));
        if (wait_ec || run_state_.stop || run_state_.plcs_done == plc_count) {
            break;
        }

        const auto actuator_state = co_await std::visit(
            [&](auto& plant) { return plant.read_actuators(latest_outputs_); }, plant_);
        if (!actuator_state) {
            run_state_.error = RunError{RunErrorKind::PlantFailed, 0, std::nullopt,
                                        actuator_state.error().message};
            run_state_.stop = true;
            break;
        }
        const auto plant_out = co_await std::visit(
            [&](auto& plant) { return plant.step(cfg_.scan_period_ms, *actuator_state); },
            plant_);
        if (!plant_out) {
            run_state_.error = RunError{RunErrorKind::PlantFailed, 0, std::nullopt,
                                        plant_out.error().message};
            run_state_.stop = true;
            break;
        }
        const auto plant_routed = co_await std::visit(
            [&](auto& plant) { return plant.route_to_plcs(*plant_out, prior_plant_out_); },
            plant_);
        if (!plant_routed) {
            run_state_.error = RunError{RunErrorKind::PlantFailed, 0, std::nullopt,
                                        plant_routed.error().message};
            run_state_.stop = true;
            break;
        }
        for (const RoutedPlantSignal& routed : *plant_routed) {
            const std::int64_t seq = ++plant_send_counts_[routed.signal_id];
            co_await bus_.send(
                routed.to_plc_index,
                Message{routed.signal_id, routed.value, kNoSender, seq});
        }

        prior_plant_out_ = *plant_out;
    }
    co_return;
}

Task HostHarness::run() {
    const Executor ex = co_await asio::this_coro::executor;
    const std::uint32_t plc_count = static_cast<std::uint32_t>(spec_.plc_ids.size());
    std::vector<asio::experimental::promise<void(std::exception_ptr)>> joins;
    joins.reserve(plc_count + 1);
    for (std::uint32_t index = 0; index < plc_count; ++index) {
        joins.push_back(asio::co_spawn(
            ex,
            run_plc_scan_loop(PlcExecutionContext{
                index, cfg_.max_scans, cfg_.scan_period_ms, &bus_, &states_[index],
                &trace_, &table_, &strategy_, &latest_outputs_[index], &run_state_}),
            asio::experimental::use_promise));
    }
    joins.push_back(asio::co_spawn(ex, run_plant_loop(), asio::experimental::use_promise));

    for (auto& join : joins) {
        co_await join(asio::use_awaitable);
    }
    bus_.close();
    run_error_ = run_state_.error;
    co_return;
}

}  // namespace relay_host
