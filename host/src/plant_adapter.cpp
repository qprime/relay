#include "relay_host/plant_adapter.hpp"

#include <algorithm>
#include <cmath>

namespace relay_host {

namespace {

constexpr double kBeltALengthM = 0.15;
constexpr double kBeltBStartM = 0.15;
constexpr double kBeltAStopOffsetM = 0.001;

std::optional<std::uint32_t> index_of(std::span<const std::string> plc_ids,
                                      const std::string& plc_id) {
    const auto it = std::find(plc_ids.begin(), plc_ids.end(), plc_id);
    if (it == plc_ids.end()) {
        return std::nullopt;
    }
    return static_cast<std::uint32_t>(it - plc_ids.begin());
}

std::expected<double, PlantError> require_config_number(const nlohmann::json& config,
                                                        const std::string& key) {
    if (!config.contains(key) || !config[key].is_number()) {
        return std::unexpected(PlantError{
            "plant_adapter: conveyor config field '" + key + "' must be a number, got " +
            (config.contains(key) ? std::string(config[key].type_name())
                                  : std::string("nothing"))});
    }
    return config[key].get<double>();
}

}  // namespace

void ActuatorState::set(std::string alias, Cell value) {
    for (auto& [existing, cell] : values_) {
        if (existing == alias) {
            cell = value;
            return;
        }
    }
    values_.emplace_back(std::move(alias), value);
}

std::optional<Cell> ActuatorState::get(std::string_view alias) const noexcept {
    for (const auto& [existing, cell] : values_) {
        if (existing == alias) {
            return cell;
        }
    }
    return std::nullopt;
}

std::expected<LocalStubPlant, PlantError> LocalStubPlant::try_create(
    const ResolvedPlant& plant, std::span<const std::string> plc_ids,
    const SignalTable& table) {
    LocalStubPlant stub;
    auto belt_speed = require_config_number(plant.config, "belt_speed_m_per_s");
    if (!belt_speed) return std::unexpected(belt_speed.error());
    auto threshold = require_config_number(plant.config, "sensor_trigger_threshold_m");
    if (!threshold) return std::unexpected(threshold.error());
    auto latency = require_config_number(plant.config, "actuator_latency_ms");
    if (!latency) return std::unexpected(latency.error());
    stub.config_ = Config{*belt_speed, *threshold, *latency};
    for (const ResolvedRoute& route : plant.routes) {
        Sensor sensor;
        if (route.sensor == "sensor_a_exit_triggered") {
            sensor = Sensor::SensorAExitTriggered;
        } else if (route.sensor == "part_at_b") {
            sensor = Sensor::PartAtB;
        } else {
            return std::unexpected(PlantError{
                "plant_adapter: route sensor '" + route.sensor +
                "' is not a conveyor sensor; known: part_at_b, sensor_a_exit_triggered"});
        }
        const auto to_plc = index_of(plc_ids, route.to_plc);
        if (!to_plc) {
            return std::unexpected(PlantError{"plant_adapter: route sensor '" +
                                              route.sensor + "' targets '" + route.to_plc +
                                              "', which is not a declared plc_id"});
        }
        const auto signal_id = table.find_id(route.as_key);
        if (!signal_id) {
            return std::unexpected(PlantError{
                "plant_adapter: route as_key '" + route.as_key +
                "' is not in the signal table; route keys must be registered at startup"});
        }
        stub.routes_.push_back(Route{
            sensor, *to_plc, *signal_id,
            route.trigger == "edge" ? TriggerKind::Edge : TriggerKind::Level});
    }
    for (const ResolvedActuator& actuator : plant.actuators) {
        const auto from_plc = index_of(plc_ids, actuator.from_plc);
        if (!from_plc) {
            return std::unexpected(PlantError{
                "plant_adapter: actuator key '" + actuator.key + "' reads from '" +
                actuator.from_plc + "', which is not a declared plc_id"});
        }
        stub.actuators_.push_back(Actuator{*from_plc, actuator.key, actuator.as});
    }
    return stub;
}

asio::awaitable<std::expected<ActuatorState, PlantError>> LocalStubPlant::read_actuators(
    std::span<const IOImage> latest_outputs) const {
    ActuatorState state;
    for (const Actuator& actuator : actuators_) {
        const std::optional<Cell> value =
            latest_outputs[actuator.from_plc_index].get(actuator.key);
        state.set(actuator.alias, value.value_or(Cell{false}));
    }
    co_return state;
}

asio::awaitable<std::expected<PlantOutputs, PlantError>> LocalStubPlant::step(
    double dt_ms, const ActuatorState& actuator_state) {
    const double dt_s = dt_ms / 1000.0;
    const bool belt_b_enable_signal =
        is_truthy(actuator_state.get("belt_b_enable_signal").value_or(Cell{false}));

    double pending = belt_b_enable_pending_ms_;
    bool belt_b_running = false;
    if (belt_b_running_) {
        belt_b_running = true;
    } else if (pending > 0.0) {
        pending = std::max(0.0, pending - dt_ms);
        belt_b_running = pending == 0.0;
    } else if (belt_b_enable_signal) {
        pending = config_.actuator_latency_ms;
        belt_b_running = false;
    }

    double new_pos = part_position_m_;
    if (belt_a_running_ && part_on_belt_a_) {
        double advance = config_.belt_speed_m_per_s * dt_s;
        if (!belt_b_running) {
            advance = std::min(
                advance,
                std::max(0.0, kBeltALengthM - part_position_m_ - kBeltAStopOffsetM));
        }
        new_pos += advance;
    }
    if (belt_b_running && part_on_belt_b_) {
        new_pos += config_.belt_speed_m_per_s * dt_s;
    }

    const bool on_belt_a = new_pos < kBeltALengthM;
    const bool on_belt_b = new_pos >= kBeltBStartM;

    part_position_m_ = new_pos;
    part_on_belt_a_ = on_belt_a;
    part_on_belt_b_ = on_belt_b;
    belt_b_running_ = belt_b_running;
    belt_b_enable_pending_ms_ = pending;

    const bool sensor_a_exit =
        std::abs(new_pos - kBeltALengthM) < config_.sensor_trigger_threshold_m;

    co_return PlantOutputs{sensor_a_exit, on_belt_b};
}

asio::awaitable<std::expected<std::vector<RoutedPlantSignal>, PlantError>>
LocalStubPlant::route_to_plcs(PlantOutputs current, std::optional<PlantOutputs> prior) {
    std::vector<RoutedPlantSignal> routed;
    routed.reserve(routes_.size());
    for (const Route& route : routes_) {
        bool current_value = false;
        bool prior_value = false;
        switch (route.sensor) {
            case Sensor::SensorAExitTriggered:
                current_value = current.sensor_a_exit_triggered;
                prior_value = prior.has_value() && prior->sensor_a_exit_triggered;
                break;
            case Sensor::PartAtB:
                current_value = current.part_at_b;
                prior_value = prior.has_value() && prior->part_at_b;
                break;
        }
        const bool emit = route.trigger == TriggerKind::Level
                              ? current_value
                              : current_value && !prior_value;
        if (emit) {
            routed.push_back(RoutedPlantSignal{route.to_plc_index, route.signal_id,
                                               Cell{current_value}});
        }
    }
    co_return routed;
}

}  // namespace relay_host
