#pragma once

#include <concepts>
#include <cstdint>
#include <expected>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "relay_host/async.hpp"
#include "relay_host/io_image.hpp"
#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"

namespace relay_host {

class ActuatorState {
 public:
    void set(std::string alias, Cell value);
    [[nodiscard]] std::optional<Cell> get(std::string_view alias) const noexcept;
    [[nodiscard]] std::span<const std::pair<std::string, Cell>> entries() const noexcept {
        return values_;
    }

 private:
    std::vector<std::pair<std::string, Cell>> values_;
};

struct PlantOutputs {
    bool sensor_a_exit_triggered;
    bool part_at_b;
};

struct RoutedPlantSignal {
    std::uint32_t to_plc_index;
    std::uint32_t signal_id;
    Cell value;
};

struct PlantError {
    std::string message;
};

template <typename T>
concept PlantModel = requires(T plant, double dt_ms, const ActuatorState& actuators,
                              std::span<const IOImage> latest_outputs,
                              PlantOutputs outputs, std::optional<PlantOutputs> prior) {
    {
        plant.read_actuators(latest_outputs)
    } -> std::same_as<asio::awaitable<std::expected<ActuatorState, PlantError>>>;
    {
        plant.step(dt_ms, actuators)
    } -> std::same_as<asio::awaitable<std::expected<PlantOutputs, PlantError>>>;
    {
        plant.route_to_plcs(outputs, prior)
    } -> std::same_as<
        asio::awaitable<std::expected<std::vector<RoutedPlantSignal>, PlantError>>>;
};

class LocalStubPlant {
 public:
    [[nodiscard]] static std::expected<LocalStubPlant, PlantError> try_create(
        const ResolvedPlant& plant, std::span<const std::string> plc_ids,
        const SignalTable& table);

    [[nodiscard]] asio::awaitable<std::expected<ActuatorState, PlantError>>
    read_actuators(std::span<const IOImage> latest_outputs) const;
    [[nodiscard]] asio::awaitable<std::expected<PlantOutputs, PlantError>> step(
        double dt_ms, const ActuatorState& actuator_state);
    [[nodiscard]] asio::awaitable<std::expected<std::vector<RoutedPlantSignal>, PlantError>>
    route_to_plcs(PlantOutputs current, std::optional<PlantOutputs> prior);

 private:
    enum class Sensor {
        SensorAExitTriggered,
        PartAtB,
        Unknown,
    };

    enum class TriggerKind {
        Edge,
        Level,
    };

    struct Route {
        Sensor sensor;
        std::uint32_t to_plc_index;
        std::uint32_t signal_id;
        TriggerKind trigger;
    };

    struct Actuator {
        std::uint32_t from_plc_index;
        std::string key;
        std::string alias;
    };

    struct Config {
        double belt_speed_m_per_s;
        double sensor_trigger_threshold_m;
        double actuator_latency_ms;
    };

    Config config_;
    std::vector<Route> routes_;
    std::vector<Actuator> actuators_;

    double part_position_m_ = 0.0;
    bool part_on_belt_a_ = true;
    bool part_on_belt_b_ = false;
    bool belt_a_running_ = true;
    bool belt_b_running_ = false;
    double belt_b_enable_pending_ms_ = 0.0;
};

static_assert(PlantModel<LocalStubPlant>);

}  // namespace relay_host
