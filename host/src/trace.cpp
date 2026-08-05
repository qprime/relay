#include "relay_host/trace.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <utility>

namespace relay_host {

namespace {

std::string escape_json_string(std::string_view text) {
    std::string out;
    out.reserve(text.size() + 2);
    out.push_back('"');
    for (const char c : text) {
        const unsigned char uc = static_cast<unsigned char>(c);
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (uc < 0x20 || uc > 0x7e) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", uc);
                    out += buf;
                } else {
                    out.push_back(c);
                }
        }
    }
    out.push_back('"');
    return out;
}

struct FormattedCell {
    std::string text;
    bool ok;
};

FormattedCell format_cell(const Cell& cell) {
    if (const bool* b = std::get_if<bool>(&cell)) {
        return FormattedCell{*b ? "true" : "false", true};
    }
    if (const std::int64_t* i = std::get_if<std::int64_t>(&cell)) {
        return FormattedCell{std::to_string(*i), true};
    }
    const double d = std::get<double>(cell);
    if (!std::isfinite(d)) {
        return FormattedCell{"", false};
    }
    return FormattedCell{format_json_double(d), true};
}

std::expected<std::string, DumpError> format_cells_object(
    std::span<const CellSlot> cells, const SignalTable& table, std::string_view where,
    std::int64_t tick) {
    std::vector<std::pair<std::string, const Cell*>> named;
    named.reserve(cells.size());
    for (const CellSlot& slot : cells) {
        named.emplace_back(table.name_of(slot.signal_id), &slot.value);
    }
    std::sort(named.begin(), named.end(),
              [](const auto& lhs, const auto& rhs) { return lhs.first < rhs.first; });
    std::string out = "{";
    bool first = true;
    for (const auto& [name, value] : named) {
        const FormattedCell formatted = format_cell(*value);
        if (!formatted.ok) {
            return std::unexpected(DumpError{
                "trace: " + std::string(where) + " signal '" + name + "' at tick " +
                std::to_string(tick) +
                " is non-finite, which JSON cannot represent portably; signal values "
                "must be finite"});
        }
        if (!first) {
            out += ", ";
        }
        first = false;
        out += escape_json_string(name) + ": " + formatted.text;
    }
    out += "}";
    return out;
}

std::string format_seqs_object(std::span<const SeqSlot> slots, const SignalTable& table) {
    std::vector<std::pair<std::string, std::int64_t>> named;
    named.reserve(slots.size());
    for (const SeqSlot& slot : slots) {
        named.emplace_back(table.name_of(slot.signal_id), slot.seq);
    }
    std::sort(named.begin(), named.end(),
              [](const auto& lhs, const auto& rhs) { return lhs.first < rhs.first; });
    std::string out = "{";
    bool first = true;
    for (const auto& [name, seq] : named) {
        if (!first) {
            out += ", ";
        }
        first = false;
        out += escape_json_string(name) + ": " + std::to_string(seq);
    }
    out += "}";
    return out;
}

}  // namespace

std::string describe(const ScanError& error, const SignalTable& table) {
    const std::string signal =
        error.signal_id < table.size() ? table.name_of(error.signal_id) : "(unknown)";
    switch (error.kind) {
        case ScanErrorKind::CellOverflow:
            return "scan produced more than " + std::to_string(kMaxCellsPerScan) +
                   " io cells; kMaxCellsPerScan exceeded at signal '" + signal + "'";
        case ScanErrorKind::OutgoingOverflow:
            return "scan produced more than " + std::to_string(kMaxCellsPerScan) +
                   " outgoing messages; overflow at signal '" + signal + "'";
        case ScanErrorKind::SignalLookupMiss:
            return "assigned variable has no signal table entry; population rule missed "
                   "local slot index " +
                   std::to_string(error.signal_id);
        case ScanErrorKind::EvalDivisionByZero:
            return "ST evaluation divided by zero";
    }
    return "unknown scan error";
}

