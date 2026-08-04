#include <gtest/gtest.h>

#include "harness_helpers.hpp"
#include "relay_host/st_parser.hpp"
#include "relay_host/st_validator.hpp"

namespace relay_host {
namespace {

TEST(TestSTParser, parses_conveyor_plc_a) {
    const auto program = parse_st(testing::kConveyorPlcA);
    ASSERT_TRUE(program.has_value());
    ASSERT_EQ(program->statements.size(), 4u);
    const Assignment* edge = std::get_if<Assignment>(&program->statements[0]);
    ASSERT_NE(edge, nullptr);
    EXPECT_EQ(edge->target, "_scratch_edge_handoff_on_exit");
    EXPECT_NE(std::get_if<IfBlock>(&program->statements[2]), nullptr);
    const Assignment* send = std::get_if<Assignment>(&program->statements[3]);
    ASSERT_NE(send, nullptr);
    EXPECT_EQ(send->target, "_send_plc_b_handoff_signal");
}

TEST(TestSTParser, parses_conveyor_plc_b) {
    const auto program = parse_st(testing::kConveyorPlcB);
    ASSERT_TRUE(program.has_value());
    ASSERT_EQ(program->statements.size(), 2u);
    const IfBlock* latch = std::get_if<IfBlock>(&program->statements[0]);
    ASSERT_NE(latch, nullptr);
    ASSERT_EQ(latch->body.size(), 1u);
    const Assignment* output = std::get_if<Assignment>(&program->statements[1]);
    ASSERT_NE(output, nullptr);
    EXPECT_EQ(output->target, "belt_b_enable");
}

TEST(TestSTParser, empty_source_produces_empty_validated_st) {
    auto program = parse_st("");
    ASSERT_TRUE(program.has_value());
    EXPECT_TRUE(program->statements.empty());
    SignalTable table;
    const std::vector<std::string> plc_ids{"plc_a"};
    const auto validated = ValidatedSt::try_from(std::move(*program), table, plc_ids);
    ASSERT_TRUE(validated.has_value());
    EXPECT_TRUE(validated->slots().empty());
}

TEST(TestSTParser, unterminated_if_returns_parse_error) {
    const auto program = parse_st("IF x THEN\ny := TRUE;\n");
    ASSERT_FALSE(program.has_value());
    EXPECT_NE(program.error().message.find("END_IF"), std::string::npos);
}

TEST(TestSTParser, unknown_keyword_returns_parse_error) {
    const auto program = parse_st("WHILE x DO y := TRUE; END_WHILE;");
    ASSERT_FALSE(program.has_value());
    EXPECT_FALSE(program.error().message.empty());
}

}  // namespace
}  // namespace relay_host
