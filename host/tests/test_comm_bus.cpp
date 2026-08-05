#include <gtest/gtest.h>

#include <memory>

#include <hce.hpp>

#include "relay_host/comm_bus.hpp"
#include "relay_host/host_harness.hpp"

namespace relay_host {
namespace {

class HermesEnvironment : public ::testing::Environment {
 public:
    void SetUp() override { lifecycle_ = hce::initialize(); }
    void TearDown() override { lifecycle_.reset(); }

 private:
    std::unique_ptr<hce::lifecycle> lifecycle_;
};

const ::testing::Environment* const kHermesEnvironment =
    ::testing::AddGlobalTestEnvironment(new HermesEnvironment);

hce::co<void> drain_into(CommBus* bus, std::uint32_t plc_index) {
    bus->begin_drain(plc_index);
    Message msg{};
    while (true) {
        const hce::channel::result received =
            co_await bus->channel_of(plc_index).try_recv(msg);
        if (received != hce::channel::success) {
            co_return;
        }
        bus->fold(plc_index, msg);
    }
}

void drain(CommBus& bus, std::uint32_t plc_index) {
    join_scheduled(hce::schedule(drain_into(&bus, plc_index)));
}

void send(CommBus& bus, std::uint32_t to_plc, Message msg) {
    const bool sent = bus.send(to_plc, msg);
    ASSERT_TRUE(sent);
}

TEST(TestCommBus, send_then_drain_returns_message) {
    CommBus bus(1, 4, 64);
    send(bus, 0, Message{2, Cell{true}});
    drain(bus, 0);
    const CommBuffer& buffer = bus.buffer(0);
    ASSERT_EQ(buffer.present_ids().size(), 1u);
    ASSERT_TRUE(buffer.get(2).has_value());
    EXPECT_TRUE(is_truthy(*buffer.get(2)));
}

TEST(TestCommBus, drain_empty_returns_empty_buffer) {
    CommBus bus(1, 4, 64);
    drain(bus, 0);
    EXPECT_TRUE(bus.buffer(0).present_ids().empty());
}

TEST(TestCommBus, duplicate_key_in_one_scan_is_last_wins) {
    CommBus bus(1, 4, 64);
    send(bus, 0, Message{1, Cell{std::int64_t{7}}});
    send(bus, 0, Message{1, Cell{std::int64_t{9}}});
    drain(bus, 0);
    const CommBuffer& buffer = bus.buffer(0);
    ASSERT_EQ(buffer.present_ids().size(), 1u);
    EXPECT_EQ(std::get<std::int64_t>(*buffer.get(1)), 9);
}

TEST(TestCommBus, close_then_drain_returns_buffered_messages) {
    CommBus bus(1, 4, 64);
    send(bus, 0, Message{0, Cell{true}});
    send(bus, 0, Message{3, Cell{false}});
    bus.close();
    drain(bus, 0);
    const CommBuffer& buffer = bus.buffer(0);
    EXPECT_EQ(buffer.present_ids().size(), 2u);
    EXPECT_TRUE(buffer.get(0).has_value());
    EXPECT_TRUE(buffer.get(3).has_value());
}

}  // namespace
}  // namespace relay_host
