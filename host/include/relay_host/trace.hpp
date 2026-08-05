#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <optional>
#include <ostream>
#include <span>
#include <string>
#include <vector>

#include "relay_host/clock.hpp"
#include "relay_host/io_image.hpp"
#include "relay_host/signal_table.hpp"

namespace relay_host {

inline constexpr std::size_t kMaxCellsPerScan = 64;

struct CellSlot {
    std::uint32_t signal_id;
    Cell value;
};

struct SeqSlot {
    std::uint32_t signal_id;
    std::int64_t seq;
};

enum class ScanErrorKind {
    CellOverflow,
    OutgoingOverflow,
    SignalLookupMiss,
    EvalDivisionByZero,
};

struct ScanError {
    ScanErrorKind kind;
    std::uint32_t signal_id;
};

[[nodiscard]] std::string describe(const ScanError& error, const SignalTable& table);

struct ScanTraceEntry {
    std::uint32_t plc_index;
    SimClock clock;
    std::uint32_t input_count;
    std::uint32_t output_count;
    std::uint32_t send_count;
    std::uint32_t recv_count;
    std::array<CellSlot, kMaxCellsPerScan> input_cells;
    std::array<CellSlot, kMaxCellsPerScan> output_cells;
    std::array<SeqSlot, kMaxCellsPerScan> send_slots;
    std::array<SeqSlot, kMaxCellsPerScan> recv_slots;
    std::optional<ScanError> error;
};

struct DumpError {
    std::string message;
};

[[nodiscard]] std::string format_json_double(double value);

class TraceRing {
 public:
    explicit TraceRing(std::size_t capacity);

    [[nodiscard]] ScanTraceEntry& next_entry() noexcept;
    [[nodiscard]] std::size_t size() const noexcept;
    [[nodiscard]] std::size_t dropped() const noexcept;
    [[nodiscard]] const ScanTraceEntry& at(std::size_t index) const noexcept;

    [[nodiscard]] std::expected<void, DumpError> dump_to_jsonl(
        std::ostream& stream, const SignalTable& table,
        std::span<const std::string> plc_ids) const;

 private:
    std::vector<ScanTraceEntry> entries_;
    std::size_t write_pos_ = 0;
    std::size_t total_ = 0;
};

}  // namespace relay_host
