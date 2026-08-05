#pragma once

#include <cstdint>
#include <expected>
#include <map>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include <nlohmann/json.hpp>

#include "relay_host/async.hpp"
#include "relay_host/plant_adapter.hpp"
#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"

namespace relay_host {

class RemoteSocketPlant {
 public:
    [[nodiscard]] static std::expected<RemoteSocketPlant, PlantError> try_create(
        const ResolvedPlant& plant, std::span<const std::string> plc_ids,
        const SignalTable& table, Executor ex);

    [[nodiscard]] asio::awaitable<std::expected<ActuatorState, PlantError>>
    read_actuators(std::span<const IOImage> latest_outputs);
    [[nodiscard]] asio::awaitable<std::expected<PlantOutputs, PlantError>> step(
        double dt_ms, const ActuatorState& actuator_state);
    [[nodiscard]] asio::awaitable<std::expected<std::vector<RoutedPlantSignal>, PlantError>>
    route_to_plcs(PlantOutputs current, std::optional<PlantOutputs> prior);

 private:
    struct State {
        State(asio::ip::tcp::socket socket_arg, std::vector<std::string> plc_ids_arg,
              SignalTable table_arg, double request_timeout_ms_arg);

        asio::ip::tcp::socket socket;
        Channel<std::string> write_queue;
        std::vector<std::string> plc_ids;
        SignalTable table;
        double request_timeout_ms;
        std::map<std::int64_t, Channel<nlohmann::json>*> pending;
        std::int64_t next_id = 1;
        bool pumps_running = false;
        std::optional<std::string> dead;
    };

    explicit RemoteSocketPlant(std::shared_ptr<State> state);

    static void fail(const std::shared_ptr<State>& state, std::string message);
    static Task read_loop(std::shared_ptr<State> state);
    static Task write_loop(std::shared_ptr<State> state);
    [[nodiscard]] asio::awaitable<std::expected<nlohmann::json, PlantError>> call(
        std::string_view method, nlohmann::json params);

    std::shared_ptr<State> state_;
};

static_assert(PlantModel<RemoteSocketPlant>);

}  // namespace relay_host
