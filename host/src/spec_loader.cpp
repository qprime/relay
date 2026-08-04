#include "relay_host/spec_loader.hpp"

#include <fstream>
#include <sstream>

#include <nlohmann/json.hpp>

namespace relay_host {

namespace {

using nlohmann::json;

std::unexpected<LoadError> fail(const std::filesystem::path& path, const std::string& detail) {
    return std::unexpected(LoadError{"spec_loader: " + path.string() + ": " + detail});
}

std::expected<json, LoadError> parse_file(const std::filesystem::path& path) {
    std::ifstream stream(path);
    if (!stream) {
        return fail(path, "file cannot be opened for reading");
    }
    json parsed = json::parse(stream, nullptr, false);
    if (parsed.is_discarded()) {
        return fail(path, "content is not valid JSON");
    }
    return parsed;
}

std::expected<std::string, LoadError> require_string(const json& obj, const std::string& key,
                                                     const std::filesystem::path& path) {
    if (!obj.contains(key) || !obj[key].is_string()) {
        return fail(path, "field '" + key + "' must be a string, got " +
                              (obj.contains(key) ? std::string(obj[key].type_name())
                                                 : std::string("nothing")));
    }
    return obj[key].get<std::string>();
}

std::expected<double, LoadError> require_number(const json& obj, const std::string& key,
                                                const std::filesystem::path& path) {
    if (!obj.contains(key) || !obj[key].is_number()) {
        return fail(path, "field '" + key + "' must be a number, got " +
                              (obj.contains(key) ? std::string(obj[key].type_name())
                                                 : std::string("nothing")));
    }
    return obj[key].get<double>();
}

std::expected<std::vector<std::string>, LoadError> require_string_array(
    const json& obj, const std::string& key, const std::filesystem::path& path) {
    if (!obj.contains(key) || !obj[key].is_array()) {
        return fail(path, "field '" + key + "' must be an array, got " +
                              (obj.contains(key) ? std::string(obj[key].type_name())
                                                 : std::string("nothing")));
    }
    std::vector<std::string> out;
    for (const json& item : obj[key]) {
        if (!item.is_string()) {
            return fail(path, "field '" + key + "' must contain only strings, got " +
                                  std::string(item.type_name()));
        }
        out.push_back(item.get<std::string>());
    }
    return out;
}

}  // namespace

std::expected<ResolvedTaskSpec, LoadError> try_load(const std::filesystem::path& path) {
    auto parsed = parse_file(path);
    if (!parsed) {
        return std::unexpected(parsed.error());
    }
    const json& root = *parsed;
    if (!root.is_object()) {
        return fail(path, "top level must be a JSON object, got " +
                              std::string(root.type_name()));
    }

    ResolvedTaskSpec spec;
    auto system_name = require_string(root, "system_name", path);
    if (!system_name) return std::unexpected(system_name.error());
    spec.system_name = *system_name;

    auto plc_ids = require_string_array(root, "plc_ids", path);
    if (!plc_ids) return std::unexpected(plc_ids.error());
    if (plc_ids->empty()) {
        return fail(path, "field 'plc_ids' must be non-empty");
    }
    spec.plc_ids = *plc_ids;

    auto scan_period = require_number(root, "scan_period_ms", path);
    if (!scan_period) return std::unexpected(scan_period.error());
    if (*scan_period <= 0.0) {
        return fail(path, "field 'scan_period_ms' must be > 0, got " +
                              std::to_string(*scan_period));
    }
    spec.scan_period_ms = *scan_period;

    auto max_scans = require_number(root, "max_scans", path);
    if (!max_scans) return std::unexpected(max_scans.error());
    if (*max_scans < 1.0) {
        return fail(path, "field 'max_scans' must be >= 1, got " + std::to_string(*max_scans));
    }
    spec.max_scans = static_cast<std::int64_t>(*max_scans);

    if (!root.contains("comm") || !root["comm"].is_object()) {
        return fail(path, "field 'comm' must be an object");
    }
    const json& comm = root["comm"];
    auto strategy = require_string(comm, "strategy", path);
    if (!strategy) return std::unexpected(strategy.error());
    spec.comm.strategy = *strategy;
    if (comm.contains("tags")) {
        if (!comm["tags"].is_array()) {
            return fail(path, "field 'comm.tags' must be an array");
        }
        for (const json& tag : comm["tags"]) {
            if (!tag.is_object()) {
                return fail(path, "field 'comm.tags' entries must be objects");
            }
            ResolvedTag resolved;
            auto name = require_string(tag, "name", path);
            if (!name) return std::unexpected(name.error());
            resolved.name = *name;
            auto produced_by = require_string(tag, "produced_by", path);
            if (!produced_by) return std::unexpected(produced_by.error());
            resolved.produced_by = *produced_by;
            auto consumed_by = require_string_array(tag, "consumed_by", path);
            if (!consumed_by) return std::unexpected(consumed_by.error());
            resolved.consumed_by = *consumed_by;
            spec.comm.tags.push_back(std::move(resolved));
        }
    }

    if (!root.contains("plant") || !root["plant"].is_object()) {
        return fail(path, "field 'plant' must be an object");
    }
    const json& plant = root["plant"];
    auto plant_type = require_string(plant, "type", path);
    if (!plant_type) return std::unexpected(plant_type.error());
    spec.plant.type = *plant_type;
    if (!plant.contains("config") || !plant["config"].is_object()) {
        return fail(path, "field 'plant.config' must be an object");
    }
    const json& config = plant["config"];
    auto belt_speed = require_number(config, "belt_speed_m_per_s", path);
    if (!belt_speed) return std::unexpected(belt_speed.error());
    auto threshold = require_number(config, "sensor_trigger_threshold_m", path);
    if (!threshold) return std::unexpected(threshold.error());
    auto latency = require_number(config, "actuator_latency_ms", path);
    if (!latency) return std::unexpected(latency.error());
    spec.plant.config =
        ResolvedPlantConfig{*belt_speed, *threshold, *latency};

    if (!plant.contains("routes") || !plant["routes"].is_array()) {
        return fail(path, "field 'plant.routes' must be an array");
    }
    for (const json& route : plant["routes"]) {
        if (!route.is_object()) {
            return fail(path, "field 'plant.routes' entries must be objects");
        }
        ResolvedRoute resolved;
        auto sensor = require_string(route, "sensor", path);
        if (!sensor) return std::unexpected(sensor.error());
        resolved.sensor = *sensor;
        auto to_plc = require_string(route, "to_plc", path);
        if (!to_plc) return std::unexpected(to_plc.error());
        resolved.to_plc = *to_plc;
        auto as_key = require_string(route, "as_key", path);
        if (!as_key) return std::unexpected(as_key.error());
        resolved.as_key = *as_key;
        auto trigger = require_string(route, "trigger", path);
        if (!trigger) return std::unexpected(trigger.error());
        if (*trigger != "edge" && *trigger != "level") {
            return fail(path, "field 'plant.routes[].trigger' must be 'edge' or 'level', got '" +
                                  *trigger + "'");
        }
        resolved.trigger = *trigger;
        spec.plant.routes.push_back(std::move(resolved));
    }

    if (!plant.contains("actuators") || !plant["actuators"].is_array()) {
        return fail(path, "field 'plant.actuators' must be an array");
    }
    for (const json& actuator : plant["actuators"]) {
        if (!actuator.is_object()) {
            return fail(path, "field 'plant.actuators' entries must be objects");
        }
        ResolvedActuator resolved;
        auto from_plc = require_string(actuator, "from_plc", path);
        if (!from_plc) return std::unexpected(from_plc.error());
        resolved.from_plc = *from_plc;
        auto key = require_string(actuator, "key", path);
        if (!key) return std::unexpected(key.error());
        resolved.key = *key;
        auto alias = require_string(actuator, "as", path);
        if (!alias) return std::unexpected(alias.error());
        resolved.as = *alias;
        spec.plant.actuators.push_back(std::move(resolved));
    }

    auto assertions = require_string_array(root, "assertions", path);
    if (!assertions) return std::unexpected(assertions.error());
    spec.assertions = *assertions;

    return spec;
}

std::expected<std::vector<std::pair<std::string, std::string>>, LoadError> try_load_st_blocks(
    const std::filesystem::path& path, std::span<const std::string> plc_ids) {
    auto parsed = parse_file(path);
    if (!parsed) {
        return std::unexpected(parsed.error());
    }
    const json& root = *parsed;
    if (!root.is_object()) {
        return fail(path, "top level must be a JSON object mapping plc_id to ST source");
    }
    std::vector<std::pair<std::string, std::string>> blocks;
    for (const std::string& plc_id : plc_ids) {
        if (!root.contains(plc_id)) {
            return fail(path, "missing ST block for plc_id '" + plc_id + "'");
        }
        if (!root[plc_id].is_string()) {
            return fail(path, "ST block for plc_id '" + plc_id + "' must be a string, got " +
                                  std::string(root[plc_id].type_name()));
        }
        blocks.emplace_back(plc_id, root[plc_id].get<std::string>());
    }
    return blocks;
}

}  // namespace relay_host
