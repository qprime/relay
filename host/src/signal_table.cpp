#include "relay_host/signal_table.hpp"

namespace relay_host {

std::uint32_t SignalTable::add(std::string_view name) {
    const auto it = ids_.find(std::string(name));
    if (it != ids_.end()) {
        return it->second;
    }
    const std::uint32_t id = static_cast<std::uint32_t>(names_.size());
    names_.emplace_back(name);
    ids_.emplace(names_.back(), id);
    return id;
}

std::optional<std::uint32_t> SignalTable::find_id(std::string_view name) const {
    const auto it = ids_.find(std::string(name));
    if (it == ids_.end()) {
        return std::nullopt;
    }
    return it->second;
}

const std::string& SignalTable::name_of(std::uint32_t id) const {
    return names_.at(id);
}

std::uint32_t SignalTable::size() const noexcept {
    return static_cast<std::uint32_t>(names_.size());
}

}  // namespace relay_host
