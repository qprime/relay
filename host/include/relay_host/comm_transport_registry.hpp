#pragma once

#include <expected>
#include <optional>
#include <variant>

#include "relay_host/async.hpp"
#include "relay_host/comm_bus.hpp"
#include "relay_host/comm_transport.hpp"
#include "relay_host/can_transport.hpp"
#include "relay_host/modbus_tcp_transport.hpp"
#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"

namespace relay_host {

using CommTransportVariant =
    std::variant<InProcessTransport, ModbusTcpTransport, CanTransport>;

[[nodiscard]] std::expected<CommTransportVariant, TransportError> build_comm_transport(
    const std::optional<ModbusEndpoint>& endpoint, const ResolvedTaskSpec& spec,
    const SignalTable& table, CommBus& bus, Executor ex);

}  // namespace relay_host
