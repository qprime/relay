#pragma once

#include <cstdint>
#include <expected>
#include <span>
#include <vector>

#include "relay_host/io_image.hpp"
#include "relay_host/st_validator.hpp"

namespace relay_host {

struct Timer {
    double preset_ms;
    double accumulated_ms = 0.0;
    bool running = false;
    bool done = false;

    void tick(double dt_ms, bool enable) noexcept;
};

class STContext {
 public:
    explicit STContext(const ValidatedSt& block);

    void begin_scan() noexcept;
    void set_slot(std::uint32_t slot, Cell value) noexcept;
    void assign_slot(std::uint32_t slot, Cell value) noexcept;
    [[nodiscard]] const Cell& slot_value(std::uint32_t slot) const noexcept;
    [[nodiscard]] std::span<const std::uint32_t> assigned_slots() const noexcept;
    [[nodiscard]] Timer& timer_at(std::uint32_t timer_slot) noexcept;

 private:
    std::vector<Cell> values_;
    std::vector<std::uint8_t> assigned_;
    std::vector<std::uint32_t> assigned_order_;
    std::vector<Timer> timers_;
};

enum class EvalErrorKind {
    DivisionByZero,
};

struct EvalError {
    EvalErrorKind kind;
};

[[nodiscard]] std::expected<void, EvalError> evaluate(const ValidatedSt& block,
                                                      STContext& ctx,
                                                      double dt_ms) noexcept;

}  // namespace relay_host
