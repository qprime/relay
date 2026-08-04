#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <vector>

#include <hce.hpp>

#include "relay_host/io_image.hpp"

namespace relay_host {

struct Message {
    std::uint32_t signal_id;
    Cell value;
};

class CommBuffer {
 public:
    explicit CommBuffer(std::uint32_t signal_count);

    void set(std::uint32_t signal_id, Cell value) noexcept;
    void clear() noexcept;
    [[nodiscard]] std::optional<Cell> get(std::uint32_t signal_id) const noexcept;
    [[nodiscard]] std::span<const std::uint32_t> present_ids() const noexcept;

 private:
    std::vector<std::optional<Cell>> cells_;
    std::vector<std::uint32_t> order_;
};

class CommBus {
 public:
    CommBus(std::uint32_t plc_count, std::uint32_t signal_count, int channel_capacity);

    // The reference is captured by the channel until the returned awaitable
    // completes; the argument must outlive the co_await of the result.
    [[nodiscard]] hce::awt<bool> send(std::uint32_t to_plc, const Message& msg);
    void begin_drain(std::uint32_t plc_index) noexcept;
    void fold(std::uint32_t plc_index, const Message& msg) noexcept;
    [[nodiscard]] hce::chan<Message>& channel_of(std::uint32_t plc_index) noexcept;
    [[nodiscard]] const CommBuffer& buffer(std::uint32_t plc_index) const noexcept;
    void close();

 private:
    std::vector<hce::chan<Message>> channels_;
    std::vector<CommBuffer> buffers_;
};

}  // namespace relay_host
