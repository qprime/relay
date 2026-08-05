#include "relay_host/remote_socket_plant.hpp"

#include <algorithm>
#include <chrono>
#include <utility>

#include <asio/experimental/awaitable_operators.hpp>

namespace relay_host {

namespace {

using nlohmann::json;

constexpr int kWriteQueueCapacity = 64;
constexpr double kDefaultRequestTimeoutMs = 1000.0;

json cell_to_json(const Cell& cell) {
    return std::visit([](const auto& value) { return json(value); }, cell);
}

std::optional<Cell> cell_from_json(const json& value) {
    if (value.is_boolean()) {
        return Cell{value.get<bool>()};
    }
    if (value.is_number_integer()) {
        return Cell{value.get<std::int64_t>()};
    }
    if (value.is_number()) {
        return Cell{value.get<double>()};
    }
    return std::nullopt;
}

json outputs_to_json(const PlantOutputs& outputs) {
    return json{{"sensor_a_exit_triggered", outputs.sensor_a_exit_triggered},
                {"part_at_b", outputs.part_at_b}};
}

std::unexpected<PlantError> protocol_error(const std::string& detail) {
    return std::unexpected(PlantError{"remote_socket: " + detail});
}

json images_to_json(std::span<const IOImage> latest_outputs,
                    std::span<const std::string> plc_ids) {
    json images = json::object();
    const std::size_t count = std::min(latest_outputs.size(), plc_ids.size());
    for (std::size_t index = 0; index < count; ++index) {
        json image = json::object();
        for (const auto& [key, value] : latest_outputs[index].values()) {
            image[key] = cell_to_json(value);
        }
        images[plc_ids[index]] = std::move(image);
    }
    return images;
}

json actuators_to_json(const ActuatorState& actuator_state) {
    json actuators = json::object();
    for (const auto& [alias, value] : actuator_state.entries()) {
        actuators[alias] = cell_to_json(value);
    }
    return actuators;
}

std::expected<ActuatorState, PlantError> parse_actuators(const json& result) {
    if (!result.contains("actuators") || !result["actuators"].is_object()) {
        return protocol_error("read_actuators result carries no actuators object");
    }
    ActuatorState actuators;
    for (const auto& [alias, value] : result["actuators"].items()) {
        const std::optional<Cell> cell = cell_from_json(value);
        if (!cell.has_value()) {
            return protocol_error("actuator '" + alias + "' carries a non-scalar value");
        }
        actuators.set(alias, *cell);
    }
    return actuators;
}

std::expected<PlantOutputs, PlantError> parse_outputs(const json& result) {
    if (!result.contains("outputs") || !result["outputs"].is_object()) {
        return protocol_error("step result carries no outputs object");
    }
    const json& outputs = result["outputs"];
    for (const char* field : {"sensor_a_exit_triggered", "part_at_b"}) {
        if (!outputs.contains(field) || !outputs[field].is_boolean()) {
            return protocol_error("step outputs field '" + std::string(field) +
                                  "' must be a boolean");
        }
    }
    return PlantOutputs{outputs["sensor_a_exit_triggered"].get<bool>(),
                        outputs["part_at_b"].get<bool>()};
}

std::expected<std::vector<RoutedPlantSignal>, PlantError> parse_routed(
    const json& result, std::span<const std::string> plc_ids, const SignalTable& table) {
    if (!result.contains("routed") || !result["routed"].is_array()) {
        return protocol_error("route_to_plcs result carries no routed array");
    }
    std::vector<RoutedPlantSignal> routed;
    routed.reserve(result["routed"].size());
    for (const json& entry : result["routed"]) {
        if (!entry.is_object() || !entry.contains("to_plc") ||
            !entry["to_plc"].is_string() || !entry.contains("as_key") ||
            !entry["as_key"].is_string() || !entry.contains("value")) {
            return protocol_error("routed entry must carry to_plc, as_key, value");
        }
        const std::string to_plc = entry["to_plc"].get<std::string>();
        const auto plc_it = std::find(plc_ids.begin(), plc_ids.end(), to_plc);
        if (plc_it == plc_ids.end()) {
            return protocol_error("routed signal targets '" + to_plc +
                                  "', which is not a declared plc_id");
        }
        const std::string as_key = entry["as_key"].get<std::string>();
        const std::optional<std::uint32_t> signal_id = table.find_id(as_key);
        if (!signal_id.has_value()) {
            return protocol_error("routed signal key '" + as_key +
                                  "' is not in the signal table");
        }
        const std::optional<Cell> cell = cell_from_json(entry["value"]);
        if (!cell.has_value()) {
            return protocol_error("routed signal '" + as_key +
                                  "' carries a non-scalar value");
        }
        routed.push_back(RoutedPlantSignal{
            static_cast<std::uint32_t>(plc_it - plc_ids.begin()), *signal_id, *cell});
    }
    return routed;
}

}  // namespace

RemoteSocketPlant::State::State(asio::ip::tcp::socket socket_arg,
                                std::vector<std::string> plc_ids_arg,
                                SignalTable table_arg, double request_timeout_ms_arg)
    : socket(std::move(socket_arg)),
      write_queue(socket.get_executor(), kWriteQueueCapacity),
      plc_ids(std::move(plc_ids_arg)),
      table(std::move(table_arg)),
      request_timeout_ms(request_timeout_ms_arg) {}

RemoteSocketPlant::RemoteSocketPlant(std::shared_ptr<State> state)
    : state_(std::move(state)) {}

std::expected<RemoteSocketPlant, PlantError> RemoteSocketPlant::try_create(
    const ResolvedPlant& plant, std::span<const std::string> plc_ids,
    const SignalTable& table, Executor ex) {
    if (!plant.config.contains("endpoint") || !plant.config["endpoint"].is_string()) {
        return std::unexpected(PlantError{
            "remote_socket: config field 'endpoint' must be a 'host:port' string"});
    }
    const std::string endpoint = plant.config["endpoint"].get<std::string>();
    double timeout_ms = kDefaultRequestTimeoutMs;
    if (plant.config.contains("request_timeout_ms")) {
        if (!plant.config["request_timeout_ms"].is_number()) {
            return std::unexpected(PlantError{
                "remote_socket: config field 'request_timeout_ms' must be a number"});
        }
        timeout_ms = plant.config["request_timeout_ms"].get<double>();
        if (timeout_ms <= 0.0) {
            return std::unexpected(PlantError{
                "remote_socket: config field 'request_timeout_ms' must be > 0"});
        }
    }
    const std::size_t colon = endpoint.rfind(':');
    if (colon == std::string::npos || colon == 0 || colon + 1 == endpoint.size()) {
        return std::unexpected(PlantError{"remote_socket: endpoint '" + endpoint +
                                          "' is not of the form 'host:port'"});
    }

    asio::error_code ec;
    asio::ip::tcp::resolver resolver(ex);
    const auto endpoints =
        resolver.resolve(endpoint.substr(0, colon), endpoint.substr(colon + 1), ec);
    if (ec) {
        return std::unexpected(PlantError{"remote_socket: cannot resolve endpoint '" +
                                          endpoint + "': " + ec.message()});
    }
    asio::ip::tcp::socket socket(ex);
    asio::connect(socket, endpoints, ec);
    if (ec) {
        return std::unexpected(PlantError{
            "remote_socket: cannot connect to plant server at '" + endpoint +
            "': " + ec.message()});
    }
    socket.set_option(asio::ip::tcp::no_delay(true), ec);

    auto state = std::make_shared<State>(
        std::move(socket), std::vector<std::string>(plc_ids.begin(), plc_ids.end()),
        table, timeout_ms);
    return RemoteSocketPlant(std::move(state));
}

void RemoteSocketPlant::fail(const std::shared_ptr<State>& state, std::string message) {
    if (!state->dead.has_value()) {
        state->dead = std::move(message);
    }
    asio::error_code ignored;
    state->socket.close(ignored);
    state->write_queue.close();
    for (const auto& [id, waiter] : state->pending) {
        waiter->close();
    }
    state->pending.clear();
}

Task RemoteSocketPlant::read_loop(std::shared_ptr<State> state) {
    std::string buffer;
    while (true) {
        const auto [ec, size] = co_await asio::async_read_until(
            state->socket, asio::dynamic_buffer(buffer), '\n',
            asio::as_tuple(asio::use_awaitable));
        if (ec) {
            fail(state, "remote_socket: connection to plant server lost: " + ec.message());
            co_return;
        }
        json parsed = json::parse(buffer.substr(0, size), nullptr, false);
        buffer.erase(0, size);
        if (parsed.is_discarded() || !parsed.is_object() || !parsed.contains("id") ||
            !parsed["id"].is_number_integer()) {
            fail(state, "remote_socket: malformed response line from plant server");
            co_return;
        }
        const auto waiter = state->pending.find(parsed["id"].get<std::int64_t>());
        if (waiter != state->pending.end()) {
            waiter->second->try_send(asio::error_code{}, std::move(parsed));
            state->pending.erase(waiter);
        }
    }
}

Task RemoteSocketPlant::write_loop(std::shared_ptr<State> state) {
    while (true) {
        auto [ec, line] = co_await state->write_queue.async_receive(
            asio::as_tuple(asio::use_awaitable));
        if (ec) {
            co_return;
        }
        const auto [write_ec, written] = co_await asio::async_write(
            state->socket, asio::buffer(line), asio::as_tuple(asio::use_awaitable));
        if (write_ec) {
            fail(state, "remote_socket: write to plant server failed: " +
                            write_ec.message());
            co_return;
        }
    }
}

asio::awaitable<std::expected<nlohmann::json, PlantError>> RemoteSocketPlant::call(
    std::string_view method, nlohmann::json params) {
    const std::shared_ptr<State> state = state_;
    if (state->dead.has_value()) {
        co_return std::unexpected(PlantError{*state->dead});
    }
    if (!state->pumps_running) {
        state->pumps_running = true;
        asio::co_spawn(state->socket.get_executor(), read_loop(state), asio::detached);
        asio::co_spawn(state->socket.get_executor(), write_loop(state), asio::detached);
    }

    const std::int64_t id = state->next_id++;
    std::string line =
        json{{"id", id}, {"method", method}, {"params", std::move(params)}}.dump();
    line += '\n';

    const auto waiter =
        std::make_shared<Channel<json>>(state->socket.get_executor(), 1);
    state->pending.emplace(id, waiter);

    const auto [send_ec] = co_await state->write_queue.async_send(
        asio::error_code{}, std::move(line), asio::as_tuple(asio::use_awaitable));
    if (send_ec) {
        state->pending.erase(id);
        co_return std::unexpected(PlantError{state->dead.value_or(
            "remote_socket: connection to plant server is closed")});
    }

    asio::steady_timer timer(state->socket.get_executor());
    timer.expires_after(std::chrono::duration_cast<asio::steady_timer::duration>(
        std::chrono::duration<double, std::milli>(state->request_timeout_ms)));

    using namespace asio::experimental::awaitable_operators;
    auto outcome =
        co_await (waiter->async_receive(asio::as_tuple(asio::use_awaitable)) ||
                  timer.async_wait(asio::as_tuple(asio::use_awaitable)));
    state->pending.erase(id);

    if (outcome.index() == 1) {
        fail(state, "remote_socket: request " + std::to_string(id) + " ('" +
                        std::string(method) + "') timed out after " +
                        std::to_string(state->request_timeout_ms) + "ms");
        co_return std::unexpected(PlantError{*state->dead});
    }

    auto& [recv_ec, response] = std::get<0>(outcome);
    if (recv_ec) {
        co_return std::unexpected(PlantError{state->dead.value_or(
            "remote_socket: connection to plant server is closed")});
    }
    if (response.contains("error")) {
        std::string message = "plant server error";
        if (response["error"].is_object() && response["error"].contains("message") &&
            response["error"]["message"].is_string()) {
            message = response["error"]["message"].get<std::string>();
        }
        co_return std::unexpected(PlantError{"remote_socket: " + std::string(method) +
                                             " failed: " + message});
    }
    if (!response.contains("result") || !response["result"].is_object()) {
        co_return std::unexpected(PlantError{"remote_socket: response to " +
                                             std::string(method) +
                                             " carries no result object"});
    }
    co_return std::move(response["result"]);
}

asio::awaitable<std::expected<ActuatorState, PlantError>>
RemoteSocketPlant::read_actuators(std::span<const IOImage> latest_outputs) {
    json params{{"latest_outputs", images_to_json(latest_outputs, state_->plc_ids)}};
    auto result = co_await call("read_actuators", std::move(params));
    if (!result) {
        co_return std::unexpected(result.error());
    }
    co_return parse_actuators(*result);
}

asio::awaitable<std::expected<PlantOutputs, PlantError>> RemoteSocketPlant::step(
    double dt_ms, const ActuatorState& actuator_state) {
    json params{{"dt_ms", dt_ms}, {"actuators", actuators_to_json(actuator_state)}};
    auto result = co_await call("step", std::move(params));
    if (!result) {
        co_return std::unexpected(result.error());
    }
    co_return parse_outputs(*result);
}

asio::awaitable<std::expected<std::vector<RoutedPlantSignal>, PlantError>>
RemoteSocketPlant::route_to_plcs(PlantOutputs current, std::optional<PlantOutputs> prior) {
    json params{{"current", outputs_to_json(current)},
                {"prior", prior.has_value() ? outputs_to_json(*prior) : json(nullptr)}};
    auto result = co_await call("route_to_plcs", std::move(params));
    if (!result) {
        co_return std::unexpected(result.error());
    }
    co_return parse_routed(*result, state_->plc_ids, state_->table);
}

}  // namespace relay_host
