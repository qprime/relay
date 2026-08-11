#pragma once

#include <expected>
#include <span>
#include <string>

#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"
#include "relay_host/st_validator.hpp"

namespace relay_host {

struct StrategyError {
    std::string message;
};

[[nodiscard]] std::expected<void, StrategyError> validate_comm_signals(
    const ResolvedTaskSpec& spec, const SignalTable& table,
    std::span<const ValidatedSt> blocks);

}  // namespace relay_host
