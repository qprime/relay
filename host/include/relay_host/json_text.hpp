#pragma once

#include <string>
#include <string_view>

namespace relay_host {

// The trace and the verdict are two documents describing one run, and Python's
// json module is normative for both. Two formatters that disagree would spell
// the same value differently in the two files, and no round-trip test on either
// one alone could see it.
[[nodiscard]] std::string format_json_double(double value);
[[nodiscard]] std::string escape_json_string(std::string_view text);

}  // namespace relay_host
