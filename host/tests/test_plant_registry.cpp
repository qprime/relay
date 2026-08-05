#include <gtest/gtest.h>

#include <cstdio>
#include <filesystem>
#include <fstream>

#include "harness_helpers.hpp"
#include "relay_host/plant_registry.hpp"

namespace relay_host {
namespace {

using testing::minimal_two_plc_spec;

TEST(TestPlantRegistry, test_unknown_plant_type_is_startup_error) {
    ResolvedTaskSpec spec = minimal_two_plc_spec();
    spec.plant.type = "tank";
    asio::io_context io;
    SignalTable table;
    const auto plant = build_plant(spec.plant, spec.plc_ids, table, io.get_executor());
    ASSERT_FALSE(plant.has_value());
    EXPECT_NE(plant.error().message.find("tank"), std::string::npos);
    EXPECT_NE(plant.error().message.find("conveyor"), std::string::npos);
}

TEST(TestPlantRegistry, test_conveyor_config_parsed_by_stub_not_loader) {
    ResolvedTaskSpec spec = minimal_two_plc_spec();
    spec.plant.config = nlohmann::json{{"belt_speed_m_per_s", "fast"},
                                       {"sensor_trigger_threshold_m", 0.1},
                                       {"actuator_latency_ms", 50.0}};
    asio::io_context io;
    SignalTable table;
    const auto plant = build_plant(spec.plant, spec.plc_ids, table, io.get_executor());
    ASSERT_FALSE(plant.has_value());
    EXPECT_NE(plant.error().message.find("belt_speed_m_per_s"), std::string::npos);
}

TEST(TestPlantRegistry, test_unknown_route_sensor_is_startup_error) {
    ResolvedTaskSpec spec = minimal_two_plc_spec();
    spec.plant.routes = {ResolvedRoute{"sensor_c", "plc_a", "some_key", "level"}};
    asio::io_context io;
    SignalTable table;
    const auto plant = build_plant(spec.plant, spec.plc_ids, table, io.get_executor());
    ASSERT_FALSE(plant.has_value());
    EXPECT_NE(plant.error().message.find("sensor_c"), std::string::npos);
    EXPECT_NE(plant.error().message.find("part_at_b"), std::string::npos);
}

TEST(TestPlantRegistry, test_remote_socket_type_resolves) {
    asio::io_context io;
    asio::ip::tcp::acceptor acceptor(
        io, asio::ip::tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));

    ResolvedTaskSpec spec = minimal_two_plc_spec();
    spec.plant.type = "remote_socket";
    spec.plant.config = nlohmann::json{
        {"endpoint", "127.0.0.1:" + std::to_string(acceptor.local_endpoint().port())}};
    SignalTable table;
    const auto plant = build_plant(spec.plant, spec.plc_ids, table, io.get_executor());
    ASSERT_TRUE(plant.has_value()) << plant.error().message;
    EXPECT_TRUE(std::holds_alternative<RemoteSocketPlant>(*plant));
}

TEST(TestPlantRegistry, test_loader_accepts_non_conveyor_config_shape) {
    const nlohmann::json spec_json{
        {"system_name", "socket_demo"},
        {"plc_ids", {"plc_a"}},
        {"scan_period_ms", 10.0},
        {"max_scans", 10},
        {"comm", {{"strategy", "tag"}}},
        {"plant",
         {{"type", "remote_socket"},
          {"config", {{"endpoint", "127.0.0.1:9000"}}},
          {"routes", nlohmann::json::array()},
          {"actuators", nlohmann::json::array()}}},
        {"assertions", nlohmann::json::array()},
    };
    const std::filesystem::path path =
        std::filesystem::temp_directory_path() / "relay_host_registry_spec.json";
    std::ofstream(path) << spec_json.dump();
    const auto spec = try_load(path);
    std::filesystem::remove(path);
    ASSERT_TRUE(spec.has_value()) << spec.error().message;
    EXPECT_EQ(spec->plant.type, "remote_socket");
    EXPECT_EQ(spec->plant.config.at("endpoint").get<std::string>(), "127.0.0.1:9000");
}

}  // namespace
}  // namespace relay_host
