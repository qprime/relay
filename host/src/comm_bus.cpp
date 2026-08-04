#include "relay_host/comm_bus.hpp"

namespace relay_host {

CommBuffer::CommBuffer(std::uint32_t signal_count) : cells_(signal_count) {
    order_.reserve(signal_count);
}

void CommBuffer::set(std::uint32_t signal_id, Cell value) noexcept {
    if (!cells_[signal_id].has_value()) {
        order_.push_back(signal_id);
    }
    cells_[signal_id] = value;
}

void CommBuffer::clear() noexcept {
    for (const std::uint32_t id : order_) {
        cells_[id].reset();
    }
    order_.clear();
}

std::optional<Cell> CommBuffer::get(std::uint32_t signal_id) const noexcept {
    return cells_[signal_id];
}

std::span<const std::uint32_t> CommBuffer::present_ids() const noexcept {
    return order_;
}

CommBus::CommBus(std::uint32_t plc_count, std::uint32_t signal_count,
                 int channel_capacity) {
    channels_.reserve(plc_count);
    buffers_.reserve(plc_count);
    for (std::uint32_t index = 0; index < plc_count; ++index) {
        channels_.push_back(hce::chan<Message>::make(channel_capacity));
        buffers_.emplace_back(signal_count);
    }
}

hce::awt<bool> CommBus::send(std::uint32_t to_plc, const Message& msg) {
    return channels_[to_plc].send(msg);
}

void CommBus::begin_drain(std::uint32_t plc_index) noexcept {
    buffers_[plc_index].clear();
}

void CommBus::fold(std::uint32_t plc_index, const Message& msg) noexcept {
    buffers_[plc_index].set(msg.signal_id, msg.value);
}

hce::chan<Message>& CommBus::channel_of(std::uint32_t plc_index) noexcept {
    return channels_[plc_index];
}

const CommBuffer& CommBus::buffer(std::uint32_t plc_index) const noexcept {
    return buffers_[plc_index];
}

void CommBus::close() {
    for (hce::chan<Message>& channel : channels_) {
        channel.close();
    }
}

}  // namespace relay_host
