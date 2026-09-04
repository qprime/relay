#include "relay_host/can_transport.hpp"
#include "relay_host/trace.hpp"

#include <algorithm>
#include <cmath>

namespace relay_host {

namespace {
std::uint64_t to_ps(double milliseconds) {
    return static_cast<std::uint64_t>(std::llround(milliseconds * 1000000000.0));
}
}

CanTransport::CanTransport(const ResolvedTaskSpec& spec, const SignalTable& table,
                           CommBus* bus)
    : baud_rate_(spec.comm.transport.baud_rate.value_or(0)),
      bus_(bus),
      can_ids_(table.size(), 0), consumers_(table.size()),
      deliveries_(spec.plc_ids.size()) {
    for (const ResolvedSignal& signal : spec.comm.signals) {
        const auto id = table.find_id(signal.name);
        if (!id || !signal.can_id) continue;
        can_ids_[*id] = *signal.can_id;
        for (const std::string& consumer : signal.consumed_by) {
            const auto found = std::find(spec.plc_ids.begin(), spec.plc_ids.end(), consumer);
            if (found != spec.plc_ids.end()) consumers_[*id].push_back(
                static_cast<std::uint32_t>(found - spec.plc_ids.begin()));
        }
    }
}

asio::awaitable<std::expected<void, TransportError>> CanTransport::emit(
    const OutgoingMessage& outgoing, SimClock clock) {
    const bool* value = std::get_if<bool>(&outgoing.msg.value);
    if (!value) co_return std::unexpected(TransportError{"can: signal value must be bool"});
    const std::uint16_t can_id = can_ids_.at(outgoing.msg.signal_id);
    auto encoded = can::encode(can_id, *value);
    if (!encoded) co_return std::unexpected(TransportError{"can: " + encoded.error().message});
    if (outgoing.trace_slot != nullptr) {
        outgoing.trace_slot->can_id = can_id;
        outgoing.trace_slot->frame_bits =
            static_cast<std::uint32_t>(encoded->bits.size());
    }
    pending_.push_back(Frame{outgoing.msg, can_id,
                             static_cast<std::uint32_t>(encoded->bits.size()),
                             to_ps(clock.elapsed_ms), next_order_++,
                             outgoing.trace_slot});
    co_return std::expected<void, TransportError>{};
}

asio::awaitable<std::expected<std::vector<PolledValue>, TransportError>>
CanTransport::poll(std::uint32_t plc_index, SimClock clock) {
    const std::uint64_t limit = to_ps(clock.elapsed_ms);
    std::vector<PolledValue> result;
    while (true) {
        Message message{};
        const bool received = bus_->channel_of(plc_index).try_receive(
            [&](asio::error_code, Message item) { message = item; });
        if (!received) break;
        result.push_back(PolledValue{message.signal_id, message.value,
                                     message.sender_plc, message.seq});
    }
    while (active_ || !pending_.empty()) {
        if (active_) {
            if (active_completion_ps_ > limit) break;
            for (const std::uint32_t consumer : consumers_[active_->message.signal_id]) {
                deliveries_[consumer].push_back(
                    Delivery{active_->message, active_completion_ps_});
            }
            if (active_->trace_slot != nullptr) {
                active_->trace_slot->completion_ms =
                    static_cast<double>(active_completion_ps_) / 1000000000.0;
            }
            active_.reset();
            continue;
        }
        const auto earliest = std::min_element(pending_.begin(), pending_.end(),
            [](const Frame& a, const Frame& b) { return a.ready_ps < b.ready_ps; })->ready_ps;
        const std::uint64_t start = std::max(available_ps_, earliest);
        auto winner = pending_.end();
        for (auto it = pending_.begin(); it != pending_.end(); ++it) {
            if (it->ready_ps > start) continue;
            if (winner == pending_.end() || it->can_id < winner->can_id ||
                (it->can_id == winner->can_id && it->order < winner->order)) winner = it;
        }
        const std::uint64_t duration =
            (static_cast<std::uint64_t>(winner->bit_count) * 1000000000000ULL +
             baud_rate_ - 1) / baud_rate_;
        const std::uint64_t finish = start + duration;
        available_ps_ = finish;
        active_ = *winner;
        if (active_->trace_slot != nullptr) {
            active_->trace_slot->arbitration_start_ms =
                static_cast<double>(start) / 1000000000.0;
        }
        active_completion_ps_ = finish;
        pending_.erase(winner);
    }
    std::vector<Delivery>& ready = deliveries_.at(plc_index);
    for (const Delivery& delivery : ready) {
        result.push_back(PolledValue{delivery.message.signal_id, delivery.message.value,
                                     delivery.message.sender_plc, delivery.message.seq});
    }
    ready.clear();
    co_return result;
}

}  // namespace relay_host
