#pragma once

#include <concepts>
#include <cstdint>
#include <expected>
#include <string>
#include <vector>

#include "relay_host/async.hpp"
#include "relay_host/comm_bus.hpp"
#include "relay_host/clock.hpp"
#include "relay_host/io_image.hpp"
#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"

namespace relay_host {

struct TransportError {
    std::string message;
};

struct PolledValue {
    std::uint32_t signal_id;
    Cell value;
    std::uint32_t sender_plc;
    std::int64_t seq;
};

template <typename T>
concept CommTransport = requires(T transport, const OutgoingMessage& message,
                                 std::uint32_t plc_index, SimClock clock) {
    {
        transport.emit(message, clock)
    } -> std::same_as<asio::awaitable<std::expected<void, TransportError>>>;
    {
        transport.poll(plc_index, clock)
    } -> std::same_as<
        asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>>;
};

class InProcessTransport {
 public:
    InProcessTransport(CommBus* bus, const ResolvedTaskSpec& spec,
                       const SignalTable& table);

    [[nodiscard]] asio::awaitable<std::expected<void, TransportError>> emit(
        const OutgoingMessage& message, SimClock clock);
    [[nodiscard]] asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>
    poll(std::uint32_t plc_index, SimClock clock);

 private:
    CommBus* bus_;
    std::vector<std::vector<std::uint32_t>> consumers_;
};

static_assert(CommTransport<InProcessTransport>);

}  // namespace relay_host
