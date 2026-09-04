#include "relay_host/modbus_tcp_transport.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <string_view>
#include <utility>

#include <asio/experimental/awaitable_operators.hpp>

namespace relay_host {

namespace {

constexpr int kWriteQueueCapacity = 64;
constexpr std::string_view kCoilTable = "coil";

std::unexpected<TransportError> fail_with(const std::string& detail) {
    return std::unexpected(TransportError{"modbus_tcp: " + detail});
}

std::optional<std::uint32_t> index_of(std::span<const std::string> plc_ids,
                                      const std::string& plc_id) {
    const auto it = std::find(plc_ids.begin(), plc_ids.end(), plc_id);
    if (it == plc_ids.end()) {
        return std::nullopt;
    }
    return static_cast<std::uint32_t>(it - plc_ids.begin());
}

}  // namespace

ModbusTcpTransport::State::State(asio::ip::tcp::socket socket_arg,
                                 const ModbusEndpoint& endpoint_arg,
                                 SignalTable table_arg, CommBus* bus_arg)
    : socket(std::move(socket_arg)),
      write_queue(socket.get_executor(), kWriteQueueCapacity),
      table(std::move(table_arg)),
      bus(bus_arg),
      unit_id(endpoint_arg.unit_id),
      request_timeout_ms(endpoint_arg.request_timeout_ms) {}

ModbusTcpTransport::ModbusTcpTransport(std::shared_ptr<State> state)
    : state_(std::move(state)) {}

std::expected<ModbusTcpTransport, TransportError> ModbusTcpTransport::try_create(
    const ModbusEndpoint& endpoint, const ResolvedTaskSpec& spec,
    const SignalTable& table, CommBus& bus, Executor ex) {
    if (endpoint.request_timeout_ms <= 0.0) {
        return fail_with("request_timeout_ms must be > 0");
    }
    if (spec.comm.signals.empty()) {
        return fail_with(
            "the resolved spec declares no comm signals, so there is nothing to "
            "address; a Modbus transport needs a spec whose comm strategy binds "
            "every signal to a coil");
    }

    std::unordered_map<std::uint32_t, Binding> bindings;
    std::unordered_map<std::uint16_t, std::string> addresses;
    std::vector<std::vector<std::uint32_t>> consumed(spec.plc_ids.size());
    for (const ResolvedSignal& signal : spec.comm.signals) {
        if (!signal.table.has_value() || !signal.address.has_value()) {
            return fail_with("comm signal '" + signal.name +
                             "' declares no register binding; a Modbus transport "
                             "needs a spec whose comm strategy binds every signal to "
                             "a coil (the 'address' strategy does, 'tag' does not)");
        }
        if (*signal.table != kCoilTable) {
            return fail_with("comm signal '" + signal.name + "' binds table '" +
                             *signal.table +
                             "'; this client writes coils only, because every emit "
                             "mode assigns a boolean");
        }
        const auto signal_id = table.find_id(signal.name);
        if (!signal_id.has_value()) {
            return fail_with("comm signal '" + signal.name +
                             "' is not in the signal table");
        }
        const auto producer = index_of(spec.plc_ids, signal.produced_by);
        if (!producer.has_value()) {
            return fail_with("comm signal '" + signal.name + "' is produced by '" +
                             signal.produced_by + "', which is not a declared plc_id");
        }
        const auto address = static_cast<std::uint16_t>(*signal.address);
        const auto collision = addresses.find(address);
        if (collision != addresses.end()) {
            return fail_with("comm signals '" + collision->second + "' and '" +
                             signal.name + "' both bind coil " +
                             std::to_string(address));
        }
        addresses.emplace(address, signal.name);
        bindings.emplace(*signal_id, Binding{address, *producer});
        for (const std::string& consumer : signal.consumed_by) {
            const auto consumer_index = index_of(spec.plc_ids, consumer);
            if (!consumer_index.has_value()) {
                return fail_with("comm signal '" + signal.name + "' is consumed by '" +
                                 consumer +
                                 "', which is not a declared plc_id");
            }
            consumed[*consumer_index].push_back(*signal_id);
        }
    }

    const std::size_t colon = endpoint.endpoint.rfind(':');
    if (colon == std::string::npos || colon == 0 ||
        colon + 1 == endpoint.endpoint.size()) {
        return fail_with("endpoint '" + endpoint.endpoint +
                         "' is not of the form 'host:port'");
    }

    asio::error_code ec;
    asio::ip::tcp::resolver resolver(ex);
    const auto endpoints = resolver.resolve(endpoint.endpoint.substr(0, colon),
                                            endpoint.endpoint.substr(colon + 1), ec);
    if (ec) {
        return fail_with("cannot resolve endpoint '" + endpoint.endpoint +
                         "': " + ec.message());
    }
    asio::ip::tcp::socket socket(ex);
    asio::connect(socket, endpoints, ec);
    if (ec) {
        return fail_with("cannot connect to the register server at '" +
                         endpoint.endpoint + "': " + ec.message());
    }
    socket.set_option(asio::ip::tcp::no_delay(true), ec);

    auto state = std::make_shared<State>(std::move(socket), endpoint, table, &bus);
    state->bindings = std::move(bindings);
    state->consumed = std::move(consumed);
    return ModbusTcpTransport(std::move(state));
}

void ModbusTcpTransport::fail(const std::shared_ptr<State>& state, std::string message) {
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

Task ModbusTcpTransport::read_loop(std::shared_ptr<State> state) {
    while (true) {
        std::vector<std::uint8_t> frame(modbus::kLengthPrefixBytes);
        const auto [header_ec, header_size] = co_await asio::async_read(
            state->socket, asio::buffer(frame), asio::as_tuple(asio::use_awaitable));
        if (header_ec) {
            fail(state, "modbus_tcp: connection to the register server lost: " +
                            header_ec.message());
            co_return;
        }
        const auto total = modbus::expected_frame_length(frame);
        if (!total) {
            fail(state, total.error().message);
            co_return;
        }
        frame.resize(*total);
        const auto [body_ec, body_size] = co_await asio::async_read(
            state->socket,
            asio::buffer(frame.data() + modbus::kLengthPrefixBytes,
                         *total - modbus::kLengthPrefixBytes),
            asio::as_tuple(asio::use_awaitable));
        if (body_ec) {
            fail(state, "modbus_tcp: connection to the register server lost: " +
                            body_ec.message());
            co_return;
        }
        auto decoded = modbus::decode_response(frame);
        if (!decoded) {
            fail(state, decoded.error().message);
            co_return;
        }
        const auto waiter = state->pending.find(decoded->transaction_id);
        if (waiter == state->pending.end()) {
            fail(state, "modbus_tcp: response carries transaction id " +
                            std::to_string(decoded->transaction_id) +
                            ", which matches no request in flight");
            co_return;
        }
        waiter->second->try_send(asio::error_code{}, std::move(*decoded));
        state->pending.erase(waiter);
    }
}

Task ModbusTcpTransport::write_loop(std::shared_ptr<State> state) {
    while (true) {
        auto [ec, frame] =
            co_await state->write_queue.async_receive(asio::as_tuple(asio::use_awaitable));
        if (ec) {
            co_return;
        }
        const auto [write_ec, written] = co_await asio::async_write(
            state->socket, asio::buffer(frame), asio::as_tuple(asio::use_awaitable));
        if (write_ec) {
            fail(state, "modbus_tcp: write to the register server failed: " +
                            write_ec.message());
            co_return;
        }
    }
}

asio::awaitable<std::expected<modbus::Response, TransportError>>
ModbusTcpTransport::call(std::uint8_t function, std::uint16_t address,
                         std::uint16_t value) {
    const std::shared_ptr<State> state = state_;
    if (state->dead.has_value()) {
        co_return std::unexpected(TransportError{*state->dead});
    }
    if (!state->pumps_running) {
        state->pumps_running = true;
        asio::co_spawn(state->socket.get_executor(), read_loop(state), asio::detached);
        asio::co_spawn(state->socket.get_executor(), write_loop(state), asio::detached);
    }

    std::uint16_t transaction_id = state->next_transaction_id;
    while (transaction_id == 0 || state->pending.count(transaction_id) != 0) {
        ++transaction_id;
    }
    state->next_transaction_id = static_cast<std::uint16_t>(transaction_id + 1);

    const auto waiter =
        std::make_shared<Channel<modbus::Response>>(state->socket.get_executor(), 1);
    state->pending.emplace(transaction_id, waiter);

    const auto [send_ec] = co_await state->write_queue.async_send(
        asio::error_code{},
        modbus::encode_request(
            modbus::Request{transaction_id, state->unit_id, function, address, value}),
        asio::as_tuple(asio::use_awaitable));
    if (send_ec) {
        state->pending.erase(transaction_id);
        co_return std::unexpected(TransportError{state->dead.value_or(
            "modbus_tcp: connection to the register server is closed")});
    }

    asio::steady_timer timer(state->socket.get_executor());
    timer.expires_after(std::chrono::duration_cast<asio::steady_timer::duration>(
        std::chrono::duration<double, std::milli>(state->request_timeout_ms)));

    using namespace asio::experimental::awaitable_operators;
    auto outcome = co_await (waiter->async_receive(asio::as_tuple(asio::use_awaitable)) ||
                             timer.async_wait(asio::as_tuple(asio::use_awaitable)));
    state->pending.erase(transaction_id);

    if (outcome.index() == 1) {
        fail(state, "modbus_tcp: request " + std::to_string(transaction_id) +
                        " (function " + std::to_string(function) + ", address " +
                        std::to_string(address) + ") timed out after " +
                        std::to_string(state->request_timeout_ms) + "ms");
        co_return std::unexpected(TransportError{*state->dead});
    }

    auto& [recv_ec, response] = std::get<0>(outcome);
    if (recv_ec) {
        co_return std::unexpected(TransportError{state->dead.value_or(
            "modbus_tcp: connection to the register server is closed")});
    }
    if (response.unit_id != state->unit_id) {
        fail(state, "modbus_tcp: response carries unit id " +
                        std::to_string(response.unit_id) + ", expected " +
                        std::to_string(state->unit_id));
        co_return std::unexpected(TransportError{*state->dead});
    }
    if (response.function != function) {
        fail(state, "modbus_tcp: response to function " + std::to_string(function) +
                        " carries function " + std::to_string(response.function));
        co_return std::unexpected(TransportError{*state->dead});
    }
    if (response.exception_code.has_value()) {
        co_return std::unexpected(TransportError{
            "modbus_tcp: function " + std::to_string(function) + " at address " +
            std::to_string(address) + " was refused with exception code " +
            std::to_string(*response.exception_code)});
    }
    co_return std::move(response);
}

asio::awaitable<std::expected<void, TransportError>> ModbusTcpTransport::emit(
    const OutgoingMessage& message, SimClock) {
    const std::shared_ptr<State> state = state_;
    const std::uint32_t signal_id = message.msg.signal_id;
    const auto binding = state->bindings.find(signal_id);
    if (binding == state->bindings.end()) {
        co_return fail_with("signal '" + state->table.name_of(signal_id) +
                            "' has no register binding, so it cannot be written");
    }
    const std::uint16_t address = binding->second.address;
    const std::uint16_t value =
        is_truthy(message.msg.value) ? modbus::kCoilOn : modbus::kCoilOff;

    auto response = co_await call(modbus::kWriteSingleCoil, address, value);
    if (!response) {
        co_return std::unexpected(response.error());
    }
    const std::uint16_t echoed_address =
        static_cast<std::uint16_t>((response->payload[0] << 8) | response->payload[1]);
    const std::uint16_t echoed_value =
        static_cast<std::uint16_t>((response->payload[2] << 8) | response->payload[3]);
    if (echoed_address != address || echoed_value != value) {
        fail(state, "modbus_tcp: write to coil " + std::to_string(address) +
                        " was acknowledged for coil " + std::to_string(echoed_address) +
                        " with value " + std::to_string(echoed_value));
        co_return std::unexpected(TransportError{*state->dead});
    }

    state->acked_seq[signal_id] = message.msg.seq;
    co_return std::expected<void, TransportError>{};
}

asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>
ModbusTcpTransport::poll(std::uint32_t plc_index, SimClock) {
    const std::shared_ptr<State> state = state_;
    std::vector<PolledValue> polled;
    while (true) {
        Message msg{};
        const bool received = state->bus->channel_of(plc_index).try_receive(
            [&](asio::error_code, Message m) { msg = m; });
        if (!received) {
            break;
        }
        polled.push_back(PolledValue{msg.signal_id, msg.value, msg.sender_plc, msg.seq});
    }

    for (const std::uint32_t signal_id : state->consumed[plc_index]) {
        const auto acked = state->acked_seq.find(signal_id);
        if (acked == state->acked_seq.end()) {
            continue;
        }
        const std::int64_t seq = acked->second;
        const Binding binding = state->bindings.at(signal_id);
        auto response = co_await call(modbus::kReadCoils, binding.address, 1);
        if (!response) {
            co_return std::unexpected(response.error());
        }
        polled.push_back(PolledValue{signal_id, Cell{(response->payload[0] & 0x01) != 0},
                                     binding.producer_plc, seq});
    }
    co_return polled;
}

}  // namespace relay_host
