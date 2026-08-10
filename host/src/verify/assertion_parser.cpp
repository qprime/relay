#include "relay_host/verify/assertion_parser.hpp"

#include <cmath>
#include <cstdlib>
#include <regex>
#include <string>

namespace relay_host::verify {

namespace {

// A budget too large for a double is not a budget. The grammar admits any digit
// string, so `within: <400 digits>ms` reaches here; `std::stod` would throw
// out_of_range and abort the process, and returning inf to match Python's
// float() would make EVENTUALLY pass for any signal that ever became true.
// Both sides call it an unrecognized form instead — see _finite_budget in
// relay/strategies/assertions.py.
std::optional<double> finite_budget(const std::string& text) {
    const char* begin = text.c_str();
    char* end = nullptr;
    const double value = std::strtod(begin, &end);
    if (end == begin || *end != '\0' || !std::isfinite(value)) {
        return std::nullopt;
    }
    return value;
}

const std::regex& eventually_re() {
    static const std::regex re(R"(EVENTUALLY\(\s*(\w+)\s*,\s*within:\s*(\d+(?:\.\d+)?)\s*ms\s*\))",
                               std::regex::ECMAScript | std::regex::icase);
    return re;
}

const std::regex& precedes_re() {
    static const std::regex re(
        R"(PRECEDES\(\s*(\w+)\s*,\s*(\w+)\s*,\s*within:\s*(\d+(?:\.\d+)?)\s*ms\s*\))",
        std::regex::ECMAScript | std::regex::icase);
    return re;
}

const std::regex& causes_re() {
    static const std::regex re(R"(CAUSES\(\s*(\w+)\s*,\s*(\w+)\s*\))",
                               std::regex::ECMAScript | std::regex::icase);
    return re;
}

std::string trimmed(std::string_view text) {
    constexpr std::string_view kSpace = " \t\n\r\f\v";
    const std::size_t begin = text.find_first_not_of(kSpace);
    if (begin == std::string_view::npos) {
        return std::string();
    }
    const std::size_t end = text.find_last_not_of(kSpace);
    return std::string(text.substr(begin, end - begin + 1));
}

}  // namespace

std::optional<ParsedAssertion> parse_assertion(std::string_view text) {
    const std::string subject = trimmed(text);
    std::smatch match;
    if (std::regex_match(subject, match, eventually_re())) {
        const std::optional<double> budget = finite_budget(match[2].str());
        if (!budget.has_value()) {
            return std::nullopt;
        }
        return ParsedAssertion{AssertionForm::Eventually, {match[1].str()}, budget};
    }
    if (std::regex_match(subject, match, precedes_re())) {
        const std::optional<double> budget = finite_budget(match[3].str());
        if (!budget.has_value()) {
            return std::nullopt;
        }
        return ParsedAssertion{
            AssertionForm::Precedes, {match[1].str(), match[2].str()}, budget};
    }
    if (std::regex_match(subject, match, causes_re())) {
        return ParsedAssertion{AssertionForm::Causes,
                               {match[1].str(), match[2].str()},
                               std::nullopt};
    }
    return std::nullopt;
}

}  // namespace relay_host::verify
