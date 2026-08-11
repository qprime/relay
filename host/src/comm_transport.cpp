#include "relay_host/comm_transport_registry.hpp"

#include <utility>

namespace relay_host {

InProcessTransport::InProcessTransport(CommBus* bus) noexcept : bus_(bus) {}

asio::awaitable<std::expected<void, TransportError>> InProcessTransport::emit(
    const OutgoingMessage& message) {
    co_await bus_->send(message.target_plc, message.msg);
    co_return std::expected<void, TransportError>{};
}

asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>
InProcessTransport::poll(std::uint32_t plc_index) {
    std::vector<PolledValue> polled;
    while (true) {
        Message msg{};
        const bool received = bus_->channel_of(plc_index).try_receive(
            [&](asio::error_code, Message m) { msg = m; });
        if (!received) {
            break;
        }
        polled.push_back(PolledValue{msg.signal_id, msg.value, msg.sender_plc, msg.seq});
    }
    co_return polled;
}

std::expected<CommTransportVariant, TransportError> build_comm_transport(
    const std::optional<ModbusEndpoint>& endpoint, const ResolvedTaskSpec& spec,
    const SignalTable& table, CommBus& bus, Executor ex) {
    if (!endpoint.has_value()) {
        return CommTransportVariant{InProcessTransport{&bus}};
    }
    auto transport =
        ModbusTcpTransport::try_create(*endpoint, spec, table, bus, std::move(ex));
    if (!transport) {
        return std::unexpected(transport.error());
    }
    return CommTransportVariant{std::move(*transport)};
}

}  // namespace relay_host
