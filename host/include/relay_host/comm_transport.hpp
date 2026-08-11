#pragma once

#include <concepts>
#include <cstdint>
#include <expected>
#include <string>
#include <vector>

#include "relay_host/async.hpp"
#include "relay_host/comm_bus.hpp"
#include "relay_host/io_image.hpp"

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
                                 std::uint32_t plc_index) {
    {
        transport.emit(message)
    } -> std::same_as<asio::awaitable<std::expected<void, TransportError>>>;
    {
        transport.poll(plc_index)
    } -> std::same_as<
        asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>>;
};

class InProcessTransport {
 public:
    explicit InProcessTransport(CommBus* bus) noexcept;

    [[nodiscard]] asio::awaitable<std::expected<void, TransportError>> emit(
        const OutgoingMessage& message);
    [[nodiscard]] asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>
    poll(std::uint32_t plc_index);

 private:
    CommBus* bus_;
};

static_assert(CommTransport<InProcessTransport>);

}  // namespace relay_host
