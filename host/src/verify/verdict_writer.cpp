#include "relay_host/verify/verdict_writer.hpp"

#include <cmath>
#include <string>
#include <vector>

#include "relay_host/json_text.hpp"

namespace relay_host::verify {

namespace {

std::string indent(int depth) {
    return std::string(static_cast<std::size_t>(depth) * 2, ' ');
}

std::expected<std::string, WriteError> format_ms(const std::optional<double>& value,
                                                 std::string_view field,
                                                 std::string_view assertion) {
    if (!value.has_value()) {
        return std::string("null");
    }
    if (!std::isfinite(*value)) {
        return std::unexpected(WriteError{"assertion '" + std::string(assertion) +
                                          "' has " + std::string(field) +
                                          " of a non-finite value, which JSON cannot "
                                          "represent portably; milliseconds must be "
                                          "finite"});
    }
    return format_json_double(*value);
}

// Keys are emitted in sorted order rather than sorted at write time: the set is
// fixed and known, and json.dumps(sort_keys=True) is what defines the bytes.
std::string format_attribution(const Attribution& attribution, int depth) {
    const std::string pad = indent(depth + 1);
    std::string out = "{\n";
    out += pad + "\"cause_received_tick\": " +
           std::to_string(attribution.cause_received_tick) + ",\n";
    out += pad + "\"cause_sender\": " + escape_json_string(attribution.cause_sender) + ",\n";
    out += pad + "\"cause_sent_tick\": " + std::to_string(attribution.cause_sent_tick) +
           ",\n";
    out += pad + "\"cause_seq\": " + std::to_string(attribution.cause_seq) + ",\n";
    out += pad + "\"effect_plc\": " + escape_json_string(attribution.effect_plc) + ",\n";
    out += pad + "\"effect_tick\": " + std::to_string(attribution.effect_tick) + "\n";
    out += indent(depth) + "}";
    return out;
}

std::expected<std::string, WriteError> format_result(const AssertionResult& result,
                                                     int depth) {
    const auto gap = format_ms(result.observed_gap_ms, "observed_gap_ms", result.assertion);
    if (!gap) {
        return std::unexpected(gap.error());
    }
    const auto witness = format_ms(result.witness_ms, "witness_ms", result.assertion);
    if (!witness) {
        return std::unexpected(witness.error());
    }
    const std::string pad = indent(depth + 1);
    std::string out = "{\n";
    out += pad + "\"assertion\": " + escape_json_string(result.assertion) + ",\n";
    out += pad + "\"attribution\": " +
           (result.attribution.has_value()
                ? format_attribution(*result.attribution, depth + 1)
                : "null") +
           ",\n";
    out += pad + "\"observed_gap_ms\": " + *gap + ",\n";
    out += pad + "\"passed\": " + (result.passed ? "true" : "false") + ",\n";
    out += pad + "\"reason\": " + escape_json_string(result.reason) + ",\n";
    out += pad + "\"witness_ms\": " + *witness + "\n";
    out += indent(depth) + "}";
    return out;
}

}  // namespace

std::expected<void, WriteError> try_write_json(std::span<const AssertionResult> results,
                                               std::ostream& stream) {
    std::vector<std::string> entries;
    entries.reserve(results.size());
    std::size_t failed = 0;
    for (const AssertionResult& result : results) {
        const auto entry = format_result(result, 2);
        if (!entry) {
            return std::unexpected(entry.error());
        }
        entries.push_back(*entry);
        if (!result.passed) {
            ++failed;
        }
    }

    std::string out = "{\n";
    out += "  \"counts\": {\n";
    out += "    \"failed\": " + std::to_string(failed) + ",\n";
    out += "    \"passed\": " + std::to_string(results.size() - failed) + ",\n";
    out += "    \"total\": " + std::to_string(results.size()) + "\n";
    out += "  },\n";
    out += std::string("  \"passed\": ") + (failed == 0 ? "true" : "false") + ",\n";
    if (entries.empty()) {
        out += "  \"results\": []\n";
    } else {
        out += "  \"results\": [\n";
        for (std::size_t index = 0; index < entries.size(); ++index) {
            out += "    " + entries[index];
            out += index + 1 < entries.size() ? ",\n" : "\n";
        }
        out += "  ]\n";
    }
    out += "}\n";
    stream << out;
    return {};
}

}  // namespace relay_host::verify
