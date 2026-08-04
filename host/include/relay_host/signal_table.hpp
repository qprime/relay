#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace relay_host {

class SignalTable {
 public:
    std::uint32_t add(std::string_view name);
    [[nodiscard]] std::optional<std::uint32_t> find_id(std::string_view name) const;
    [[nodiscard]] const std::string& name_of(std::uint32_t id) const;
    [[nodiscard]] std::uint32_t size() const noexcept;

 private:
    std::vector<std::string> names_;
    std::unordered_map<std::string, std::uint32_t> ids_;
};

}  // namespace relay_host
