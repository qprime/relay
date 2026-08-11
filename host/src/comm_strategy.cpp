#include "relay_host/comm_strategy.hpp"

#include <algorithm>
#include <span>

namespace relay_host {

namespace {

bool declared(std::span<const std::string> plc_ids, const std::string& plc_id) {
    return std::find(plc_ids.begin(), plc_ids.end(), plc_id) != plc_ids.end();
}

// The receipt's sender is the one field no medium carries. In-process it comes
// from the PLC that ran the send slot; over Modbus it comes from produced_by.
// The two agree only if the resolved spec and the ST blocks agree, and the host
// loads them as two independent documents.
std::expected<void, StrategyError> validate_send_ownership(
    const ResolvedTaskSpec& spec, const SignalTable& table,
    std::span<const ValidatedSt> blocks) {
    for (std::size_t index = 0; index < blocks.size(); ++index) {
        for (const SlotBinding& binding : blocks[index].slots()) {
            if (binding.kind != SlotKind::Send) {
                continue;
            }
            if (binding.signal_id >= table.size()) {
                return std::unexpected(StrategyError{
                    "comm_signals: '" + spec.plc_ids[index] + "' sends via '" +
                    binding.name +
                    "', whose signal id is not in this signal table; the blocks and "
                    "the table must be built from the same parsed programs"});
            }
            const std::string& key = table.name_of(binding.signal_id);
            const auto declared_signal = std::find_if(
                spec.comm.signals.begin(), spec.comm.signals.end(),
                [&](const ResolvedSignal& signal) { return signal.name == key; });
            if (declared_signal == spec.comm.signals.end()) {
                return std::unexpected(StrategyError{
                    "comm_signals: '" + spec.plc_ids[index] + "' sends '" + key +
                    "', which the resolved spec does not declare as a comm signal"});
            }
            if (declared_signal->produced_by != spec.plc_ids[index]) {
                return std::unexpected(StrategyError{
                    "comm_signals: '" + spec.plc_ids[index] + "' sends '" + key +
                    "', but the resolved spec declares it produced by '" +
                    declared_signal->produced_by +
                    "'; a receipt's sender is read from produced_by over a medium "
                    "that carries no sender, so the two must agree"});
            }
        }
    }
    return {};
}

}  // namespace

std::expected<void, StrategyError> validate_comm_signals(
    const ResolvedTaskSpec& spec, const SignalTable& table,
    std::span<const ValidatedSt> blocks) {
    for (const ResolvedSignal& signal : spec.comm.signals) {
        if (signal.name.empty() || signal.produced_by.empty()) {
            continue;
        }
        if (!declared(spec.plc_ids, signal.produced_by)) {
            return std::unexpected(StrategyError{
                "comm_signals: signal '" + signal.name + "' produced_by '" +
                signal.produced_by + "' is not a declared plc_id"});
        }
        if (!table.find_id(signal.name)) {
            return std::unexpected(StrategyError{
                "comm_signals: signal '" + signal.name +
                "' is not in the signal table; comm signals must be registered at startup"});
        }
        for (const std::string& consumer : signal.consumed_by) {
            if (!declared(spec.plc_ids, consumer)) {
                return std::unexpected(StrategyError{
                    "comm_signals: signal '" + signal.name + "' consumed_by '" + consumer +
                    "' is not a declared plc_id"});
            }
        }
    }
    return validate_send_ownership(spec, table, blocks);
}

}  // namespace relay_host
