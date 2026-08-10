#include <gtest/gtest.h>

#include <cmath>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include "relay_host/verify/verdict.hpp"
#include "relay_host/verify/verdict_writer.hpp"

namespace relay_host::verify {
namespace {

std::string written(std::span<const AssertionResult> results) {
    std::ostringstream stream;
    EXPECT_TRUE(try_write_json(results, stream).has_value());
    return stream.str();
}

TEST(TestVerdictWriter, test_empty_results_document_shape) {
    const std::vector<AssertionResult> none;
    EXPECT_EQ(written(none),
              "{\n"
              "  \"counts\": {\n"
              "    \"failed\": 0,\n"
              "    \"passed\": 0,\n"
              "    \"total\": 0\n"
              "  },\n"
              "  \"passed\": true,\n"
              "  \"results\": []\n"
              "}\n")
        << "json.dumps(sort_keys=True, indent=2) renders an empty list inline";
}

TEST(TestVerdictWriter, test_result_with_attribution_matches_python_byte_shape) {
    const std::vector<AssertionResult> results{
        AssertionResult{"CAUSES(handoff_signal, belt_b_enable)", true, "because",
                        std::nullopt, std::nullopt,
                        Attribution{"plc_b", 11, "plc_a", 11, 10, 11}}};
    EXPECT_EQ(written(results),
              "{\n"
              "  \"counts\": {\n"
              "    \"failed\": 0,\n"
              "    \"passed\": 1,\n"
              "    \"total\": 1\n"
              "  },\n"
              "  \"passed\": true,\n"
              "  \"results\": [\n"
              "    {\n"
              "      \"assertion\": \"CAUSES(handoff_signal, belt_b_enable)\",\n"
              "      \"attribution\": {\n"
              "        \"cause_received_tick\": 11,\n"
              "        \"cause_sender\": \"plc_a\",\n"
              "        \"cause_sent_tick\": 10,\n"
              "        \"cause_seq\": 11,\n"
              "        \"effect_plc\": \"plc_b\",\n"
              "        \"effect_tick\": 11\n"
              "      },\n"
              "      \"observed_gap_ms\": null,\n"
              "      \"passed\": true,\n"
              "      \"reason\": \"because\",\n"
              "      \"witness_ms\": null\n"
              "    }\n"
              "  ]\n"
              "}\n");
}

TEST(TestVerdictWriter, test_counts_and_top_level_passed_follow_the_results) {
    const std::vector<AssertionResult> results{
        AssertionResult{"a", true, "", std::nullopt, 1.0, std::nullopt},
        AssertionResult{"b", false, "", -2.5, std::nullopt, std::nullopt},
    };
    const std::string document = written(results);
    EXPECT_NE(document.find("\"failed\": 1"), std::string::npos);
    EXPECT_NE(document.find("\"passed\": 1,"), std::string::npos);
    EXPECT_NE(document.find("\"total\": 2"), std::string::npos);
    EXPECT_NE(document.find("\"passed\": false,"), std::string::npos);
    EXPECT_NE(document.find("\"witness_ms\": 1.0"), std::string::npos)
        << "an integral double keeps its trailing .0, as json.dumps writes it";
    EXPECT_NE(document.find("\"observed_gap_ms\": -2.5"), std::string::npos);
}

TEST(TestVerdictWriter, test_rejects_non_finite_milliseconds) {
    for (const double value : {std::numeric_limits<double>::infinity(),
                               std::numeric_limits<double>::quiet_NaN()}) {
        const std::vector<AssertionResult> results{
            AssertionResult{"PRECEDES(a, b, within: 5ms)", false, "", value,
                            std::nullopt, std::nullopt}};
        std::ostringstream stream;
        const auto result = try_write_json(results, stream);
        ASSERT_FALSE(result.has_value());
        EXPECT_NE(result.error().message.find("observed_gap_ms"), std::string::npos);
    }
}

TEST(TestVerdictWriter, test_an_overflowing_gap_reaches_that_guard) {
    // Not a hypothetical branch: elapsed_ms is guarded finite per record, but
    // the difference of two finite extremes is not. Python's _check_ms raises
    // on the same value, so both sides refuse to write it rather than emitting
    // an Infinity literal that json.loads would happily read back.
    Trace trace;
    TraceRecord early;
    early.plc_id = "plc_a";
    early.tick = 0;
    early.elapsed_ms = -1e308;
    early.outputs.emplace("first", Cell{true});
    TraceRecord late;
    late.plc_id = "plc_a";
    late.tick = 1;
    late.elapsed_ms = 1e308;
    late.outputs.emplace("second", Cell{true});
    trace.records.push_back(early);
    trace.records.push_back(late);

    const AssertionResult result =
        evaluate_assertion("PRECEDES(first, second, within: 5ms)", trace);
    ASSERT_TRUE(result.observed_gap_ms.has_value());
    EXPECT_FALSE(std::isfinite(*result.observed_gap_ms));

    std::ostringstream stream;
    const std::vector<AssertionResult> results{result};
    EXPECT_FALSE(try_write_json(results, stream).has_value());
}

}  // namespace
}  // namespace relay_host::verify
