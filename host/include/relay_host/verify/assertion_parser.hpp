#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace relay_host::verify {

enum class AssertionForm { Eventually, Precedes, Causes };

struct ParsedAssertion {
    AssertionForm form;
    std::vector<std::string> signals;
    std::optional<double> within_ms;
};

// Mirrors relay/strategies/assertions.py: full match after trimming,
// case-insensitive, `\w+` signal names, an optionally fractional millisecond
// budget. An unrecognized form is nullopt, never an error — the evaluator turns
// it into a failed result so one bad assertion string cannot abort a run.
[[nodiscard]] std::optional<ParsedAssertion> parse_assertion(std::string_view text);

}  // namespace relay_host::verify
