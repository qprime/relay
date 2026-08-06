#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <vector>

#include "relay_host/async.hpp"
#include "relay_host/io_image.hpp"

namespace relay_host {

struct Message {
    std::uint32_t signal_id;
    Cell value;
    std::uint32_t sender_plc;
    std::int64_t seq;
};

struct Receipt {
    std::uint32_t sender_plc;
    std::int64_t seq;
};

class CommBuffer {
 public:
    explicit CommBuffer(std::uint32_t signal_count);

    void set(std::uint32_t signal_id, Cell value, std::uint32_t sender_plc,
             std::int64_t seq) noexcept;
    void clear() noexcept;
    [[nodiscard]] std::optional<Cell> get(std::uint32_t signal_id) const noexcept;
    [[nodiscard]] std::optional<Receipt> receipt_of(
        std::uint32_t signal_id) const noexcept;
    [[nodiscard]] std::span<const std::uint32_t> present_ids() const noexcept;

 private:
    std::vector<std::optional<Cell>> cells_;
    std::vector<std::optional<Receipt>> receipts_;
    std::vector<std::uint32_t> order_;
};

class CommBus {
 public:
    CommBus(Executor ex, std::uint32_t plc_count, std::uint32_t signal_count,
            int channel_capacity);

    [[nodiscard]] asio::awaitable<bool> send(std::uint32_t to_plc, const Message& msg);
    [[nodiscard]] bool try_send(std::uint32_t to_plc, const Message& msg);
    void begin_drain(std::uint32_t plc_index) noexcept;
    void fold(std::uint32_t plc_index, const Message& msg) noexcept;
    [[nodiscard]] Channel<Message>& channel_of(std::uint32_t plc_index) noexcept;
    [[nodiscard]] const CommBuffer& buffer(std::uint32_t plc_index) const noexcept;
    void close_receiver(std::uint32_t plc_index);
    [[nodiscard]] bool receiver_open(std::uint32_t plc_index) const noexcept;
    [[nodiscard]] std::int64_t dropped(std::uint32_t plc_index) const noexcept;
    [[nodiscard]] std::int64_t dropped_total() const noexcept;
    void close();

 private:
    std::vector<Channel<Message>> channels_;
    std::vector<CommBuffer> buffers_;
    std::vector<bool> receiver_open_;
    std::vector<std::int64_t> dropped_;
};

}  // namespace relay_host
