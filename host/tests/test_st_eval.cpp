#include <gtest/gtest.h>

#include "harness_helpers.hpp"
#include "relay_host/scan_executor.hpp"
#include "relay_host/st_eval.hpp"
#include "relay_host/st_parser.hpp"
#include "relay_host/st_validator.hpp"

namespace relay_host {
namespace {

struct CompiledBlock {
    SignalTable table;
    ValidatedSt block;
};

CompiledBlock compile(const char* source) {
    const ResolvedTaskSpec spec = testing::minimal_two_plc_spec();
    auto program = parse_st(source);
    EXPECT_TRUE(program.has_value()) << source;
    SignalTable table = build_signal_table(spec, std::span(&*program, 1));
    auto validated = ValidatedSt::try_from(std::move(*program), table, spec.plc_ids);
    EXPECT_TRUE(validated.has_value());
    return CompiledBlock{std::move(table), std::move(*validated)};
}

std::optional<std::uint32_t> slot_of(const ValidatedSt& block, std::string_view name) {
    for (std::uint32_t slot = 0; slot < block.slots().size(); ++slot) {
        if (block.slots()[slot].name == name) {
            return slot;
        }
    }
    return std::nullopt;
}

TEST(TestSTEval, assignment_records_in_assigned_set) {
    CompiledBlock compiled = compile("y := TRUE;");
    STContext ctx(compiled.block);
    ctx.begin_scan();
    ASSERT_TRUE(evaluate(compiled.block, ctx, 10.0).has_value());
    const auto slot = slot_of(compiled.block, "y");
    ASSERT_TRUE(slot.has_value());
    ASSERT_EQ(ctx.assigned_slots().size(), 1u);
    EXPECT_EQ(ctx.assigned_slots()[0], *slot);
    EXPECT_TRUE(is_truthy(ctx.slot_value(*slot)));
}

TEST(TestSTEval, if_block_skips_body_when_condition_false) {
    CompiledBlock compiled = compile("IF gate THEN\ny := TRUE;\nEND_IF;");
    STContext ctx(compiled.block);
    ctx.begin_scan();
    ASSERT_TRUE(evaluate(compiled.block, ctx, 10.0).has_value());
    EXPECT_TRUE(ctx.assigned_slots().empty());
    const auto gate = slot_of(compiled.block, "gate");
    ASSERT_TRUE(gate.has_value());
    ctx.set_slot(*gate, Cell{true});
    ctx.begin_scan();
    ASSERT_TRUE(evaluate(compiled.block, ctx, 10.0).has_value());
    EXPECT_EQ(ctx.assigned_slots().size(), 1u);
}

TEST(TestSTEval, ton_timer_done_after_preset_dt_ms) {
    CompiledBlock compiled =
        compile("t1(IN := gate, PT := T#30ms);\ndone_out := t1.Q;");
    STContext ctx(compiled.block);
    const auto gate = slot_of(compiled.block, "gate");
    const auto done_out = slot_of(compiled.block, "done_out");
    ASSERT_TRUE(gate.has_value());
    ASSERT_TRUE(done_out.has_value());
    ctx.set_slot(*gate, Cell{true});
    for (int scan = 1; scan <= 3; ++scan) {
        ctx.begin_scan();
        ASSERT_TRUE(evaluate(compiled.block, ctx, 10.0).has_value());
        EXPECT_EQ(is_truthy(ctx.slot_value(*done_out)), scan >= 3) << "scan " << scan;
    }
}

TEST(TestSTEval, send_prefix_appears_in_outgoing_not_outputs) {
    CompiledBlock compiled = compile("_send_plc_b_handoff := TRUE;");
    PlcScanState state(0, &compiled.block, compiled.table.size());
    CommBuffer comm(compiled.table.size());
    ScanTraceEntry entry{};
    OutgoingBuffer outgoing;
    ASSERT_FALSE(
        execute_one_scan(state, comm, SimClock::zero(), 10.0, entry, outgoing)
            .has_value());
    EXPECT_EQ(state.output_count, 0u);
    ASSERT_EQ(outgoing.count, 1u);
    EXPECT_EQ(outgoing.items[0].target_plc, 1u);
    EXPECT_EQ(compiled.table.name_of(outgoing.items[0].msg.signal_id), "handoff");
}

TEST(TestSTEval, send_prefix_longest_match) {
    const std::vector<std::string> plc_ids{"plc_b", "plc_bc"};
    const auto longest = parse_send_target("_send_plc_bc_signal", plc_ids);
    ASSERT_TRUE(longest.has_value());
    EXPECT_EQ(longest->target_plc_index, 1u);
    EXPECT_EQ(longest->key, "signal");
    const auto shorter = parse_send_target("_send_plc_b_signal", plc_ids);
    ASSERT_TRUE(shorter.has_value());
    EXPECT_EQ(shorter->target_plc_index, 0u);
    EXPECT_FALSE(parse_send_target("_send_plc_b_", plc_ids).has_value());
    EXPECT_FALSE(parse_send_target("_send_unknown_x", plc_ids).has_value());
}

TEST(TestSTEval, scratch_prefix_suppressed_from_outputs) {
    CompiledBlock compiled = compile("_scratch_latch := TRUE;\nvisible := TRUE;");
    PlcScanState state(0, &compiled.block, compiled.table.size());
    CommBuffer comm(compiled.table.size());
    ScanTraceEntry entry{};
    OutgoingBuffer outgoing;
    ASSERT_FALSE(
        execute_one_scan(state, comm, SimClock::zero(), 10.0, entry, outgoing)
            .has_value());
    ASSERT_EQ(state.output_count, 1u);
    EXPECT_EQ(compiled.table.name_of(state.outputs[0].signal_id), "visible");
}

}  // namespace
}  // namespace relay_host
