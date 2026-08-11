#include <gtest/gtest.h>

#include <span>
#include <string>
#include <vector>

#include "harness_helpers.hpp"
#include "relay_host/comm_strategy.hpp"
#include "relay_host/st_parser.hpp"

namespace relay_host {
namespace {

constexpr const char* kSendsFromPlcA = "_send_plc_b_handoff_signal := TRUE;";
constexpr const char* kSendsFromPlcB = "_send_plc_a_handoff_signal := TRUE;";
constexpr const char* kReadsOnly = "belt_b_enable := handoff_signal;";

struct CommRig {
    ResolvedTaskSpec spec = testing::minimal_two_plc_spec();
    SignalTable table;
    std::vector<ValidatedSt> blocks;

    CommRig(const char* plc_a_source = kSendsFromPlcA,
            const char* plc_b_source = kReadsOnly) {
        spec.comm.signals = {ResolvedSignal{
            "handoff_signal", "plc_a", {"plc_b"}, std::nullopt, std::nullopt}};
        table.add("handoff_signal");
        for (const char* source : {plc_a_source, plc_b_source}) {
            blocks.push_back(
                *ValidatedSt::try_from(*parse_st(source), table, spec.plc_ids));
        }
    }

    [[nodiscard]] std::expected<void, StrategyError> validate() const {
        return validate_comm_signals(spec, table, blocks);
    }
};

TEST(CommSignalsTest, AcceptsValidProjection) {
    EXPECT_TRUE(CommRig().validate().has_value());
}

TEST(CommSignalsTest, RejectsUnknownProducer) {
    CommRig rig;
    rig.spec.comm.signals[0].produced_by = "plc_z";
    const auto result = rig.validate();
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().message.find("plc_z"), std::string::npos);
    EXPECT_NE(result.error().message.find("produced_by"), std::string::npos);
}

TEST(CommSignalsTest, RejectsUnknownConsumer) {
    CommRig rig;
    rig.spec.comm.signals[0].consumed_by = {"plc_z"};
    const auto result = rig.validate();
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().message.find("plc_z"), std::string::npos);
    EXPECT_NE(result.error().message.find("consumed_by"), std::string::npos);
}

TEST(CommSignalsTest, RejectsSignalMissingFromSignalTable) {
    CommRig rig;
    const SignalTable empty;
    const auto result = validate_comm_signals(rig.spec, empty, rig.blocks);
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().message.find("signal table"), std::string::npos);
}

TEST(CommSignalsTest, AcceptsAddressStrategyName) {
    CommRig rig;
    rig.spec.comm.strategy = "address";
    EXPECT_TRUE(rig.validate().has_value());
}

// The receipt's sender is the one field no medium carries: in-process it is the
// PLC that ran the send slot, over Modbus it is produced_by. A spec and a block
// set that disagree make the same run attribute differently by transport.
TEST(CommSignalsTest, RejectsSendFromAPlcOtherThanTheDeclaredProducer) {
    const CommRig rig(kReadsOnly, kSendsFromPlcB);
    const auto result = rig.validate();
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().message.find("plc_b"), std::string::npos)
        << result.error().message;
    EXPECT_NE(result.error().message.find("produced by 'plc_a'"), std::string::npos)
        << result.error().message;
}

TEST(CommSignalsTest, RejectsSendOfAnUndeclaredCommSignal) {
    CommRig rig;
    rig.spec.comm.signals.clear();
    const auto result = rig.validate();
    ASSERT_FALSE(result.has_value());
    EXPECT_NE(result.error().message.find("does not declare"), std::string::npos)
        << result.error().message;
}

}  // namespace
}  // namespace relay_host
