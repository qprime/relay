#include <charconv>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "relay_host/async.hpp"
#include "relay_host/host_harness.hpp"
#include "relay_host/spec_loader.hpp"
#include "relay_host/trace.hpp"

namespace {

struct CliArgs {
    std::string spec_path;
    std::string st_blocks_path;
    std::string out_path;
    std::optional<std::int64_t> max_scans;
    std::optional<double> scan_period_ms;
    std::size_t trace_capacity = 100000;
};

constexpr std::string_view kUsage =
    "usage: relay_host_main --spec <resolved_spec.json> --st-blocks <st_blocks.json> "
    "--out <trace.jsonl> [--max-scans <n>] [--scan-period-ms <x>] "
    "[--trace-capacity <n>]\n";

std::optional<CliArgs> parse_args(int argc, char** argv) {
    CliArgs args;
    for (int i = 1; i < argc; ++i) {
        const std::string_view flag = argv[i];
        if (i + 1 >= argc) {
            std::cerr << "host_main: flag '" << flag << "' requires a value\n";
            return std::nullopt;
        }
        const std::string_view value = argv[++i];
        if (flag == "--spec") {
            args.spec_path = value;
        } else if (flag == "--st-blocks") {
            args.st_blocks_path = value;
        } else if (flag == "--out") {
            args.out_path = value;
        } else if (flag == "--max-scans") {
            std::int64_t parsed = 0;
            if (std::from_chars(value.data(), value.data() + value.size(), parsed).ec !=
                std::errc{}) {
                std::cerr << "host_main: --max-scans must be an integer, got '" << value
                          << "'\n";
                return std::nullopt;
            }
            args.max_scans = parsed;
        } else if (flag == "--scan-period-ms") {
            double parsed = 0.0;
            if (std::from_chars(value.data(), value.data() + value.size(), parsed).ec !=
                std::errc{}) {
                std::cerr << "host_main: --scan-period-ms must be a number, got '" << value
                          << "'\n";
                return std::nullopt;
            }
            args.scan_period_ms = parsed;
        } else if (flag == "--trace-capacity") {
            std::size_t parsed = 0;
            if (std::from_chars(value.data(), value.data() + value.size(), parsed).ec !=
                std::errc{}) {
                std::cerr << "host_main: --trace-capacity must be an integer, got '"
                          << value << "'\n";
                return std::nullopt;
            }
            args.trace_capacity = parsed;
        } else {
            std::cerr << "host_main: unknown flag '" << flag << "'\n" << kUsage;
            return std::nullopt;
        }
    }
    if (args.spec_path.empty() || args.st_blocks_path.empty() || args.out_path.empty()) {
        std::cerr << kUsage;
        return std::nullopt;
    }
    return args;
}

}  // namespace

int main(int argc, char** argv) {
    const std::optional<CliArgs> args = parse_args(argc, argv);
    if (!args) {
        return 2;
    }

    auto spec = relay_host::try_load(args->spec_path);
    if (!spec) {
        std::cerr << "host_main: " << spec.error().message << "\n";
        return 1;
    }
    auto st_blocks = relay_host::try_load_st_blocks(args->st_blocks_path, spec->plc_ids);
    if (!st_blocks) {
        std::cerr << "host_main: " << st_blocks.error().message << "\n";
        return 1;
    }

    const relay_host::HostHarness::Config cfg{
        args->scan_period_ms.value_or(spec->scan_period_ms),
        args->max_scans.value_or(spec->max_scans),
        args->trace_capacity,
    };

    asio::io_context io;
    auto harness = relay_host::HostHarness::try_create(
        std::move(*spec), std::move(*st_blocks), cfg, io.get_executor());
    if (!harness) {
        std::cerr << "host_main: " << harness.error().message << "\n";
        return 1;
    }

    asio::co_spawn(io, (*harness)->run(), asio::detached);
    io.run();

    std::ofstream out(args->out_path);
    if (!out) {
        std::cerr << "host_main: cannot open --out path '" << args->out_path
                  << "' for writing\n";
        return 1;
    }
    const auto dumped = (*harness)->trace().dump_to_jsonl(
        out, (*harness)->signal_table(), (*harness)->plc_ids());
    if (!dumped) {
        std::cerr << "host_main: " << dumped.error().message << "\n";
        return 1;
    }
    if ((*harness)->trace().dropped() > 0) {
        std::cerr << "host_main: warning: trace ring dropped "
                  << (*harness)->trace().dropped()
                  << " oldest entries; rerun with a larger --trace-capacity\n";
    }

    if (const auto& error = (*harness)->run_error(); error.has_value()) {
        std::cerr << "host_main: run halted on plc index " << error->plc_index << ": ";
        if (error->scan_error.has_value()) {
            std::cerr << relay_host::describe(*error->scan_error,
                                              (*harness)->signal_table());
        } else {
            std::cerr << "PLC coroutine exited before the harness loop completed";
        }
        std::cerr << "; partial trace written to '" << args->out_path << "'\n";
        return 1;
    }
    return 0;
}
