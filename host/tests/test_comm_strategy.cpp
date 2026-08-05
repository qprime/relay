#include <gtest/gtest.h>

#include "harness_helpers.hpp"
#include "relay_host/comm_strategy.hpp"

namespace relay_host {
namespace {

struct StrategyFixture {
    SignalTable table;
    TagStrategy strategy;
};

StrategyFixture make_strategy(std::vector<ResolvedTag> tags,
                              std::vector<std::string> plc_ids) {
    SignalTable table;
    for (const ResolvedTag& tag : tags) {
        table.add(tag.name);
    }
    ResolvedComm comm{"tag", std::move(tags)};
    auto strategy = TagStrategy::try_create(comm, table, plc_ids);
    EXPECT_TRUE(strategy.has_value());
    return StrategyFixture{std::move(table), std::move(*strategy)};
}

TEST(TestCommStrategy, emits_on_change_only) {
    StrategyFixture fixture = make_strategy(
        {ResolvedTag{"handoff", "plc_a", {"plc_b"}}}, {"plc_a", "plc_b"});
    const IOImage prior = IOImage::empty().with_value("handoff", Cell{true});
    const IOImage unchanged = IOImage::empty().with_value("handoff", Cell{true});
    EXPECT_TRUE(fixture.strategy.route(0, unchanged, prior).empty());
    const IOImage changed = IOImage::empty().with_value("handoff", Cell{false});
    EXPECT_EQ(fixture.strategy.route(0, changed, prior).size(), 1u);
}

TEST(TestCommStrategy, emits_to_every_declared_consumer) {
    StrategyFixture fixture = make_strategy(
        {ResolvedTag{"handoff", "plc_a", {"plc_b", "plc_c"}}},
        {"plc_a", "plc_b", "plc_c"});
    const IOImage outputs = IOImage::empty().with_value("handoff", Cell{true});
    const auto routed = fixture.strategy.route(0, outputs, IOImage::empty());
    ASSERT_EQ(routed.size(), 2u);
    EXPECT_EQ(routed[0].consumer_index, 1u);
    EXPECT_EQ(routed[1].consumer_index, 2u);
}

TEST(TestCommStrategy, missing_key_both_sides_emits_nothing) {
    StrategyFixture fixture = make_strategy(
        {ResolvedTag{"handoff", "plc_a", {"plc_b"}}}, {"plc_a", "plc_b"});
    EXPECT_TRUE(fixture.strategy.route(0, IOImage::empty(), IOImage::empty()).empty());
}

TEST(TestCommStrategy, absent_to_present_emits) {
    StrategyFixture fixture = make_strategy(
        {ResolvedTag{"handoff", "plc_a", {"plc_b"}}}, {"plc_a", "plc_b"});
    const IOImage outputs = IOImage::empty().with_value("handoff", Cell{false});
    const auto routed = fixture.strategy.route(0, outputs, IOImage::empty());
    ASSERT_EQ(routed.size(), 1u);
    EXPECT_EQ(std::get<bool>(routed[0].value), false);
}

TEST(TestCommStrategy, present_to_absent_emits_nothing) {
    StrategyFixture fixture = make_strategy(
        {ResolvedTag{"handoff", "plc_a", {"plc_b"}}}, {"plc_a", "plc_b"});
    const IOImage prior = IOImage::empty().with_value("handoff", Cell{true});
    EXPECT_TRUE(fixture.strategy.route(0, IOImage::empty(), prior).empty())
        << "a retracted tag must not emit; matches TagStrategy.route in the "
           "Python oracle";
}

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
