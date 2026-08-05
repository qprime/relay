#pragma once

#include <cstdint>
#include <expected>
#include <filesystem>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "relay_host/clock.hpp"

namespace relay_host {

struct ResolvedTag {
    std::string name;
    std::string produced_by;
    std::vector<std::string> consumed_by;
};

struct ResolvedComm {
    std::string strategy;
    std::vector<ResolvedTag> tags;
};

struct ResolvedRoute {
    std::string sensor;
    std::string to_plc;
    std::string as_key;
    std::string trigger;
};

struct ResolvedActuator {
    std::string from_plc;
    std::string key;
    std::string as;
};

struct ResolvedPlant {
    std::string type;
    nlohmann::json config;
    std::vector<ResolvedRoute> routes;
    std::vector<ResolvedActuator> actuators;
};

struct ResolvedTaskSpec {
    std::string system_name;
    std::vector<std::string> plc_ids;
    double scan_period_ms;
    std::int64_t max_scans;
    ResolvedComm comm;
    ResolvedPlant plant;
    std::vector<std::string> assertions;
};

struct LoadError {
    std::string message;
};

[[nodiscard]] std::expected<ResolvedTaskSpec, LoadError> try_load(
    const std::filesystem::path& path);

[[nodiscard]] std::expected<std::vector<std::pair<std::string, std::string>>, LoadError>
try_load_st_blocks(const std::filesystem::path& path, std::span<const std::string> plc_ids);

}  // namespace relay_host
