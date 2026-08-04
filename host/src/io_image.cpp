#include "relay_host/io_image.hpp"

namespace relay_host {

namespace {

struct Numeric {
    bool is_double;
    std::int64_t as_int;
    double as_double;
};

Numeric to_numeric(const Cell& cell) noexcept {
    if (const bool* b = std::get_if<bool>(&cell)) {
        return Numeric{false, *b ? 1 : 0, *b ? 1.0 : 0.0};
    }
    if (const std::int64_t* i = std::get_if<std::int64_t>(&cell)) {
        return Numeric{false, *i, static_cast<double>(*i)};
    }
    const double d = std::get<double>(cell);
    return Numeric{true, 0, d};
}

}  // namespace

bool is_truthy(const Cell& cell) noexcept {
    const Numeric n = to_numeric(cell);
    return n.is_double ? n.as_double != 0.0 : n.as_int != 0;
}

bool cells_equal(const Cell& lhs, const Cell& rhs) noexcept {
    const Numeric l = to_numeric(lhs);
    const Numeric r = to_numeric(rhs);
    if (!l.is_double && !r.is_double) {
        return l.as_int == r.as_int;
    }
    const double ld = l.is_double ? l.as_double : static_cast<double>(l.as_int);
    const double rd = r.is_double ? r.as_double : static_cast<double>(r.as_int);
    return ld == rd;
}

IOImage IOImage::empty() {
    return IOImage{};
}

std::optional<Cell> IOImage::get(std::string_view key) const {
    const auto it = values_.find(key);
    if (it == values_.end()) {
        return std::nullopt;
    }
    return it->second;
}

IOImage IOImage::with_value(std::string_view key, Cell value) const {
    IOImage next = *this;
    const auto it = next.values_.find(key);
    if (it == next.values_.end()) {
        next.values_.emplace(std::string(key), value);
    } else {
        it->second = value;
    }
    return next;
}

const std::map<std::string, Cell, std::less<>>& IOImage::values() const noexcept {
    return values_;
}

}  // namespace relay_host
