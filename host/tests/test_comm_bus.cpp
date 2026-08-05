#include <gtest/gtest.h>

#include "relay_host/comm_bus.hpp"

namespace relay_host {
namespace {

void drain(CommBus& bus, std::uint32_t plc_index) {
    bus.begin_drain(plc_index);
    while (true) {
        Message msg{};
        const bool received =
            bus.channel_of(plc_index).try_receive([&](asio::error_code, Message m) {
                msg = m;
            });
        if (!received) {
            break;
        }
        bus.fold(plc_index, msg);
    }
}

void send(CommBus& bus, std::uint32_t to_plc, Message msg) {
    const bool sent = bus.try_send(to_plc, msg);
    ASSERT_TRUE(sent);
}

TEST(TestCommBus, send_then_drain_returns_message) {
    asio::io_context io;
    CommBus bus(io.get_executor(), 1, 4, 64);
    send(bus, 0, Message{2, Cell{true}});
    drain(bus, 0);
    const CommBuffer& buffer = bus.buffer(0);
    ASSERT_EQ(buffer.present_ids().size(), 1u);
    ASSERT_TRUE(buffer.get(2).has_value());
    EXPECT_TRUE(is_truthy(*buffer.get(2)));
}

TEST(TestCommBus, drain_empty_returns_empty_buffer) {
    asio::io_context io;
    CommBus bus(io.get_executor(), 1, 4, 64);
    drain(bus, 0);
    EXPECT_TRUE(bus.buffer(0).present_ids().empty());
}

TEST(TestCommBus, duplicate_key_in_one_scan_is_last_wins) {
    asio::io_context io;
    CommBus bus(io.get_executor(), 1, 4, 64);
    send(bus, 0, Message{1, Cell{std::int64_t{7}}});
    send(bus, 0, Message{1, Cell{std::int64_t{9}}});
    drain(bus, 0);
    const CommBuffer& buffer = bus.buffer(0);
    ASSERT_EQ(buffer.present_ids().size(), 1u);
    EXPECT_EQ(std::get<std::int64_t>(*buffer.get(1)), 9);
}

TEST(TestCommBus, close_then_drain_returns_buffered_messages) {
    asio::io_context io;
    CommBus bus(io.get_executor(), 1, 4, 64);
    send(bus, 0, Message{0, Cell{true}});
    send(bus, 0, Message{3, Cell{false}});
    bus.close();
    drain(bus, 0);
    const CommBuffer& buffer = bus.buffer(0);
    EXPECT_EQ(buffer.present_ids().size(), 2u);
    EXPECT_TRUE(buffer.get(0).has_value());
    EXPECT_TRUE(buffer.get(3).has_value());
}

TEST(TestCommBus, send_after_close_returns_false) {
    asio::io_context io;
    CommBus bus(io.get_executor(), 1, 4, 64);
    bus.close();
    EXPECT_FALSE(bus.try_send(0, Message{0, Cell{true}}));
}

}  // namespace
}  // namespace relay_host
