#pragma once

#include <cstdint>
#include <expected>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"
#include "relay_host/st_ir.hpp"

namespace relay_host {

inline constexpr std::string_view kSendPrefix = "_send_";
inline constexpr std::string_view kScratchPrefix = "_scratch_";
inline constexpr std::uint32_t kNoSignal = kUnresolvedSlot;

struct ValidateError {
    std::string message;
};

struct SendTarget {
    std::uint32_t target_plc_index;
    std::string key;
};

[[nodiscard]] std::optional<SendTarget> parse_send_target(
    std::string_view name, std::span<const std::string> plc_ids);

enum class SlotKind {
    Output,
    Scratch,
    Send,
};

struct SlotBinding {
    std::string name;
    SlotKind kind;
    std::uint32_t signal_id;
    std::uint32_t send_target_plc;
};

struct TimerDef {
    std::string name;
    double preset_ms;
};

class ValidatedSt {
 public:
    [[nodiscard]] static std::expected<ValidatedSt, ValidateError> try_from(
        StProgram program, const SignalTable& table,
        std::span<const std::string> plc_ids);

    [[nodiscard]] const StProgram& program() const noexcept { return program_; }
    [[nodiscard]] std::span<const SlotBinding> slots() const noexcept { return slots_; }
    [[nodiscard]] std::span<const TimerDef> timers() const noexcept { return timers_; }
    [[nodiscard]] std::optional<std::uint32_t> slot_of_signal(std::uint32_t signal_id) const;

 private:
    ValidatedSt() = default;

    StProgram program_;
    std::vector<SlotBinding> slots_;
    std::vector<TimerDef> timers_;
    std::unordered_map<std::uint32_t, std::uint32_t> signal_to_slot_;
};

[[nodiscard]] SignalTable build_signal_table(const ResolvedTaskSpec& spec,
                                             std::span<const StProgram> programs);

}  // namespace relay_host
