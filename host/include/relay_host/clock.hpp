#pragma once

#include <cstdint>

namespace relay_host {

struct SimClock {
    std::int64_t tick;
    double elapsed_ms;

    [[nodiscard]] SimClock advance(double scan_period_ms) const noexcept;
    [[nodiscard]] static SimClock zero() noexcept;
};

inline constexpr double kDefaultScanPeriodMs = 10.0;

}  // namespace relay_host
