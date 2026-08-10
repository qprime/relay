#pragma once

#include <expected>
#include <istream>
#include <string>

#include "relay_host/verify/trace_model.hpp"

namespace relay_host::verify {

struct ReadError {
    std::string message;
};

// Sits outside the verifier's purity boundary, exactly as relay/trace_io.py
// sits outside Python's: this is the side facing bytes it did not write, so
// every field is guarded value-level with the predicate its type calls for.
[[nodiscard]] std::expected<Trace, ReadError> try_read_jsonl(std::istream& stream);

}  // namespace relay_host::verify
