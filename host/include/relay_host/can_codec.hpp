#pragma once

#include <cstdint>
#include <expected>
#include <string>
#include <vector>

namespace relay_host::can {

struct CodecError { std::string message; };

struct EncodedFrame {
    std::vector<std::uint8_t> bits;
    std::uint16_t crc;
    std::uint32_t stuffed_bits;
};

[[nodiscard]] std::expected<EncodedFrame, CodecError> encode(std::uint16_t can_id,
                                                             bool value);

}  // namespace relay_host::can
