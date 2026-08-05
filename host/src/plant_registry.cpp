#include "relay_host/plant_registry.hpp"

#include <array>
#include <utility>

namespace relay_host {

namespace {

using PlantFactory = std::expected<PlantVariant, PlantError> (*)(
    const ResolvedPlant&, std::span<const std::string>, const SignalTable&, Executor);

std::expected<PlantVariant, PlantError> make_conveyor(
    const ResolvedPlant& plant, std::span<const std::string> plc_ids,
    const SignalTable& table, Executor) {
    auto stub = LocalStubPlant::try_create(plant, plc_ids, table);
    if (!stub) {
        return std::unexpected(stub.error());
    }
    return PlantVariant{std::move(*stub)};
}

std::expected<PlantVariant, PlantError> make_remote_socket(
    const ResolvedPlant& plant, std::span<const std::string> plc_ids,
    const SignalTable& table, Executor ex) {
    auto remote = RemoteSocketPlant::try_create(plant, plc_ids, table, std::move(ex));
    if (!remote) {
        return std::unexpected(remote.error());
    }
    return PlantVariant{std::move(*remote)};
}

struct RegistryEntry {
    std::string_view type;
    PlantFactory factory;
};

constexpr std::array<RegistryEntry, 2> kRegistry{{
    {"conveyor", make_conveyor},
    {"remote_socket", make_remote_socket},
}};

constexpr std::array<std::string_view, kRegistry.size()> kKnownTypes = [] {
    std::array<std::string_view, kRegistry.size()> types{};
    for (std::size_t index = 0; index < kRegistry.size(); ++index) {
        types[index] = kRegistry[index].type;
    }
    return types;
}();

}  // namespace

std::expected<PlantVariant, PlantError> build_plant(const ResolvedPlant& plant,
                                                    std::span<const std::string> plc_ids,
                                                    const SignalTable& table,
                                                    Executor ex) {
    for (const RegistryEntry& entry : kRegistry) {
        if (entry.type == plant.type) {
            return entry.factory(plant, plc_ids, table, std::move(ex));
        }
    }
    std::string known;
    for (const std::string_view type : kKnownTypes) {
        if (!known.empty()) {
            known += ", ";
        }
        known += type;
    }
    return std::unexpected(PlantError{"plant_registry: unknown plant type '" +
                                      plant.type + "'; known: " + known});
}

std::span<const std::string_view> known_plant_types() { return kKnownTypes; }

}  // namespace relay_host
