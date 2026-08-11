#include <gtest/gtest.h>

#include <cstdint>
#include <vector>

#include "relay_host/modbus_codec.hpp"

namespace relay_host::modbus {
namespace {

std::vector<std::uint8_t> frame_of(std::uint16_t transaction_id, std::uint16_t protocol_id,
                                   std::uint16_t length, std::uint8_t unit_id,
                                   std::vector<std::uint8_t> pdu) {
    std::vector<std::uint8_t> frame{static_cast<std::uint8_t>(transaction_id >> 8),
                                    static_cast<std::uint8_t>(transaction_id & 0xFF),
                                    static_cast<std::uint8_t>(protocol_id >> 8),
                                    static_cast<std::uint8_t>(protocol_id & 0xFF),
                                    static_cast<std::uint8_t>(length >> 8),
                                    static_cast<std::uint8_t>(length & 0xFF),
                                    unit_id};
    frame.insert(frame.end(), pdu.begin(), pdu.end());
    return frame;
}

TEST(ModbusCodecTest, EncodesWriteSingleCoilPerSpec) {
    const auto on = encode_request(Request{7, 1, kWriteSingleCoil, 0x0002, kCoilOn});
    ASSERT_EQ(on.size(), 12u);
    EXPECT_EQ(on[0], 0x00);
    EXPECT_EQ(on[1], 0x07);
    EXPECT_EQ(on[2], 0x00);
    EXPECT_EQ(on[3], 0x00);
    EXPECT_EQ(on[4], 0x00);
    EXPECT_EQ(on[5], 0x06);
    EXPECT_EQ(on[6], 0x01);
    EXPECT_EQ(on[7], kWriteSingleCoil);
    EXPECT_EQ(on[8], 0x00);
    EXPECT_EQ(on[9], 0x02);
    EXPECT_EQ(on[10], 0xFF);
    EXPECT_EQ(on[11], 0x00);

    const auto off = encode_request(Request{7, 1, kWriteSingleCoil, 0x0002, kCoilOff});
    EXPECT_EQ(off[10], 0x00);
    EXPECT_EQ(off[11], 0x00);
}

TEST(ModbusCodecTest, EncodesReadCoilsQuantityOne) {
    const auto frame = encode_request(Request{0x1234, 9, kReadCoils, 0x0100, 1});
    ASSERT_EQ(frame.size(), 12u);
    EXPECT_EQ(frame[0], 0x12);
    EXPECT_EQ(frame[1], 0x34);
    EXPECT_EQ(frame[6], 9);
    EXPECT_EQ(frame[7], kReadCoils);
    EXPECT_EQ(frame[8], 0x01);
    EXPECT_EQ(frame[9], 0x00);
    EXPECT_EQ(frame[10], 0x00);
    EXPECT_EQ(frame[11], 0x01);
}

TEST(ModbusCodecTest, DecodesReadCoilsResponse) {
    const auto set = decode_response(frame_of(3, 0, 4, 1, {kReadCoils, 0x01, 0x01}));
    ASSERT_TRUE(set.has_value()) << set.error().message;
    EXPECT_EQ(set->transaction_id, 3);
    EXPECT_EQ(set->unit_id, 1);
    EXPECT_EQ(set->function, kReadCoils);
    EXPECT_FALSE(set->exception_code.has_value());
    ASSERT_EQ(set->payload.size(), 1u);
    EXPECT_EQ(set->payload[0] & 0x01, 1);

    const auto clear = decode_response(frame_of(3, 0, 4, 1, {kReadCoils, 0x01, 0x00}));
    ASSERT_TRUE(clear.has_value()) << clear.error().message;
    EXPECT_EQ(clear->payload[0] & 0x01, 0);
}

TEST(ModbusCodecTest, DecodesWriteSingleCoilEcho) {
    const auto echo = decode_response(
        frame_of(4, 0, 6, 1, {kWriteSingleCoil, 0x00, 0x02, 0xFF, 0x00}));
    ASSERT_TRUE(echo.has_value()) << echo.error().message;
    EXPECT_EQ(echo->function, kWriteSingleCoil);
    ASSERT_EQ(echo->payload.size(), 4u);
    EXPECT_EQ(echo->payload[1], 0x02);
    EXPECT_EQ(echo->payload[2], 0xFF);
}

TEST(ModbusCodecTest, DecodesExceptionResponse) {
    const auto decoded = decode_response(
        frame_of(5, 0, 3, 1, {kReadCoils | kExceptionMask, kIllegalDataAddress}));
    ASSERT_TRUE(decoded.has_value()) << decoded.error().message;
    EXPECT_EQ(decoded->function, kReadCoils);
    ASSERT_TRUE(decoded->exception_code.has_value());
    EXPECT_EQ(*decoded->exception_code, kIllegalDataAddress);
}

TEST(ModbusCodecTest, RejectsNonZeroProtocolId) {
    const auto decoded = decode_response(frame_of(1, 1, 4, 1, {kReadCoils, 0x01, 0x01}));
    ASSERT_FALSE(decoded.has_value());
    EXPECT_NE(decoded.error().message.find("protocol id"), std::string::npos);
}

TEST(ModbusCodecTest, RejectsShortFrame) {
    const auto truncated = decode_response(frame_of(1, 0, 8, 1, {kReadCoils, 0x01, 0x01}));
    ASSERT_FALSE(truncated.has_value());
    EXPECT_NE(truncated.error().message.find("length field"), std::string::npos);

    const std::vector<std::uint8_t> stub{0x00, 0x01, 0x00};
    const auto stubbed = decode_response(stub);
    ASSERT_FALSE(stubbed.has_value());
}

TEST(ModbusCodecTest, RejectsUnknownFunctionCode) {
    const auto decoded = decode_response(frame_of(1, 0, 6, 1, {0x03, 0x02, 0x00, 0x00, 0x00}));
    ASSERT_FALSE(decoded.has_value());
    EXPECT_NE(decoded.error().message.find("unknown function code"), std::string::npos);
}

TEST(ModbusCodecTest, RejectsInconsistentReadCoilsByteCount) {
    const auto decoded = decode_response(frame_of(1, 0, 4, 1, {kReadCoils, 0x03, 0x01}));
    ASSERT_FALSE(decoded.has_value());
    EXPECT_NE(decoded.error().message.find("byte count"), std::string::npos);
}

TEST(ModbusCodecTest, ExpectedFrameLengthDrivesTheReadPump) {
    const auto request = encode_request(Request{1, 1, kReadCoils, 0, 1});
    const auto total = expected_frame_length(
        std::span<const std::uint8_t>(request).first(kLengthPrefixBytes));
    ASSERT_TRUE(total.has_value()) << total.error().message;
    EXPECT_EQ(*total, request.size());

    const std::vector<std::uint8_t> too_short{0x00, 0x01, 0x00, 0x00, 0x00};
    EXPECT_FALSE(expected_frame_length(too_short).has_value());

    const std::vector<std::uint8_t> zero_length{0x00, 0x01, 0x00, 0x00, 0x00, 0x00};
    EXPECT_FALSE(expected_frame_length(zero_length).has_value());
}

// The length field counts the unit id plus a 253-byte maximal PDU, so 254 is
// legal and 255 is not. Unreachable in this subset — the largest response here
// is 4 bytes — but the bound states the protocol, not this client's usage.
TEST(ModbusCodecTest, LengthFieldBoundIsTheProtocolMaximum) {
    const auto header = [](std::uint16_t length) {
        return std::vector<std::uint8_t>{0x00, 0x01, 0x00, 0x00,
                                         static_cast<std::uint8_t>(length >> 8),
                                         static_cast<std::uint8_t>(length & 0xFF)};
    };
    EXPECT_EQ(expected_frame_length(header(kMaxLengthField)).value(),
              kLengthPrefixBytes + kMaxLengthField);
    EXPECT_FALSE(expected_frame_length(header(kMaxLengthField + 1)).has_value());
}

}  // namespace
}  // namespace relay_host::modbus
