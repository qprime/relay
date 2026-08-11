#include "relay_host/modbus_codec.hpp"

namespace relay_host::modbus {

namespace {

std::uint16_t read_u16(std::span<const std::uint8_t> bytes, std::size_t offset) {
    return static_cast<std::uint16_t>((static_cast<std::uint16_t>(bytes[offset]) << 8) |
                                      bytes[offset + 1]);
}

void push_u16(std::vector<std::uint8_t>& out, std::uint16_t value) {
    out.push_back(static_cast<std::uint8_t>(value >> 8));
    out.push_back(static_cast<std::uint8_t>(value & 0xFF));
}

std::unexpected<DecodeError> fail(std::string detail) {
    return std::unexpected(DecodeError{"modbus_codec: " + std::move(detail)});
}

bool known_function(std::uint8_t function) {
    return function == kReadCoils || function == kWriteSingleCoil;
}

}  // namespace

std::vector<std::uint8_t> encode_request(const Request& request) {
    std::vector<std::uint8_t> frame;
    frame.reserve(12);
    push_u16(frame, request.transaction_id);
    push_u16(frame, 0);
    push_u16(frame, 6);
    frame.push_back(request.unit_id);
    frame.push_back(request.function);
    push_u16(frame, request.address);
    push_u16(frame, request.value);
    return frame;
}

std::expected<std::size_t, DecodeError> expected_frame_length(
    std::span<const std::uint8_t> header) {
    if (header.size() < kLengthPrefixBytes) {
        return fail("a frame header is " + std::to_string(kLengthPrefixBytes) +
                    " bytes, got " + std::to_string(header.size()));
    }
    const std::uint16_t protocol_id = read_u16(header, 2);
    if (protocol_id != 0) {
        return fail("protocol id must be 0, got " + std::to_string(protocol_id));
    }
    const std::uint16_t length = read_u16(header, 4);
    if (length < kMinLengthField || length > kMaxLengthField) {
        return fail("length field must be in [" + std::to_string(kMinLengthField) + ", " +
                    std::to_string(kMaxLengthField) + "], got " + std::to_string(length));
    }
    return kLengthPrefixBytes + length;
}

std::expected<Response, DecodeError> decode_response(std::span<const std::uint8_t> frame) {
    const auto total = expected_frame_length(frame);
    if (!total) {
        return std::unexpected(total.error());
    }
    if (frame.size() != *total) {
        return fail("length field declares a " + std::to_string(*total) +
                    "-byte frame, got " + std::to_string(frame.size()) + " bytes");
    }

    Response response;
    response.transaction_id = read_u16(frame, 0);
    response.unit_id = frame[6];
    const std::uint8_t raw_function = frame[7];
    const std::span<const std::uint8_t> data = frame.subspan(8);

    if ((raw_function & kExceptionMask) != 0) {
        response.function = static_cast<std::uint8_t>(raw_function & ~kExceptionMask);
        if (!known_function(response.function)) {
            return fail("exception response carries unknown function code " +
                        std::to_string(response.function));
        }
        if (data.size() != 1) {
            return fail("exception response payload must be 1 byte, got " +
                        std::to_string(data.size()));
        }
        response.exception_code = data[0];
        return response;
    }

    response.function = raw_function;
    if (!known_function(response.function)) {
        return fail("unknown function code " + std::to_string(response.function) +
                    "; this client speaks 0x01 read coils and 0x05 write single coil");
    }
    if (response.function == kReadCoils) {
        if (data.empty()) {
            return fail("read-coils response carries no byte count");
        }
        const std::size_t byte_count = data[0];
        if (byte_count == 0 || byte_count != data.size() - 1) {
            return fail("read-coils byte count " + std::to_string(byte_count) +
                        " does not match the " + std::to_string(data.size() - 1) +
                        " payload bytes present");
        }
        response.payload.assign(data.begin() + 1, data.end());
        return response;
    }
    if (data.size() != 4) {
        return fail("write-single-coil response must echo 4 bytes, got " +
                    std::to_string(data.size()));
    }
    response.payload.assign(data.begin(), data.end());
    return response;
}

}  // namespace relay_host::modbus
