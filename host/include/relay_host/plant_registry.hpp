#pragma once

#include <expected>
#include <span>
#include <string>
#include <string_view>
#include <variant>

#include "relay_host/async.hpp"
#include "relay_host/plant_adapter.hpp"
#include "relay_host/remote_socket_plant.hpp"
#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"

namespace relay_host {

using PlantVariant = std::variant<LocalStubPlant, RemoteSocketPlant>;

[[nodiscard]] std::expected<PlantVariant, PlantError> build_plant(
    const ResolvedPlant& plant, std::span<const std::string> plc_ids,
    const SignalTable& table, Executor ex);

[[nodiscard]] std::span<const std::string_view> known_plant_types();

}  // namespace relay_host
