#pragma once

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "relay_host/io_image.hpp"

namespace relay_host::verify {

// The trace as the verifier sees it: plain data read off the wire format
// relay/trace_io.py defines, not the runtime's ring-buffer representation.
// Reading the file rather than in-memory state is what lets this verifier be
// pointed at the Python sim's own trace, where the only variable is the
// verifier.

struct SendEntry {
    std::int64_t count;
    Cell value;
};

struct ReceiptEntry {
    std::optional<std::string> sender;
    std::int64_t seq;
    Cell value;
};

struct TraceRecord {
    std::string plc_id;
    std::int64_t tick;
    double elapsed_ms;
    std::map<std::string, Cell, std::less<>> io_snapshot;
    std::map<std::string, Cell, std::less<>> outputs;
    std::map<std::string, SendEntry, std::less<>> sends;
    std::map<std::string, ReceiptEntry, std::less<>> recvs;
};

struct Trace {
    std::vector<TraceRecord> records;
};

}  // namespace relay_host::verify
