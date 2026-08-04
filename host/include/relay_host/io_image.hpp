#pragma once

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <variant>

namespace relay_host {

using Cell = std::variant<bool, std::int64_t, double>;

[[nodiscard]] bool is_truthy(const Cell& cell) noexcept;
[[nodiscard]] bool cells_equal(const Cell& lhs, const Cell& rhs) noexcept;

class IOImage {
 public:
    [[nodiscard]] static IOImage empty();
    [[nodiscard]] std::optional<Cell> get(std::string_view key) const;
    [[nodiscard]] IOImage with_value(std::string_view key, Cell value) const;
    [[nodiscard]] const std::map<std::string, Cell, std::less<>>& values() const noexcept;

 private:
    std::map<std::string, Cell, std::less<>> values_;
};

}  // namespace relay_host
