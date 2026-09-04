#include "relay_host/comm_transport_registry.hpp"

#include <utility>
#include <algorithm>
#include <functional>
#include <unordered_map>

namespace relay_host {

InProcessTransport::InProcessTransport(CommBus* bus, const ResolvedTaskSpec& spec,
                                       const SignalTable& table)
    : bus_(bus), consumers_(table.size()) {
    for (const ResolvedSignal& signal : spec.comm.signals) {
        const auto signal_id = table.find_id(signal.name);
        if (!signal_id) continue;
        for (const std::string& consumer : signal.consumed_by) {
            const auto found = std::find(spec.plc_ids.begin(), spec.plc_ids.end(), consumer);
            if (found != spec.plc_ids.end()) {
                consumers_[*signal_id].push_back(
                    static_cast<std::uint32_t>(found - spec.plc_ids.begin()));
            }
        }
    }
}

asio::awaitable<std::expected<void, TransportError>> InProcessTransport::emit(
    const OutgoingMessage& message, SimClock) {
    for (const std::uint32_t consumer : consumers_.at(message.msg.signal_id)) {
        co_await bus_->send(consumer, message.msg);
    }
    co_return std::expected<void, TransportError>{};
}

asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>
InProcessTransport::poll(std::uint32_t plc_index, SimClock) {
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
    using Factory = std::function<std::expected<CommTransportVariant, TransportError>()>;
    std::unordered_map<std::string, Factory> factories;
    factories.emplace("in_process", [&]() {
        return CommTransportVariant{InProcessTransport{&bus, spec, table}};
    });
    factories.emplace("can", [&]() {
        if (endpoint) return std::expected<CommTransportVariant, TransportError>{
            std::unexpected(TransportError{
                "comm_transport: --comm-endpoint is valid only for Modbus, not CAN"})};
        return std::expected<CommTransportVariant, TransportError>{
            CommTransportVariant{CanTransport{spec, table, &bus}}};
    });
    factories.emplace("modbus", [&]() -> std::expected<CommTransportVariant, TransportError> {
        if (!endpoint) return std::unexpected(TransportError{
            "comm_transport: Modbus transport requires --comm-endpoint"});
        auto transport = ModbusTcpTransport::try_create(*endpoint, spec, table, bus, ex);
        if (!transport) return std::unexpected(transport.error());
        return CommTransportVariant{std::move(*transport)};
    });
    const auto factory = factories.find(spec.comm.transport.kind);
    if (factory == factories.end()) {
        return std::unexpected(TransportError{"comm_transport: unknown transport kind '" +
                                               spec.comm.transport.kind + "'"});
    }
    return factory->second();
}

}  // namespace relay_host
