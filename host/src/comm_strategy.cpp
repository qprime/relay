#include "relay_host/comm_strategy.hpp"

#include <algorithm>
#include <span>

namespace relay_host {

namespace {

bool declared(std::span<const std::string> plc_ids, const std::string& plc_id) {
    return std::find(plc_ids.begin(), plc_ids.end(), plc_id) != plc_ids.end();
}

}  // namespace

std::expected<void, StrategyError> validate_comm_signals(const ResolvedTaskSpec& spec,
                                                         const SignalTable& table) {
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
    return {};
}

}  // namespace relay_host
