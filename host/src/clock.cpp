#include "relay_host/clock.hpp"

namespace relay_host {

SimClock SimClock::advance(double scan_period_ms) const noexcept {
    return SimClock{tick + 1, elapsed_ms + scan_period_ms};
}

SimClock SimClock::zero() noexcept {
    return SimClock{0, 0.0};
}

}  // namespace relay_host
