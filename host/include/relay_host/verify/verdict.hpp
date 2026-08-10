#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "relay_host/verify/trace_model.hpp"

namespace relay_host::verify {

struct Attribution {
    std::string effect_plc;
    std::int64_t effect_tick;
    std::string cause_sender;
    std::int64_t cause_seq;
    std::int64_t cause_sent_tick;
    std::int64_t cause_received_tick;
};

struct AssertionResult {
    std::string assertion;
    bool passed;
    std::string reason;
    std::optional<double> observed_gap_ms;
    std::optional<double> witness_ms;
    std::optional<Attribution> attribution;
};

[[nodiscard]] AssertionResult evaluate_assertion(std::string_view assertion,
                                                 const Trace& trace);
[[nodiscard]] std::vector<AssertionResult> evaluate_all(
    std::span<const std::string> assertions, const Trace& trace);

}  // namespace relay_host::verify
