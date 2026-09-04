#pragma once

#include <cstdint>
#include <expected>
#include <vector>
#include <optional>

#include "relay_host/can_codec.hpp"
#include "relay_host/comm_transport.hpp"

namespace relay_host {

class CanTransport {
 public:
    CanTransport(const ResolvedTaskSpec& spec, const SignalTable& table, CommBus* bus);

    [[nodiscard]] asio::awaitable<std::expected<void, TransportError>> emit(
        const OutgoingMessage& message, SimClock clock);
    [[nodiscard]] asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>
    poll(std::uint32_t plc_index, SimClock clock);

 private:
    struct Frame {
        Message message;
        std::uint16_t can_id;
        std::uint32_t bit_count;
        std::uint64_t ready_ps;
        std::uint64_t order;
        SeqSlot* trace_slot;
    };
    struct Delivery { Message message; std::uint64_t completion_ps; };

    std::uint32_t baud_rate_;
    CommBus* bus_;
    std::uint64_t available_ps_ = 0;
    std::uint64_t next_order_ = 0;
    std::vector<std::uint16_t> can_ids_;
    std::vector<std::vector<std::uint32_t>> consumers_;
    std::vector<Frame> pending_;
    std::optional<Frame> active_;
    std::uint64_t active_completion_ps_ = 0;
    std::vector<std::vector<Delivery>> deliveries_;
};

static_assert(CommTransport<CanTransport>);

}  // namespace relay_host
