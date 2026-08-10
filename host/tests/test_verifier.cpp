#include <gtest/gtest.h>

#include <algorithm>
#include <string>
#include <vector>

#include "relay_host/verify/verdict.hpp"

namespace relay_host::verify {
namespace {

struct RecordBuilder {
    TraceRecord record;

    RecordBuilder(std::string plc_id, std::int64_t tick, double elapsed_ms) {
        record.plc_id = std::move(plc_id);
        record.tick = tick;
        record.elapsed_ms = elapsed_ms;
    }

    RecordBuilder& input(std::string name, Cell value) {
        record.io_snapshot.emplace(std::move(name), value);
        return *this;
    }

    RecordBuilder& output(std::string name, Cell value) {
        record.outputs.emplace(std::move(name), value);
        return *this;
    }

    RecordBuilder& sent(std::string name, std::int64_t count, Cell value) {
        record.sends.emplace(std::move(name), SendEntry{count, value});
        return *this;
    }

    RecordBuilder& received(std::string name, std::optional<std::string> sender,
                            std::int64_t seq, Cell value) {
        record.recvs.emplace(std::move(name), ReceiptEntry{std::move(sender), seq, value});
        return *this;
    }

    operator TraceRecord() const { return record; }
};

RecordBuilder scan(std::string plc_id, std::int64_t tick, double elapsed_ms) {
    return RecordBuilder(std::move(plc_id), tick, elapsed_ms);
}

// A producer that sends every scan, carrying false until the real event, and a
// consumer that acts on the delivered tag. This is the conveyor's actual shape.
Trace handoff_trace() {
    Trace trace;
    for (std::int64_t tick = 0; tick < 4; ++tick) {
        const bool real = tick >= 2;
        trace.records.push_back(
            scan("plc_a", tick, static_cast<double>(tick) * 10.0)
                .sent("handoff", tick + 1, Cell{real}));
        trace.records.push_back(
            scan("plc_b", tick, static_cast<double>(tick) * 10.0)
                .received("handoff", std::optional<std::string>("plc_a"), tick + 1,
                          Cell{real})
                .output("belt_b", Cell{real}));
    }
    return trace;
}

TEST(TestVerifier, test_comm_tag_resolves_on_producer_not_consumer) {
    // plc_a emits at 20.0ms; plc_b's image only shows the tag once delivered.
    // Resolving through the merged signal view would read the consumer's copy
    // and measure a send→act gap of zero — the pre-#21 defect.
    Trace trace;
    trace.records.push_back(scan("plc_a", 2, 20.0).sent("handoff", 3, Cell{true}));
    trace.records.push_back(scan("plc_b", 3, 30.0)
                                .input("handoff", Cell{true})
                                .output("belt_b", Cell{true}));

    const AssertionResult result =
        evaluate_assertion("PRECEDES(handoff, belt_b, within: 50ms)", trace);
    ASSERT_TRUE(result.passed) << result.reason;
    ASSERT_TRUE(result.observed_gap_ms.has_value());
    EXPECT_EQ(*result.observed_gap_ms, 10.0)
        << "a port that translates only the merged signal lookup reports 0.0ms "
           "here and both verifiers agree on the same wrong number";
}

TEST(TestVerifier, test_comm_tag_anchors_to_first_truthy_send) {
    const Trace trace = handoff_trace();
    const AssertionResult result =
        evaluate_assertion("EVENTUALLY(handoff, within: 100ms)", trace);
    ASSERT_TRUE(result.passed) << result.reason;
    ASSERT_TRUE(result.witness_ms.has_value());
    EXPECT_EQ(*result.witness_ms, 20.0)
        << "binding to the first send of any value would time the run from a "
           "message that said nothing happened";
}

TEST(TestVerifier, test_non_tag_resolves_outputs_before_io_image) {
    Trace trace;
    trace.records.push_back(
        scan("plc_a", 0, 0.0).input("motor", Cell{false}).output("motor", Cell{true}));
    const AssertionResult result =
        evaluate_assertion("EVENTUALLY(motor, within: 10ms)", trace);
    EXPECT_TRUE(result.passed) << result.reason;
    EXPECT_EQ(*result.witness_ms, 0.0);
}

TEST(TestVerifier, test_first_true_selects_minimum_elapsed_not_first_record) {
    Trace trace;
    trace.records.push_back(scan("plc_b", 5, 50.0).output("alarm", Cell{true}));
    trace.records.push_back(scan("plc_a", 2, 20.0).output("alarm", Cell{true}));
    const AssertionResult result =
        evaluate_assertion("EVENTUALLY(alarm, within: 30ms)", trace);
    ASSERT_TRUE(result.passed)
        << "first-in-list-order names a property of the container, not of the "
           "trace: " << result.reason;
    EXPECT_EQ(*result.witness_ms, 20.0);
}

TEST(TestVerifier, test_same_instant_on_two_plcs_breaks_the_tie_on_plc_id) {
    Trace forward;
    forward.records.push_back(scan("plc_b", 1, 10.0).output("alarm", Cell{true}));
    forward.records.push_back(scan("plc_a", 1, 10.0).output("alarm", Cell{true}));
    Trace reversed;
    reversed.records.push_back(forward.records[1]);
    reversed.records.push_back(forward.records[0]);

    const AssertionResult a = evaluate_assertion("CAUSES(tag, alarm)", forward);
    const AssertionResult b = evaluate_assertion("CAUSES(tag, alarm)", reversed);
    EXPECT_EQ(a.reason, b.reason)
        << "both traces name plc_a as the acting PLC; the tie-break is content, "
           "not append order";
    EXPECT_NE(a.reason.find("plc_a"), std::string::npos);
}

TEST(TestVerifier, test_verdicts_stable_under_record_permutation) {
    const Trace trace = handoff_trace();
    const std::vector<std::string> assertions{
        "EVENTUALLY(belt_b, within: 100ms)",
        "PRECEDES(handoff, belt_b, within: 50ms)",
        "CAUSES(handoff, belt_b)",
    };
    const std::vector<AssertionResult> baseline = evaluate_all(assertions, trace);

    std::vector<std::size_t> order(trace.records.size());
    for (std::size_t i = 0; i < order.size(); ++i) {
        order[i] = i;
    }
    std::reverse(order.begin(), order.end());
    Trace shuffled;
    for (const std::size_t index : order) {
        shuffled.records.push_back(trace.records[index]);
    }

    const std::vector<AssertionResult> outcome = evaluate_all(assertions, shuffled);
    ASSERT_EQ(outcome.size(), baseline.size());
    for (std::size_t i = 0; i < outcome.size(); ++i) {
        EXPECT_EQ(outcome[i].passed, baseline[i].passed) << assertions[i];
        EXPECT_EQ(outcome[i].reason, baseline[i].reason) << assertions[i];
        EXPECT_EQ(outcome[i].witness_ms, baseline[i].witness_ms) << assertions[i];
        EXPECT_EQ(outcome[i].observed_gap_ms, baseline[i].observed_gap_ms)
            << assertions[i];
    }
}

TEST(TestVerifier, test_precedes_same_scan_passes) {
    Trace trace;
    trace.records.push_back(
        scan("plc_a", 1, 10.0).output("first", Cell{true}).output("second", Cell{true}));
    const AssertionResult result =
        evaluate_assertion("PRECEDES(first, second, within: 50ms)", trace);
    EXPECT_TRUE(result.passed)
        << "within one scan there is no observable ordering; a strict rule makes "
           "the form unsatisfiable exactly where it is most wanted: "
        << result.reason;
    EXPECT_EQ(*result.observed_gap_ms, 0.0);
}

TEST(TestVerifier, test_precedes_reversed_reports_ordering_not_budget) {
    Trace trace;
    trace.records.push_back(scan("plc_a", 1, 10.0).output("second", Cell{true}));
    trace.records.push_back(scan("plc_a", 5, 50.0).output("first", Cell{true}));
    const AssertionResult result =
        evaluate_assertion("PRECEDES(first, second, within: 500ms)", trace);
    ASSERT_FALSE(result.passed);
    EXPECT_NE(result.reason.find("preceded"), std::string::npos)
        << "reporting a budget overrun for a reversed pair would mislead: "
        << result.reason;
    EXPECT_EQ(*result.observed_gap_ms, -40.0);
}

TEST(TestVerifier, test_precedes_reports_gap_on_pass_and_fail) {
    Trace tight;
    tight.records.push_back(scan("plc_a", 0, 0.0).output("first", Cell{true}));
    tight.records.push_back(scan("plc_a", 1, 10.0).output("second", Cell{true}));
    const AssertionResult passing =
        evaluate_assertion("PRECEDES(first, second, within: 50ms)", tight);
    ASSERT_TRUE(passing.passed);
    EXPECT_EQ(*passing.observed_gap_ms, 10.0);

    const AssertionResult failing =
        evaluate_assertion("PRECEDES(first, second, within: 5ms)", tight);
    ASSERT_FALSE(failing.passed);
    EXPECT_EQ(*failing.observed_gap_ms, 10.0)
        << "budgets are derived from measurement, so the gap is reported on "
           "every evaluation where both signals became true";
}

TEST(TestVerifier, test_eventually_reports_witness_past_budget) {
    Trace trace;
    trace.records.push_back(scan("plc_a", 9, 90.0).output("part", Cell{true}));
    const AssertionResult result =
        evaluate_assertion("EVENTUALLY(part, within: 50ms)", trace);
    ASSERT_FALSE(result.passed);
    ASSERT_TRUE(result.witness_ms.has_value());
    EXPECT_EQ(*result.witness_ms, 90.0);
}

TEST(TestVerifier, test_eventually_on_empty_trace_never_true) {
    const Trace empty;
    const AssertionResult result =
        evaluate_assertion("EVENTUALLY(part, within: 50ms)", empty);
    EXPECT_FALSE(result.passed);
    EXPECT_FALSE(result.witness_ms.has_value());
    EXPECT_NE(result.reason.find("never true"), std::string::npos);
}

TEST(TestVerifier, test_causes_populates_attribution) {
    const Trace trace = handoff_trace();
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", trace);
    ASSERT_TRUE(result.passed) << result.reason;
    ASSERT_TRUE(result.attribution.has_value());
    EXPECT_EQ(result.attribution->effect_plc, "plc_b");
    EXPECT_EQ(result.attribution->effect_tick, 2);
    EXPECT_EQ(result.attribution->cause_sender, "plc_a");
    EXPECT_EQ(result.attribution->cause_seq, 3);
    EXPECT_EQ(result.attribution->cause_sent_tick, 2);
    EXPECT_EQ(result.attribution->cause_received_tick, 2);
}

TEST(TestVerifier, test_causes_rejects_output_shadowing_false_delivery) {
    // plc_b writes an output named for the tag while every delivery carried
    // false. The merged signal view would read the output and manufacture an
    // activation; the receipt says nothing was delivered.
    Trace trace;
    trace.records.push_back(scan("plc_a", 0, 0.0).sent("handoff", 1, Cell{false}));
    trace.records.push_back(scan("plc_b", 0, 0.0)
                                .received("handoff", std::optional<std::string>("plc_a"),
                                          1, Cell{false})
                                .output("handoff", Cell{true})
                                .output("belt_b", Cell{true}));
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", trace);
    EXPECT_FALSE(result.passed) << result.reason;
    EXPECT_NE(result.reason.find("false value"), std::string::npos);
    EXPECT_FALSE(result.attribution.has_value());
}

TEST(TestVerifier, test_causes_rejects_overlapping_sender_seq_spaces) {
    // plc_c sends its own key 'handoff' with seq 1 but never to plc_b. The
    // receipt names plc_a as the sender, and plc_a recorded no such send.
    Trace trace;
    trace.records.push_back(scan("plc_c", 0, 0.0).sent("handoff", 1, Cell{true}));
    trace.records.push_back(scan("plc_a", 0, 0.0).output("idle", Cell{true}));
    trace.records.push_back(scan("plc_b", 1, 10.0)
                                .received("handoff", std::optional<std::string>("plc_a"),
                                          1, Cell{true})
                                .output("belt_b", Cell{true}));
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", trace);
    ASSERT_FALSE(result.passed)
        << "identity comes from receipt.sender; searching every PLC for a "
           "matching count lands on one that never sent to this consumer";
    EXPECT_NE(result.reason.find("recorded no such send"), std::string::npos);
}

TEST(TestVerifier, test_causes_rejects_all_false_receipts) {
    Trace trace;
    trace.records.push_back(scan("plc_a", 0, 0.0).sent("handoff", 1, Cell{false}));
    trace.records.push_back(scan("plc_b", 0, 0.0)
                                .received("handoff", std::optional<std::string>("plc_a"),
                                          1, Cell{false})
                                .output("belt_b", Cell{true}));
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", trace);
    EXPECT_FALSE(result.passed);
    EXPECT_NE(result.reason.find("false value"), std::string::npos);
}

TEST(TestVerifier, test_causes_rejects_null_sender_as_unattributable) {
    Trace trace;
    trace.records.push_back(scan("plc_a", 0, 0.0).sent("handoff", 1, Cell{true}));
    trace.records.push_back(scan("plc_b", 0, 0.0)
                                .received("handoff", std::nullopt, 1, Cell{true})
                                .output("belt_b", Cell{true}));
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", trace);
    ASSERT_FALSE(result.passed);
    EXPECT_NE(result.reason.find("no sender"), std::string::npos);
    EXPECT_FALSE(result.attribution.has_value());
}

TEST(TestVerifier, test_causes_never_received_names_the_plc) {
    Trace trace;
    trace.records.push_back(scan("plc_a", 0, 0.0).sent("handoff", 1, Cell{true}));
    trace.records.push_back(scan("plc_b", 0, 0.0).output("belt_b", Cell{true}));
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", trace);
    ASSERT_FALSE(result.passed);
    EXPECT_NE(result.reason.find("never received there"), std::string::npos);
}

TEST(TestVerifier, test_causes_effect_before_receipt_fails) {
    Trace trace;
    trace.records.push_back(scan("plc_a", 3, 30.0).sent("handoff", 1, Cell{true}));
    trace.records.push_back(scan("plc_b", 0, 0.0).output("belt_b", Cell{true}));
    trace.records.push_back(scan("plc_b", 4, 40.0)
                                .received("handoff", std::optional<std::string>("plc_a"),
                                          1, Cell{true}));
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", trace);
    ASSERT_FALSE(result.passed);
    EXPECT_NE(result.reason.find("before the first activating"), std::string::npos);
}

TEST(TestVerifier, test_causes_same_scan_receipt_and_action_passes) {
    Trace trace;
    trace.records.push_back(scan("plc_a", 1, 10.0).sent("handoff", 1, Cell{true}));
    trace.records.push_back(scan("plc_b", 1, 10.0)
                                .received("handoff", std::optional<std::string>("plc_a"),
                                          1, Cell{true})
                                .output("belt_b", Cell{true}));
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", trace);
    EXPECT_TRUE(result.passed)
        << "promotion precedes execution within a scan, so arriving and being "
           "acted on in one scan is a causal chain: "
        << result.reason;
}

TEST(TestVerifier, test_causes_matches_high_water_count_not_equality) {
    // A tag emitting to two consumers in one scan stores only the last count,
    // so a receipt's seq can be lower than the send record's count.
    Trace trace;
    trace.records.push_back(scan("plc_a", 1, 10.0).sent("handoff", 9, Cell{true}));
    trace.records.push_back(scan("plc_b", 1, 10.0)
                                .received("handoff", std::optional<std::string>("plc_a"),
                                          8, Cell{true})
                                .output("belt_b", Cell{true}));
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", trace);
    ASSERT_TRUE(result.passed) << result.reason;
    EXPECT_EQ(result.attribution->cause_seq, 8);
}

TEST(TestVerifier, test_causes_on_empty_trace_reports_effect_never_true) {
    const Trace empty;
    const AssertionResult result = evaluate_assertion("CAUSES(handoff, belt_b)", empty);
    EXPECT_FALSE(result.passed);
    EXPECT_NE(result.reason.find("never became true"), std::string::npos);
}

TEST(TestVerifier, test_evaluate_all_preserves_assertion_order) {
    const Trace trace = handoff_trace();
    const std::vector<std::string> assertions{"CAUSES(handoff, belt_b)",
                                              "EVENTUALLY(belt_b, within: 100ms)"};
    const std::vector<AssertionResult> results = evaluate_all(assertions, trace);
    ASSERT_EQ(results.size(), 2u);
    EXPECT_EQ(results[0].assertion, assertions[0]);
    EXPECT_EQ(results[1].assertion, assertions[1]);
}

}  // namespace
}  // namespace relay_host::verify
