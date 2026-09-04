#include "relay_host/verify/trace_reader.hpp"

#include <string>
#include <string_view>
#include <utility>
#include <cmath>
#include <cstdint>

#include <nlohmann/json.hpp>

namespace relay_host::verify {

namespace {

using Json = nlohmann::json;

// A missing key and an unreadable value get different wrappers at the top
// level, matching relay/trace_io.py's split between KeyError and
// (TypeError, ValueError). Both carry the line number there; both do here.
struct FieldError {
    std::string text;
    bool missing_key = false;
};

template <typename T>
using Read = std::expected<T, FieldError>;

std::unexpected<FieldError> unreadable(std::string text) {
    return std::unexpected(FieldError{std::move(text), false});
}

std::unexpected<FieldError> missing(std::string_view key) {
    return std::unexpected(FieldError{std::string(key), true});
}

std::string type_name(const Json& value) {
    return value.type_name();
}

// Mirrors _check_values in relay/trace_io.py: bool, int, or float. The
// finiteness half of that predicate is enforced one layer up — nlohmann rejects
// NaN, Infinity, and overflowing exponents as malformed JSON, where Python's
// json.loads accepts the first two and needs the explicit test. Repeating it
// here would be an unreachable branch, which is worse than absent.
Read<Cell> read_cell(const Json& value, std::string_view where, std::string_view key) {
    if (value.is_boolean()) {
        return Cell{value.get<bool>()};
    }
    if (value.is_number_integer()) {
        return Cell{value.get<std::int64_t>()};
    }
    if (value.is_number_float()) {
        return Cell{value.get<double>()};
    }
    return unreadable(std::string(where) + " signal '" + std::string(key) +
                      "' has unserializable type " + type_name(value) +
                      "; allowed types are bool, int, float");
}

// Mirrors _check_counters: an integer, and `bool` is not one. The signal-value
// predicate is the wrong one here — it admits True and 0.9 where a later
// truncation would silently turn them into 1 and 0.
Read<std::int64_t> read_counter(const Json& value, std::string_view where,
                                std::string_view key) {
    if (value.is_boolean() || !value.is_number_integer()) {
        return unreadable(std::string(where) + " counter '" + std::string(key) +
                          "' has unserializable type " + type_name(value) +
                          "; expected int");
    }
    return value.get<std::int64_t>();
}

Read<const Json*> require_key(const Json& node, std::string_view key) {
    const auto it = node.find(key);
    if (it == node.end()) {
        return missing(key);
    }
    return &*it;
}

Read<const Json*> require_object(const Json& node, std::string_view key) {
    const auto found = require_key(node, key);
    if (!found) {
        return std::unexpected(found.error());
    }
    if (!(*found)->is_object()) {
        return unreadable(std::string(key) + " is a " + type_name(**found) +
                          ", not an object keyed by signal name");
    }
    return *found;
}

Read<std::map<std::string, Cell, std::less<>>> read_values(const Json& record,
                                                           std::string_view key) {
    const auto node = require_object(record, key);
    if (!node) {
        return std::unexpected(node.error());
    }
    std::map<std::string, Cell, std::less<>> values;
    for (const auto& [name, value] : (*node)->items()) {
        const auto cell = read_cell(value, key, name);
        if (!cell) {
            return std::unexpected(cell.error());
        }
        values.emplace(name, *cell);
    }
    return values;
}

Read<std::map<std::string, SendEntry, std::less<>>> read_sends(const Json& record) {
    const auto node = require_object(record, "sends");
    if (!node) {
        return std::unexpected(node.error());
    }
    std::map<std::string, SendEntry, std::less<>> sends;
    for (const auto& [name, entry] : (*node)->items()) {
        if (!entry.is_object()) {
            return unreadable("sends entry '" + name + "' is a " + type_name(entry) +
                              ", not an object with 'count' and 'value'");
        }
        const auto value = require_key(entry, "value");
        if (!value) {
            return std::unexpected(value.error());
        }
        const auto cell = read_cell(**value, "sends", name);
        if (!cell) {
            return std::unexpected(cell.error());
        }
        const auto count_node = require_key(entry, "count");
        if (!count_node) {
            return std::unexpected(count_node.error());
        }
        const auto count = read_counter(**count_node, "sends", name);
        if (!count) {
            return std::unexpected(count.error());
        }
        for (const std::string field : {"can_id", "frame_bits"}) {
            if (!entry.contains(field)) continue;
            const Json& metadata = entry[field];
            if (metadata.is_boolean() || !metadata.is_number_unsigned()) {
                return unreadable("sends entry '" + name + "' field '" + field +
                                  "' must be an unsigned integer");
            }
            const std::uint64_t number = metadata.get<std::uint64_t>();
            if ((field == "can_id" && number > 0x7ff) ||
                (field == "frame_bits" && number == 0)) {
                return unreadable("sends entry '" + name + "' field '" + field +
                                  "' is out of range");
            }
        }
        for (const std::string field : {"arbitration_start_ms", "completion_ms"}) {
            if (!entry.contains(field)) continue;
            const Json& metadata = entry[field];
            if (!metadata.is_number() || !std::isfinite(metadata.get<double>())) {
                return unreadable("sends entry '" + name + "' field '" + field +
                                  "' must be a finite number");
            }
        }
        sends.emplace(name, SendEntry{*count, *cell});
    }
    return sends;
}

Read<std::map<std::string, ReceiptEntry, std::less<>>> read_recvs(const Json& record) {
    const auto node = require_object(record, "recvs");
    if (!node) {
        return std::unexpected(node.error());
    }
    std::map<std::string, ReceiptEntry, std::less<>> recvs;
    for (const auto& [name, entry] : (*node)->items()) {
        if (!entry.is_object()) {
            return unreadable("recvs entry '" + name + "' is a " + type_name(entry) +
                              ", not an object with 'sender', 'seq', and 'value'");
        }
        const auto sender_node = require_key(entry, "sender");
        if (!sender_node) {
            return std::unexpected(sender_node.error());
        }
        std::optional<std::string> sender;
        if ((*sender_node)->is_string()) {
            sender = (*sender_node)->get<std::string>();
        } else if (!(*sender_node)->is_null()) {
            return unreadable("recvs entry '" + name + "' has sender of type " +
                              type_name(**sender_node) +
                              "; expected a plc_id string or null");
        }
        const auto value = require_key(entry, "value");
        if (!value) {
            return std::unexpected(value.error());
        }
        const auto cell = read_cell(**value, "recvs", name);
        if (!cell) {
            return std::unexpected(cell.error());
        }
        const auto seq_node = require_key(entry, "seq");
        if (!seq_node) {
            return std::unexpected(seq_node.error());
        }
        const auto seq = read_counter(**seq_node, "recvs", name);
        if (!seq) {
            return std::unexpected(seq.error());
        }
        recvs.emplace(name, ReceiptEntry{sender, *seq, *cell});
    }
    return recvs;
}

Read<TraceRecord> read_record(const Json& data) {
    TraceRecord record;

    const auto plc_id = require_key(data, "plc_id");
    if (!plc_id) {
        return std::unexpected(plc_id.error());
    }
    if (!(*plc_id)->is_string()) {
        return unreadable("field 'plc_id' has unserializable type " + type_name(**plc_id) +
                          "; expected str");
    }
    record.plc_id = (*plc_id)->get<std::string>();

    const auto tick = require_key(data, "tick");
    if (!tick) {
        return std::unexpected(tick.error());
    }
    if ((*tick)->is_boolean() || !(*tick)->is_number_integer()) {
        return unreadable("field 'tick' has unserializable type " + type_name(**tick) +
                          "; expected int");
    }
    record.tick = (*tick)->get<std::int64_t>();

    const auto elapsed = require_key(data, "elapsed_ms");
    if (!elapsed) {
        return std::unexpected(elapsed.error());
    }
    if (!(*elapsed)->is_number()) {
        return unreadable("field 'elapsed_ms' has unserializable type " +
                          type_name(**elapsed) + "; expected a number");
    }
    record.elapsed_ms = (*elapsed)->get<double>();

    auto io_snapshot = read_values(data, "io_snapshot");
    if (!io_snapshot) {
        return std::unexpected(io_snapshot.error());
    }
    record.io_snapshot = std::move(*io_snapshot);

    auto outputs = read_values(data, "outputs");
    if (!outputs) {
        return std::unexpected(outputs.error());
    }
    record.outputs = std::move(*outputs);

    auto sends = read_sends(data);
    if (!sends) {
        return std::unexpected(sends.error());
    }
    record.sends = std::move(*sends);

    auto recvs = read_recvs(data);
    if (!recvs) {
        return std::unexpected(recvs.error());
    }
    record.recvs = std::move(*recvs);

    return record;
}

bool is_blank(std::string_view line) {
    return line.find_first_not_of(" \t\n\r\f\v") == std::string_view::npos;
}

}  // namespace

std::expected<Trace, ReadError> try_read_jsonl(std::istream& stream) {
    Trace trace;
    std::string line;
    std::int64_t lineno = 0;
    while (std::getline(stream, line)) {
        ++lineno;
        if (is_blank(line)) {
            continue;
        }
        const std::string where = "line " + std::to_string(lineno);
        const Json data = Json::parse(line, nullptr, false);
        if (data.is_discarded()) {
            return std::unexpected(ReadError{"malformed JSON on " + where});
        }
        if (!data.is_object()) {
            return std::unexpected(
                ReadError{where + " is a JSON " + type_name(data) + ", not an object"});
        }
        auto record = read_record(data);
        if (!record) {
            const FieldError& error = record.error();
            return std::unexpected(ReadError{
                error.missing_key ? where + " missing required key '" + error.text + "'"
                                  : where + " has an unreadable field: " + error.text});
        }
        trace.records.push_back(std::move(*record));
    }
    return trace;
}

}  // namespace relay_host::verify
