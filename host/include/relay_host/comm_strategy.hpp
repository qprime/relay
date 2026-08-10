#pragma once

#include <expected>
#include <string>

#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"

namespace relay_host {

struct StrategyError {
    std::string message;
};

[[nodiscard]] std::expected<void, StrategyError> validate_comm_signals(
    const ResolvedTaskSpec& spec, const SignalTable& table);

}  // namespace relay_host
