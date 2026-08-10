#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <nlohmann/json.hpp>

#include "relay_host/verify/trace_reader.hpp"
#include "relay_host/verify/verdict.hpp"
#include "relay_host/verify/verdict_writer.hpp"

namespace {

struct CliArgs {
    std::string spec_path;
    std::string trace_path;
    std::string out_path;
};

constexpr std::string_view kUsage =
    "usage: relay_host_verify --spec <resolved_spec.json> --trace <trace.jsonl> "
    "--out <verdict.json>\n";

std::optional<CliArgs> parse_args(int argc, char** argv) {
    CliArgs args;
    for (int i = 1; i < argc; ++i) {
        const std::string_view flag = argv[i];
        if (i + 1 >= argc) {
            std::cerr << "verify_main: flag '" << flag << "' requires a value\n";
            return std::nullopt;
        }
        const std::string_view value = argv[++i];
        if (flag == "--spec") {
            args.spec_path = value;
        } else if (flag == "--trace") {
            args.trace_path = value;
        } else if (flag == "--out") {
            args.out_path = value;
        } else {
            std::cerr << "verify_main: unknown flag '" << flag << "'\n" << kUsage;
            return std::nullopt;
        }
    }
    if (args.spec_path.empty() || args.trace_path.empty() || args.out_path.empty()) {
        std::cerr << kUsage;
        return std::nullopt;
    }
    return args;
}

std::optional<std::vector<std::string>> read_assertions(const std::string& path) {
    std::ifstream stream(path);
    if (!stream) {
        std::cerr << "verify_main: cannot open --spec path '" << path << "'\n";
        return std::nullopt;
    }
    const nlohmann::json spec = nlohmann::json::parse(stream, nullptr, false);
    if (spec.is_discarded() || !spec.is_object()) {
        std::cerr << "verify_main: '" << path << "' is not a JSON object\n";
        return std::nullopt;
    }
    const auto found = spec.find("assertions");
    if (found == spec.end() || !found->is_array()) {
        std::cerr << "verify_main: '" << path << "' has no 'assertions' array\n";
        return std::nullopt;
    }
    std::vector<std::string> assertions;
    for (const auto& entry : *found) {
        if (!entry.is_string()) {
            std::cerr << "verify_main: 'assertions' holds a " << entry.type_name()
                      << " where an assertion string was expected\n";
            return std::nullopt;
        }
        assertions.push_back(entry.get<std::string>());
    }
    return assertions;
}

}  // namespace

int main(int argc, char** argv) {
    const std::optional<CliArgs> args = parse_args(argc, argv);
    if (!args) {
        return 2;
    }

    const std::optional<std::vector<std::string>> assertions =
        read_assertions(args->spec_path);
    if (!assertions) {
        return 2;
    }

    std::ifstream trace_stream(args->trace_path);
    if (!trace_stream) {
        std::cerr << "verify_main: cannot open --trace path '" << args->trace_path
                  << "'\n";
        return 2;
    }
    const auto trace = relay_host::verify::try_read_jsonl(trace_stream);
    if (!trace) {
        std::cerr << "verify_main: " << trace.error().message << "\n";
        return 2;
    }

    const std::vector<relay_host::verify::AssertionResult> results =
        relay_host::verify::evaluate_all(*assertions, *trace);

    std::ofstream out(args->out_path);
    if (!out) {
        std::cerr << "verify_main: cannot open --out path '" << args->out_path
                  << "' for writing\n";
        return 2;
    }
    const auto written = relay_host::verify::try_write_json(results, out);
    if (!written) {
        std::cerr << "verify_main: " << written.error().message << "\n";
        return 2;
    }

    // A failing assertion is a normal outcome, not an error: exit 1 says the
    // verdict is red, and exit 2 is reserved for usage and load failures so a
    // caller can tell "the run did not hold" from "I could not read it".
    bool all_passed = true;
    for (const relay_host::verify::AssertionResult& result : results) {
        if (!result.passed) {
            all_passed = false;
            std::cerr << "verify_main: FAILED " << result.assertion << ": "
                      << result.reason << "\n";
        }
    }
    return all_passed ? 0 : 1;
}
