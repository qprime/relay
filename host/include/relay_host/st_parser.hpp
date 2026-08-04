#pragma once

#include <cstddef>
#include <expected>
#include <string>
#include <string_view>

#include "relay_host/st_ir.hpp"

namespace relay_host {

struct ParseError {
    std::string message;
    std::size_t line;
};

[[nodiscard]] std::expected<StProgram, ParseError> parse_st(std::string_view source);

}  // namespace relay_host
