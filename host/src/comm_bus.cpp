#include "relay_host/comm_bus.hpp"

namespace relay_host {

CommBuffer::CommBuffer(std::uint32_t signal_count)
    : cells_(signal_count), receipts_(signal_count) {
    order_.reserve(signal_count);
}

void CommBuffer::set(std::uint32_t signal_id, Cell value, std::uint32_t sender_plc,
                     std::int64_t seq) noexcept {
    if (!cells_[signal_id].has_value()) {
        order_.push_back(signal_id);
    }
    cells_[signal_id] = value;
    receipts_[signal_id] = Receipt{sender_plc, seq};
}

void CommBuffer::clear() noexcept {
    for (const std::uint32_t id : order_) {
        cells_[id].reset();
        receipts_[id].reset();
    }
    order_.clear();
}

std::optional<Cell> CommBuffer::get(std::uint32_t signal_id) const noexcept {
    return cells_[signal_id];
}

std::optional<Receipt> CommBuffer::receipt_of(std::uint32_t signal_id) const noexcept {
    return receipts_[signal_id];
}

std::span<const std::uint32_t> CommBuffer::present_ids() const noexcept {
    return order_;
}

CommBus::CommBus(Executor ex, std::uint32_t plc_count, std::uint32_t signal_count,
                 int channel_capacity) {
    channels_.reserve(plc_count);
    buffers_.reserve(plc_count);
    for (std::uint32_t index = 0; index < plc_count; ++index) {
        channels_.emplace_back(ex, static_cast<std::size_t>(channel_capacity));
        buffers_.emplace_back(signal_count);
    }
}

asio::awaitable<bool> CommBus::send(std::uint32_t to_plc, const Message& msg) {
    const auto [ec] = co_await channels_[to_plc].async_send(
        asio::error_code{}, msg, asio::as_tuple(asio::use_awaitable));
    co_return !ec;
}

bool CommBus::try_send(std::uint32_t to_plc, const Message& msg) {
    return channels_[to_plc].try_send(asio::error_code{}, msg);
}

void CommBus::begin_drain(std::uint32_t plc_index) noexcept {
    buffers_[plc_index].clear();
}

void CommBus::fold(std::uint32_t plc_index, const Message& msg) noexcept {
    buffers_[plc_index].set(msg.signal_id, msg.value, msg.sender_plc, msg.seq);
}

Channel<Message>& CommBus::channel_of(std::uint32_t plc_index) noexcept {
    return channels_[plc_index];
}

const CommBuffer& CommBus::buffer(std::uint32_t plc_index) const noexcept {
    return buffers_[plc_index];
}

void CommBus::close() {
    for (Channel<Message>& channel : channels_) {
        channel.close();
    }
}

}  // namespace relay_host
