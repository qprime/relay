#include <gtest/gtest.h>

#include <array>

#include "relay_host/can_codec.hpp"

namespace relay_host::can {
namespace {

TEST(TestCanCodec, golden_vectors) {
    struct Vector { std::uint16_t id; bool value; std::uint16_t crc; std::uint32_t stuffed; std::size_t bits; };
    constexpr std::array<Vector, 8> vectors{{
        {0x000, false, 17446, 4, 59}, {0x000, true, 447, 6, 61},
        {0x080, false, 17304, 3, 58}, {0x080, true, 1537, 4, 59},
        {0x300, false, 21922, 3, 58}, {0x300, true, 4155, 4, 59},
        {0x7ff, false, 31360, 5, 60}, {0x7ff, true, 16153, 5, 60},
    }};
    for (const Vector& vector : vectors) {
        const auto encoded = encode(vector.id, vector.value);
        ASSERT_TRUE(encoded.has_value());
        EXPECT_EQ(encoded->crc, vector.crc);
        EXPECT_EQ(encoded->stuffed_bits, vector.stuffed);
        EXPECT_EQ(encoded->bits.size(), vector.bits);
    }
}

}  // namespace
}  // namespace relay_host::can
