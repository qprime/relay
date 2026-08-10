#include <gtest/gtest.h>

#include "harness_helpers.hpp"
#include "relay_host/comm_strategy.hpp"

namespace relay_host {
namespace {

ResolvedTaskSpec spec_with_signal() {
    ResolvedTaskSpec spec = testing::minimal_two_plc_spec();
    spec.comm.signals = {ResolvedSignal{"handoff_signal", "plc_a", {"plc_b"}}};
    return spec;
}

SignalTable table_with_signal() {
    SignalTable table;
    table.add("handoff_signal");
    return table;
}

TEST(CommSignalsTest, AcceptsValidProjection) {
    const auto result = validate_comm_signals(spec_with_signal(), table_with_signal());
    EXPECT_TRUE(result.has_value());
}

TEST(CommSignalsTest, RejectsUnknownProducer) {
    ResolvedTaskSpec spec = spec_with_signal();
    spec.comm.signals[0].produced_by = "plc_z";
    const auto result = validate_comm_signals(spec, table_with_signal());
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().message.find("plc_z"), std::string::npos);
    EXPECT_NE(result.error().message.find("produced_by"), std::string::npos);
}

TEST(CommSignalsTest, RejectsUnknownConsumer) {
    ResolvedTaskSpec spec = spec_with_signal();
    spec.comm.signals[0].consumed_by = {"plc_z"};
    const auto result = validate_comm_signals(spec, table_with_signal());
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().message.find("plc_z"), std::string::npos);
    EXPECT_NE(result.error().message.find("consumed_by"), std::string::npos);
}

TEST(CommSignalsTest, RejectsSignalMissingFromSignalTable) {
    SignalTable empty;
    const auto result = validate_comm_signals(spec_with_signal(), empty);
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().message.find("signal table"), std::string::npos);
}

TEST(CommSignalsTest, AcceptsAddressStrategyName) {
    ResolvedTaskSpec spec = spec_with_signal();
    spec.comm.strategy = "address";
    const auto result = validate_comm_signals(spec, table_with_signal());
    EXPECT_TRUE(result.has_value());
}

}  // namespace
}  // namespace relay_host
