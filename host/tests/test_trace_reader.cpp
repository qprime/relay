#include <gtest/gtest.h>

#include <fstream>
#include <sstream>
#include <string>

#include "relay_host/trace.hpp"
#include "relay_host/verify/trace_reader.hpp"

namespace relay_host::verify {
namespace {

std::string read_line() {
    return
        R"({"elapsed_ms": 10.0, "io_snapshot": {"sensor": true}, "outputs": {"motor": 2.5})"
        R"(, "plc_id": "plc_a", "recvs": {"tag": {"sender": "plc_b", "seq": 3, "value": true}})"
        R"(, "sends": {"tag": {"count": 4, "value": false}}, "tick": 1})";
}

std::expected<Trace, ReadError> read(const std::string& text) {
    std::istringstream stream(text);
    return try_read_jsonl(stream);
}

TEST(TestTraceReader, test_reads_every_field_of_a_record) {
    const auto trace = read(read_line() + "\n");
    ASSERT_TRUE(trace.has_value()) << trace.error().message;
    ASSERT_EQ(trace->records.size(), 1u);
    const TraceRecord& record = trace->records.front();
    EXPECT_EQ(record.plc_id, "plc_a");
    EXPECT_EQ(record.tick, 1);
    EXPECT_EQ(record.elapsed_ms, 10.0);
    EXPECT_TRUE(is_truthy(record.io_snapshot.at("sensor")));
    EXPECT_EQ(std::get<double>(record.outputs.at("motor")), 2.5);
    EXPECT_EQ(record.sends.at("tag").count, 4);
    EXPECT_FALSE(is_truthy(record.sends.at("tag").value));
    ASSERT_TRUE(record.recvs.at("tag").sender.has_value());
    EXPECT_EQ(*record.recvs.at("tag").sender, "plc_b");
    EXPECT_EQ(record.recvs.at("tag").seq, 3);
}

TEST(TestTraceReader, test_round_trips_a_host_dump) {
    SignalTable table;
    table.add("sensor");
    table.add("motor");
    const std::vector<std::string> plc_ids{"plc_a", "plc_b"};

    TraceRing ring(4);
    ScanTraceEntry& entry = ring.next_entry();
    entry.plc_index = 0;
    entry.clock = SimClock{7, 70.0};
    entry.input_count = 1;
    entry.input_cells[0] = CellSlot{0, Cell{true}};
    entry.output_count = 1;
    entry.output_cells[0] = CellSlot{1, Cell{std::int64_t{3}}};
    entry.send_count = 1;
    entry.send_slots[0] = SeqSlot{1, 11, Cell{true}};
    entry.recv_count = 1;
    entry.recv_slots[0] = ReceiptSlot{0, 1, 11, Cell{true}};

    std::ostringstream dumped;
    ASSERT_TRUE(ring.dump_to_jsonl(dumped, table, plc_ids).has_value());

    const auto trace = read(dumped.str());
    ASSERT_TRUE(trace.has_value()) << trace.error().message;
    ASSERT_EQ(trace->records.size(), 1u);
    const TraceRecord& record = trace->records.front();
    EXPECT_EQ(record.plc_id, "plc_a");
    EXPECT_EQ(record.tick, 7);
    EXPECT_EQ(record.elapsed_ms, 70.0);
    EXPECT_EQ(std::get<std::int64_t>(record.outputs.at("motor")), 3);
    EXPECT_EQ(record.sends.at("motor").count, 11)
        << "the dump writes SeqSlot::count under the JSON key 'count'; a reader "
           "that expects 'seq' there silently loses every send";
    ASSERT_TRUE(record.recvs.at("sensor").sender.has_value());
    EXPECT_EQ(*record.recvs.at("sensor").sender, "plc_b");
}

TEST(TestTraceReader, test_reads_null_sender_as_absent) {
    SignalTable table;
    table.add("sensor");
    const std::vector<std::string> plc_ids{"plc_a"};
    TraceRing ring(2);
    ScanTraceEntry& entry = ring.next_entry();
    entry.plc_index = 0;
    entry.clock = SimClock{0, 0.0};
    entry.recv_count = 1;
    entry.recv_slots[0] = ReceiptSlot{0, kNoSender, 1, Cell{true}};
    std::ostringstream dumped;
    ASSERT_TRUE(ring.dump_to_jsonl(dumped, table, plc_ids).has_value());

    const auto trace = read(dumped.str());
    ASSERT_TRUE(trace.has_value()) << trace.error().message;
    EXPECT_FALSE(trace->records.front().recvs.at("sensor").sender.has_value())
        << "plant-routed and strategy-routed messages record no sender and are "
           "unattributable by construction";
}

TEST(TestTraceReader, test_reads_committed_sim_trace) {
    const std::string path =
        std::string(RELAY_REPO_ROOT) + "/tests/golden/conveyor_trace.jsonl";
    std::ifstream stream(path);
    ASSERT_TRUE(stream) << "cannot open " << path;
    const auto trace = try_read_jsonl(stream);
    ASSERT_TRUE(trace.has_value()) << trace.error().message;
    EXPECT_GT(trace->records.size(), 0u);
    bool saw_send = false;
    for (const TraceRecord& record : trace->records) {
        saw_send = saw_send || !record.sends.empty();
    }
    EXPECT_TRUE(saw_send) << "the conveyor trace carries handoff_signal sends; a "
                             "reader that drops them cannot resolve a comm tag";
}

TEST(TestTraceReader, test_skips_blank_lines) {
    const auto trace = read("\n" + read_line() + "\n\n   \n");
    ASSERT_TRUE(trace.has_value()) << trace.error().message;
    EXPECT_EQ(trace->records.size(), 1u);
}

TEST(TestTraceReader, test_rejects_non_object_line) {
    const auto trace = read("[1, 2, 3]\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("not an object"), std::string::npos);
}

TEST(TestTraceReader, test_rejects_malformed_json) {
    const auto trace = read("{not json\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("malformed JSON"), std::string::npos);
}

TEST(TestTraceReader, test_rejects_missing_key_naming_it) {
    const auto trace = read(R"({"tick": 1, "elapsed_ms": 0.0, "io_snapshot": {})"
                            R"(, "outputs": {}, "sends": {}, "recvs": {}})"
                            "\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("plc_id"), std::string::npos);
    EXPECT_NE(trace.error().message.find("missing required key"), std::string::npos);
}

TEST(TestTraceReader, test_rejects_string_signal_value) {
    const auto trace = read(R"({"elapsed_ms": 0.0, "io_snapshot": {"sensor": "true"})"
                            R"(, "outputs": {}, "plc_id": "plc_a", "recvs": {})"
                            R"(, "sends": {}, "tick": 0})"
                            "\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("sensor"), std::string::npos);
    EXPECT_NE(trace.error().message.find("io_snapshot"), std::string::npos);
}

TEST(TestTraceReader, test_rejects_non_finite_numbers) {
    // Python's json.loads accepts the NaN/Infinity literals, which is why
    // relay/trace_io.py carries an explicit finiteness test. This reader gets
    // the same rejection from the parser, so the property is pinned here rather
    // than duplicated as an unreachable branch in read_cell.
    for (const std::string_view literal : {"NaN", "Infinity", "-Infinity", "1e400"}) {
        const std::string line =
            std::string(R"({"elapsed_ms": )") + std::string(literal) +
            R"(, "io_snapshot": {}, "outputs": {}, "plc_id": "plc_a", "recvs": {})"
            R"(, "sends": {}, "tick": 0})" + "\n";
        const auto trace = read(line);
        ASSERT_FALSE(trace.has_value()) << literal;
        EXPECT_NE(trace.error().message.find("line 1"), std::string::npos) << literal;
    }
    for (const std::string_view literal : {"NaN", "1e400"}) {
        const std::string line =
            std::string(R"({"elapsed_ms": 0.0, "io_snapshot": {"sensor": )") +
            std::string(literal) +
            R"(}, "outputs": {}, "plc_id": "plc_a", "recvs": {}, "sends": {}, "tick": 0})" +
            "\n";
        const auto trace = read(line);
        ASSERT_FALSE(trace.has_value()) << literal;
    }
}

TEST(TestTraceReader, test_rejects_bool_where_counter_expected) {
    const auto trace = read(R"({"elapsed_ms": 0.0, "io_snapshot": {}, "outputs": {})"
                            R"(, "plc_id": "plc_a", "recvs": {})"
                            R"(, "sends": {"tag": {"count": true, "value": true}})"
                            R"(, "tick": 0})"
                            "\n");
    ASSERT_FALSE(trace.has_value())
        << "bool is not an int: the signal-value predicate would admit true here "
           "and a later cast would turn it into the count 1";
    EXPECT_NE(trace.error().message.find("counter"), std::string::npos);
}

TEST(TestTraceReader, test_rejects_float_where_counter_expected) {
    const auto trace = read(R"({"elapsed_ms": 0.0, "io_snapshot": {}, "outputs": {})"
                            R"(, "plc_id": "plc_a")"
                            R"(, "recvs": {"tag": {"sender": null, "seq": 1.5, "value": true}})"
                            R"(, "sends": {}, "tick": 0})"
                            "\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("counter"), std::string::npos);
}

TEST(TestTraceReader, test_rejects_sender_of_wrong_type) {
    const auto trace = read(R"({"elapsed_ms": 0.0, "io_snapshot": {}, "outputs": {})"
                            R"(, "plc_id": "plc_a")"
                            R"(, "recvs": {"tag": {"sender": 7, "seq": 1, "value": true}})"
                            R"(, "sends": {}, "tick": 0})"
                            "\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("sender"), std::string::npos);
}

TEST(TestTraceReader, test_rejects_non_object_sends_block) {
    const auto trace = read(R"({"elapsed_ms": 0.0, "io_snapshot": {}, "outputs": {})"
                            R"(, "plc_id": "plc_a", "recvs": {}, "sends": [], "tick": 0})"
                            "\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("sends"), std::string::npos);
}

TEST(TestTraceReader, test_rejects_non_object_sends_entry) {
    const auto trace = read(R"({"elapsed_ms": 0.0, "io_snapshot": {}, "outputs": {})"
                            R"(, "plc_id": "plc_a", "recvs": {}, "sends": {"tag": 4})"
                            R"(, "tick": 0})"
                            "\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("sends entry"), std::string::npos);
}

TEST(TestTraceReader, test_rejects_non_string_plc_id) {
    const auto trace = read(R"({"elapsed_ms": 0.0, "io_snapshot": {}, "outputs": {})"
                            R"(, "plc_id": 7, "recvs": {}, "sends": {}, "tick": 0})"
                            "\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("plc_id"), std::string::npos);
}

TEST(TestTraceReader, test_error_message_carries_line_number) {
    const auto trace = read(read_line() + "\n" + read_line() + "\n[]\n");
    ASSERT_FALSE(trace.has_value());
    EXPECT_NE(trace.error().message.find("line 3"), std::string::npos)
        << "'expected str' without a position is not actionable against a "
           "thousand-line trace";
}

TEST(TestTraceReader, test_empty_stream_reads_as_empty_trace) {
    const auto trace = read("");
    ASSERT_TRUE(trace.has_value());
    EXPECT_TRUE(trace->records.empty());
}

}  // namespace
}  // namespace relay_host::verify