std::string format_json_double(double value) {
    std::array<char, 40> buf;
    const auto result = std::to_chars(buf.data(), buf.data() + buf.size(), value,
                                      std::chars_format::scientific);
    std::string sci(buf.data(), result.ptr);
    bool negative = false;
    if (!sci.empty() && sci[0] == '-') {
        negative = true;
        sci.erase(0, 1);
    }
    const std::size_t epos = sci.find('e');
    const std::string mantissa = sci.substr(0, epos);
    const int exp10 = std::stoi(sci.substr(epos + 1));
    std::string digits;
    for (const char c : mantissa) {
        if (c != '.') {
            digits.push_back(c);
        }
    }

    std::string out;
    if (exp10 >= -4 && exp10 < 16) {
        const int ndigits = static_cast<int>(digits.size());
        if (exp10 >= ndigits - 1) {
            out = digits + std::string(static_cast<std::size_t>(exp10 - (ndigits - 1)), '0') +
                  ".0";
        } else if (exp10 >= 0) {
            out = digits.substr(0, static_cast<std::size_t>(exp10 + 1)) + "." +
                  digits.substr(static_cast<std::size_t>(exp10 + 1));
        } else {
            out = "0." + std::string(static_cast<std::size_t>(-exp10 - 1), '0') + digits;
        }
    } else {
        out = digits.substr(0, 1);
        if (digits.size() > 1) {
            out += "." + digits.substr(1);
        }
        out += "e";
        out += exp10 < 0 ? "-" : "+";
        std::string exp_text = std::to_string(exp10 < 0 ? -exp10 : exp10);
        if (exp_text.size() < 2) {
            exp_text.insert(0, "0");
        }
        out += exp_text;
    }
    return negative ? "-" + out : out;
}

TraceRing::TraceRing(std::size_t capacity) : entries_(capacity == 0 ? 1 : capacity) {}

ScanTraceEntry& TraceRing::next_entry() noexcept {
    ScanTraceEntry& entry = entries_[write_pos_];
    write_pos_ = (write_pos_ + 1) % entries_.size();
    ++total_;
    entry.input_count = 0;
    entry.output_count = 0;
    entry.send_count = 0;
    entry.recv_count = 0;
    entry.error.reset();
    return entry;
}

std::size_t TraceRing::size() const noexcept {
    return std::min(total_, entries_.size());
}

std::size_t TraceRing::dropped() const noexcept {
    return total_ > entries_.size() ? total_ - entries_.size() : 0;
}

const ScanTraceEntry& TraceRing::at(std::size_t index) const noexcept {
    if (total_ <= entries_.size()) {
        return entries_[index];
    }
    return entries_[(write_pos_ + index) % entries_.size()];
}

std::expected<void, DumpError> TraceRing::dump_to_jsonl(
    std::ostream& stream, const SignalTable& table,
    std::span<const std::string> plc_ids) const {
    for (std::size_t index = 0; index < size(); ++index) {
        const ScanTraceEntry& entry = at(index);
        if (!std::isfinite(entry.clock.elapsed_ms)) {
            return std::unexpected(DumpError{
                "trace: elapsed_ms at tick " + std::to_string(entry.clock.tick) +
                " is non-finite, which JSON cannot represent portably"});
        }
        auto io_object = format_cells_object(
            std::span(entry.input_cells.data(), entry.input_count), table, "io_snapshot",
            entry.clock.tick);
        if (!io_object) {
            return std::unexpected(io_object.error());
        }
        auto outputs_object = format_cells_object(
            std::span(entry.output_cells.data(), entry.output_count), table, "outputs",
            entry.clock.tick);
        if (!outputs_object) {
            return std::unexpected(outputs_object.error());
        }
        const std::string recvs_object = format_seqs_object(
            std::span(entry.recv_slots.data(), entry.recv_count), table);
        const std::string sends_object = format_seqs_object(
            std::span(entry.send_slots.data(), entry.send_count), table);
        stream << "{\"elapsed_ms\": " << format_json_double(entry.clock.elapsed_ms)
               << ", \"io_snapshot\": " << *io_object
               << ", \"outputs\": " << *outputs_object
               << ", \"plc_id\": " << escape_json_string(plc_ids[entry.plc_index])
               << ", \"recvs\": " << recvs_object
               << ", \"sends\": " << sends_object
               << ", \"tick\": " << entry.clock.tick << "}\n";
    }
    return {};
}

}  // namespace relay_host
