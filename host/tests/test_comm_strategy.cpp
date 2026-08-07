#include <gtest/gtest.h>

#include "harness_helpers.hpp"
#include "relay_host/comm_strategy.hpp"

namespace relay_host {
namespace {

TEST(TestCommStrategy, unknown_strategy_name_is_startup_error) {
    ResolvedTaskSpec spec = testing::minimal_two_plc_spec();
    spec.comm.strategy = "modbus";
    SignalTable table;
    const auto strategy = build_comm_strategy(spec, table);
    ASSERT_FALSE(strategy.has_value());
    EXPECT_NE(strategy.error().message.find("modbus"), std::string::npos);
    EXPECT_NE(strategy.error().message.find("known: tag"), std::string::npos);
}

}  // namespace
}  // namespace relay_host
