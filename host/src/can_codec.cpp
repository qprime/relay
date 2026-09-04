#include "relay_host/can_codec.hpp"

namespace relay_host::can {

std::expected<EncodedFrame, CodecError> encode(std::uint16_t can_id, bool value) {
    if (can_id > 0x7ff) {
        return std::unexpected(CodecError{"can_id must be in [0, 2047]"});
    }
    std::vector<std::uint8_t> body{0};
    for (int shift = 10; shift >= 0; --shift) body.push_back((can_id >> shift) & 1);
    body.insert(body.end(), {0, 0, 0, 0, 0, 0, 1});
    const std::uint8_t byte = value ? 1 : 0;
    for (int shift = 7; shift >= 0; --shift) body.push_back((byte >> shift) & 1);

    std::uint16_t crc = 0;
    for (const std::uint8_t bit : body) {
        const bool feedback = ((crc >> 14) & 1) != bit;
        crc = static_cast<std::uint16_t>((crc << 1) & 0x7fff);
        if (feedback) crc ^= 0x4599;
    }
    std::vector<std::uint8_t> source = body;
    for (int shift = 14; shift >= 0; --shift) source.push_back((crc >> shift) & 1);

    EncodedFrame encoded{{}, crc, 0};
    int prior = -1;
    int run = 0;
    for (const std::uint8_t bit : source) {
        encoded.bits.push_back(bit);
        if (bit == prior) ++run;
        else { prior = bit; run = 1; }
        if (run == 5) {
            encoded.bits.push_back(static_cast<std::uint8_t>(1 - bit));
            ++encoded.stuffed_bits;
            prior = 1 - bit;
            run = 1;
        }
    }
    encoded.bits.insert(encoded.bits.end(), 13, 1);
    return encoded;
}

}  // namespace relay_host::can
