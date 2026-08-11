#pragma once

#include <cstddef>
#include <cstdint>
#include <expected>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace relay_host::modbus {

inline constexpr std::uint8_t kReadCoils = 0x01;
inline constexpr std::uint8_t kWriteSingleCoil = 0x05;
inline constexpr std::uint8_t kExceptionMask = 0x80;

inline constexpr std::uint16_t kCoilOn = 0xFF00;
inline constexpr std::uint16_t kCoilOff = 0x0000;

inline constexpr std::uint8_t kIllegalFunction = 0x01;
inline constexpr std::uint8_t kIllegalDataAddress = 0x02;
inline constexpr std::uint8_t kIllegalDataValue = 0x03;

inline constexpr std::size_t kLengthPrefixBytes = 6;
inline constexpr std::size_t kMinLengthField = 2;
inline constexpr std::size_t kMaxLengthField = 254;

struct Request {
    std::uint16_t transaction_id;
    std::uint8_t unit_id;
    std::uint8_t function;
    std::uint16_t address;
    std::uint16_t value;
};

struct DecodeError {
    std::string message;
};

struct Response {
    std::uint16_t transaction_id;
    std::uint8_t unit_id;
    std::uint8_t function;
    std::vector<std::uint8_t> payload;
    std::optional<std::uint8_t> exception_code;
};

[[nodiscard]] std::vector<std::uint8_t> encode_request(const Request& request);

[[nodiscard]] std::expected<Response, DecodeError> decode_response(
    std::span<const std::uint8_t> frame);

[[nodiscard]] std::expected<std::size_t, DecodeError> expected_frame_length(
    std::span<const std::uint8_t> header);

}  // namespace relay_host::modbus
