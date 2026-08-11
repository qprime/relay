#pragma once

#include <cstdint>
#include <expected>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "relay_host/async.hpp"
#include "relay_host/comm_transport.hpp"
#include "relay_host/modbus_codec.hpp"
#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"

namespace relay_host {

struct ModbusEndpoint {
    std::string endpoint;
    std::uint8_t unit_id = 1;
    double request_timeout_ms = 1000.0;
};

class ModbusTcpTransport {
 public:
    [[nodiscard]] static std::expected<ModbusTcpTransport, TransportError> try_create(
        const ModbusEndpoint& endpoint, const ResolvedTaskSpec& spec,
        const SignalTable& table, CommBus& bus, Executor ex);

    [[nodiscard]] asio::awaitable<std::expected<void, TransportError>> emit(
        const OutgoingMessage& message);
    [[nodiscard]] asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>
    poll(std::uint32_t plc_index);

 private:
    struct Binding {
        std::uint16_t address;
        std::uint32_t producer_plc;
    };

    struct State {
        State(asio::ip::tcp::socket socket_arg, const ModbusEndpoint& endpoint_arg,
              SignalTable table_arg, CommBus* bus_arg);

        asio::ip::tcp::socket socket;
        Channel<std::vector<std::uint8_t>> write_queue;
        SignalTable table;
        CommBus* bus;
        std::uint8_t unit_id;
        double request_timeout_ms;
        std::unordered_map<std::uint32_t, Binding> bindings;
        std::vector<std::vector<std::uint32_t>> consumed;
        std::unordered_map<std::uint32_t, std::int64_t> acked_seq;
        std::map<std::uint16_t, std::shared_ptr<Channel<modbus::Response>>> pending;
        std::uint16_t next_transaction_id = 1;
        bool pumps_running = false;
        std::optional<std::string> dead;
    };

    explicit ModbusTcpTransport(std::shared_ptr<State> state);

    static void fail(const std::shared_ptr<State>& state, std::string message);
    static Task read_loop(std::shared_ptr<State> state);
    static Task write_loop(std::shared_ptr<State> state);
    [[nodiscard]] asio::awaitable<std::expected<modbus::Response, TransportError>> call(
        std::uint8_t function, std::uint16_t address, std::uint16_t value);

    std::shared_ptr<State> state_;
};

static_assert(CommTransport<ModbusTcpTransport>);

}  // namespace relay_host
