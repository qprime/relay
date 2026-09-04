#include <gtest/gtest.h>

#include <optional>
#include <vector>

#include "relay_host/can_transport.hpp"

namespace relay_host {
namespace {

struct CanRig {
    asio::io_context io;
    ResolvedTaskSpec spec;
    SignalTable table;
    std::optional<CommBus> bus;
    std::optional<CanTransport> transport;

    CanRig() {
        spec.plc_ids = {"high", "low", "consumer"};
        spec.comm.transport.baud_rate = 1'000;
        spec.comm.signals = {
            ResolvedSignal{"high_signal", "high", {"consumer"}, std::nullopt,
                           std::nullopt, 0x300},
            ResolvedSignal{"low_signal", "low", {"consumer"}, std::nullopt,
                           std::nullopt, 0x080},
        };
        table.add("high_signal");
        table.add("low_signal");
        bus.emplace(io.get_executor(), 3, table.size(), 64);
        transport.emplace(spec, table, &*bus);
    }

    OutgoingMessage message(std::string_view name, std::uint32_t sender) {
        return OutgoingMessage{Message{*table.find_id(name), Cell{true}, sender, 1}};
    }
};

TEST(TestCanTransport, same_instant_emitters_arbitrate_after_the_instant_closes) {
    CanRig rig;
    std::vector<PolledValue> received;
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            EXPECT_TRUE((co_await rig.transport->emit(rig.message("high_signal", 0),
                                                      SimClock{0, 0.0})).has_value());
            EXPECT_TRUE((co_await rig.transport->poll(2, SimClock{0, 0.0})).has_value());
            EXPECT_TRUE((co_await rig.transport->emit(rig.message("low_signal", 1),
                                                      SimClock{0, 0.0})).has_value());
            const auto polled = co_await rig.transport->poll(2, SimClock{1, 200.0});
            EXPECT_TRUE(polled.has_value());
            if (!polled) co_return;
            received = *polled;
        },
        asio::detached);
    rig.io.run();
    ASSERT_EQ(received.size(), 2u);
    EXPECT_EQ(received[0].signal_id, rig.table.find_id("low_signal"));
    EXPECT_EQ(received[1].signal_id, rig.table.find_id("high_signal"));
}

TEST(TestCanTransport, a_slow_consumer_cannot_receive_a_future_delivery) {
    CanRig rig;
    std::vector<PolledValue> early;
    std::vector<PolledValue> ready;
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            EXPECT_TRUE((co_await rig.transport->emit(rig.message("low_signal", 1),
                                                      SimClock{0, 0.0})).has_value());
            EXPECT_TRUE((co_await rig.transport->poll(0, SimClock{1, 100.0})).has_value());
            auto polled = co_await rig.transport->poll(2, SimClock{0, 0.0});
            EXPECT_TRUE(polled.has_value());
            if (!polled) co_return;
            early = *polled;
            polled = co_await rig.transport->poll(2, SimClock{1, 100.0});
            EXPECT_TRUE(polled.has_value());
            if (!polled) co_return;
            ready = *polled;
        },
        asio::detached);
    rig.io.run();
    EXPECT_TRUE(early.empty());
    ASSERT_EQ(ready.size(), 1u);
    EXPECT_EQ(ready[0].signal_id, rig.table.find_id("low_signal"));
}

}  // namespace
}  // namespace relay_host
