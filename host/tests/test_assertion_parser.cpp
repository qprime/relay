#include <gtest/gtest.h>

#include "relay_host/verify/assertion_parser.hpp"
#include "relay_host/verify/verdict.hpp"

namespace relay_host::verify {
namespace {

TEST(TestAssertionParser, test_parses_all_three_forms) {
    const auto eventually = parse_assertion("EVENTUALLY(part_at_b, within: 400ms)");
    ASSERT_TRUE(eventually.has_value());
    EXPECT_EQ(eventually->form, AssertionForm::Eventually);
    EXPECT_EQ(eventually->signals, (std::vector<std::string>{"part_at_b"}));
    EXPECT_EQ(eventually->within_ms, 400.0);

    const auto precedes =
        parse_assertion("PRECEDES(handoff_signal, belt_b_enable, within: 50ms)");
    ASSERT_TRUE(precedes.has_value());
    EXPECT_EQ(precedes->form, AssertionForm::Precedes);
    EXPECT_EQ(precedes->signals,
              (std::vector<std::string>{"handoff_signal", "belt_b_enable"}));
    EXPECT_EQ(precedes->within_ms, 50.0);

    const auto causes = parse_assertion("CAUSES(handoff_signal, belt_b_enable)");
    ASSERT_TRUE(causes.has_value());
    EXPECT_EQ(causes->form, AssertionForm::Causes);
    EXPECT_EQ(causes->signals,
              (std::vector<std::string>{"handoff_signal", "belt_b_enable"}));
    EXPECT_FALSE(causes->within_ms.has_value())
        << "CAUSES takes no budget; it reads no clock on the pass/fail path";
}

TEST(TestAssertionParser, test_is_case_insensitive) {
    EXPECT_TRUE(parse_assertion("eventually(part_at_b, WITHIN: 400MS)").has_value());
    EXPECT_TRUE(parse_assertion("causes(handoff_signal, belt_b_enable)").has_value());
}

TEST(TestAssertionParser, test_tolerates_surrounding_whitespace) {
    const auto parsed = parse_assertion("  EVENTUALLY( part_at_b , within:  400 ms )  ");
    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->signals.front(), "part_at_b");
}

TEST(TestAssertionParser, test_rejects_trailing_text) {
    EXPECT_FALSE(parse_assertion("EVENTUALLY(part_at_b, within: 400ms) and more")
                     .has_value())
        << "the Python parser full-matches; a search would accept a prefix and "
           "silently ignore whatever follows it";
    EXPECT_FALSE(parse_assertion("NOT CAUSES(a, b)").has_value());
}

TEST(TestAssertionParser, test_accepts_fractional_budget) {
    const auto parsed = parse_assertion("PRECEDES(a, b, within: 12.5ms)");
    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->within_ms, 12.5);
}

TEST(TestAssertionParser, test_rejects_a_budget_too_large_for_a_double) {
    const std::string huge(400, '9');
    const std::string text = "EVENTUALLY(part_at_b, within: " + huge + "ms)";
    EXPECT_FALSE(parse_assertion(text).has_value())
        << "std::stod throws out_of_range here and aborts the process; "
           "returning inf instead would make the form pass for any signal that "
           "ever became true";

    Trace trace;
    TraceRecord record;
    record.plc_id = "plc_a";
    record.tick = 0;
    record.elapsed_ms = 0.0;
    record.outputs.emplace("part_at_b", Cell{true});
    trace.records.push_back(record);
    const AssertionResult result = evaluate_assertion(text, trace);
    EXPECT_FALSE(result.passed);
    EXPECT_NE(result.reason.find("unrecognized assertion form"), std::string::npos);
}

TEST(TestAssertionParser, test_accepts_a_large_but_representable_budget) {
    const auto parsed = parse_assertion("EVENTUALLY(part_at_b, within: 1e0ms)");
    EXPECT_FALSE(parsed.has_value()) << "the grammar has no exponent form";
    const auto plain = parse_assertion("EVENTUALLY(part_at_b, within: 100000000000ms)");
    ASSERT_TRUE(plain.has_value());
    EXPECT_EQ(plain->within_ms, 100000000000.0);
}

TEST(TestAssertionParser, test_rejects_non_word_signal_names) {
    EXPECT_FALSE(parse_assertion("EVENTUALLY(part.at.b, within: 400ms)").has_value());
    EXPECT_FALSE(parse_assertion("CAUSES(a, )").has_value());
}

TEST(TestAssertionParser, test_unrecognized_form_yields_failed_result_not_error) {
    const Trace empty;
    const AssertionResult result = evaluate_assertion("NONSENSE(x)", empty);
    EXPECT_FALSE(result.passed);
    EXPECT_EQ(result.assertion, "NONSENSE(x)");
    EXPECT_NE(result.reason.find("unrecognized assertion form"), std::string::npos);
}

TEST(TestAssertionParser, test_unparseable_assertion_reports_the_trimmed_text) {
    const Trace empty;
    const AssertionResult result = evaluate_assertion("  NONSENSE(x)  ", empty);
    EXPECT_EQ(result.assertion, "NONSENSE(x)");
}

}  // namespace
}  // namespace relay_host::verify
