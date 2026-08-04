#include <gtest/gtest.h>

#include "relay_host/io_image.hpp"

namespace relay_host {
namespace {

TEST(TestIOImage, with_value_returns_new_instance) {
    const IOImage original = IOImage::empty();
    const IOImage updated = original.with_value("belt_b_enable", Cell{true});
    EXPECT_FALSE(original.get("belt_b_enable").has_value());
    ASSERT_TRUE(updated.get("belt_b_enable").has_value());
    EXPECT_EQ(std::get<bool>(*updated.get("belt_b_enable")), true);
}

TEST(TestIOImage, get_returns_nullopt_for_missing_key) {
    const IOImage image = IOImage::empty().with_value("present", Cell{false});
    EXPECT_FALSE(image.get("absent").has_value());
    EXPECT_TRUE(image.get("present").has_value());
}

TEST(TestIOImage, bool_cell_is_not_int) {
    const IOImage image = IOImage::empty().with_value("flag", Cell{true});
    const Cell cell = *image.get("flag");
    EXPECT_TRUE(std::holds_alternative<bool>(cell));
    EXPECT_FALSE(std::holds_alternative<std::int64_t>(cell));
}

}  // namespace
}  // namespace relay_host
