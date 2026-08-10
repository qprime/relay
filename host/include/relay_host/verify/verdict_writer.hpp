#pragma once

#include <expected>
#include <ostream>
#include <span>
#include <string>

#include "relay_host/verify/verdict.hpp"

namespace relay_host::verify {

struct WriteError {
    std::string message;
};

// Emits the document relay/verdict_io.py defines: sorted keys, indent 2,
// integral doubles keeping their trailing `.0`, non-finite milliseconds
// rejected rather than written.
[[nodiscard]] std::expected<void, WriteError> try_write_json(
    std::span<const AssertionResult> results, std::ostream& stream);

}  // namespace relay_host::verify
