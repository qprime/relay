#include <gtest/gtest.h>

#include <optional>
#include <string>
#include <vector>

#include <asio/experimental/awaitable_operators.hpp>

#include "harness_helpers.hpp"
#include "relay_host/plant_registry.hpp"

namespace relay_host {
namespace {

using nlohmann::json;
using testing::conveyor_blocks;
using testing::conveyor_spec;
using testing::minimal_two_plc_spec;

ResolvedPlant remote_plant_block(std::uint16_t port, double timeout_ms) {
    ResolvedPlant plant;
    plant.type = "remote_socket";
    plant.config = json{{"endpoint", "127.0.0.1:" + std::to_string(port)},
                        {"request_timeout_ms", timeout_ms}};
    return plant;
}

json stub_response(const json& request) {
    const std::string method = request["method"].get<std::string>();
    if (method == "read_actuators") {
        return json{{"id", request["id"]},
                    {"result", {{"actuators", {{"marker", true}}}}}};
    }
    if (method == "step") {
        return json{{"id", request["id"]},
                    {"result",
                     {{"outputs",
                       {{"sensor_a_exit_triggered", true}, {"part_at_b", false}}}}}};
    }
    return json{{"id", request["id"]}, {"result", {{"routed", json::array()}}}};
}

asio::awaitable<void> run_stub_server(asio::ip::tcp::acceptor& acceptor,
                                      int max_responses) {
    auto socket = co_await acceptor.async_accept(asio::use_awaitable);
    std::string buffer;
    for (int count = 0; count < max_responses; ++count) {
        const auto [ec, size] = co_await asio::async_read_until(
            socket, asio::dynamic_buffer(buffer), '\n',
            asio::as_tuple(asio::use_awaitable));
        if (ec) {
            co_return;
        }
        const json request = json::parse(buffer.substr(0, size));
        buffer.erase(0, size);
        const std::string line = stub_response(request).dump() + "\n";
        co_await asio::async_write(socket, asio::buffer(line), asio::use_awaitable);
    }
}

TEST(TestRemoteSocketPlant, test_absent_server_is_startup_error_not_hang) {
    asio::io_context io;
    asio::ip::tcp::acceptor acceptor(
        io, asio::ip::tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));
    const std::uint16_t port = acceptor.local_endpoint().port();
    acceptor.close();

    const ResolvedTaskSpec spec = minimal_two_plc_spec();
    SignalTable table;
    const auto plant = build_plant(remote_plant_block(port, 1000.0), spec.plc_ids,
                                   table, io.get_executor());
    ASSERT_FALSE(plant.has_value());
    EXPECT_NE(plant.error().message.find("127.0.0.1:" + std::to_string(port)),
              std::string::npos);
}

TEST(TestRemoteSocketPlant, test_malformed_response_surfaces_plant_error) {
    asio::io_context io;
    asio::ip::tcp::acceptor acceptor(
        io, asio::ip::tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));

    const ResolvedTaskSpec spec = minimal_two_plc_spec();
    SignalTable table;
    auto plant = build_plant(remote_plant_block(acceptor.local_endpoint().port(), 1000.0),
                             spec.plc_ids, table, io.get_executor());
    ASSERT_TRUE(plant.has_value()) << plant.error().message;

    auto server = [&]() -> asio::awaitable<void> {
        auto socket = co_await acceptor.async_accept(asio::use_awaitable);
        std::string buffer;
        co_await asio::async_read_until(socket, asio::dynamic_buffer(buffer), '\n',
                                        asio::use_awaitable);
        const std::string line = "this is not json\n";
        co_await asio::async_write(socket, asio::buffer(line), asio::use_awaitable);
    };

    std::optional<std::expected<ActuatorState, PlantError>> outcome;
    auto scenario = [&]() -> asio::awaitable<void> {
        const std::vector<IOImage> images(2, IOImage::empty());
        outcome = co_await std::get<RemoteSocketPlant>(*plant).read_actuators(images);
    };

    asio::co_spawn(io, server(), asio::detached);
    asio::co_spawn(io, scenario(), asio::detached);
    io.run();

    ASSERT_TRUE(outcome.has_value());
    ASSERT_FALSE(outcome->has_value());
    EXPECT_NE(outcome->error().message.find("malformed"), std::string::npos);
}

TEST(TestRemoteSocketPlant, test_server_death_midrun_surfaces_run_error) {
    asio::io_context io;
    asio::ip::tcp::acceptor acceptor(
        io, asio::ip::tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));

    ResolvedTaskSpec spec = conveyor_spec();
    spec.plant = remote_plant_block(acceptor.local_endpoint().port(), 500.0);
    auto harness = HostHarness::try_create(std::move(spec), conveyor_blocks(),
                                           HostHarness::Config{1.0, 100, 100000},
                                           io.get_executor());
    ASSERT_TRUE(harness.has_value()) << harness.error().message;

    asio::co_spawn(io, run_stub_server(acceptor, 7), asio::detached);
    asio::co_spawn(io, (*harness)->run(), asio::detached);
    io.run();

    ASSERT_TRUE((*harness)->run_error().has_value());
    EXPECT_EQ((*harness)->run_error()->kind, RunErrorKind::PlantFailed);
    EXPECT_FALSE((*harness)->run_error()->message.empty());
    EXPECT_GT((*harness)->trace().size(), 0u);
}

TEST(TestRemoteSocketPlant, test_concurrent_requests_correlate_correctly) {
    asio::io_context io;
    asio::ip::tcp::acceptor acceptor(
        io, asio::ip::tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));

    const ResolvedTaskSpec spec = minimal_two_plc_spec();
    SignalTable table;
    auto plant = build_plant(remote_plant_block(acceptor.local_endpoint().port(), 1000.0),
                             spec.plc_ids, table, io.get_executor());
    ASSERT_TRUE(plant.has_value()) << plant.error().message;

    auto server = [&]() -> asio::awaitable<void> {
        auto socket = co_await acceptor.async_accept(asio::use_awaitable);
        std::string buffer;
        std::vector<json> requests;
        while (requests.size() < 2) {
            const auto size = co_await asio::async_read_until(
                socket, asio::dynamic_buffer(buffer), '\n', asio::use_awaitable);
            requests.push_back(json::parse(buffer.substr(0, size)));
            buffer.erase(0, size);
        }
        for (auto it = requests.rbegin(); it != requests.rend(); ++it) {
            const std::string line = stub_response(*it).dump() + "\n";
            co_await asio::async_write(socket, asio::buffer(line), asio::use_awaitable);
        }
    };

    std::optional<std::expected<ActuatorState, PlantError>> actuators_outcome;
    std::optional<std::expected<PlantOutputs, PlantError>> step_outcome;
    auto scenario = [&]() -> asio::awaitable<void> {
        auto& remote = std::get<RemoteSocketPlant>(*plant);
        const std::vector<IOImage> images(2, IOImage::empty());
        const ActuatorState empty_actuators;
        using namespace asio::experimental::awaitable_operators;
        auto [actuators, outputs] = co_await (remote.read_actuators(images) &&
                                              remote.step(10.0, empty_actuators));
        actuators_outcome = std::move(actuators);
        step_outcome = std::move(outputs);
    };

    asio::co_spawn(io, server(), asio::detached);
    asio::co_spawn(io, scenario(), asio::detached);
    io.run();

    ASSERT_TRUE(actuators_outcome.has_value());
    ASSERT_TRUE(actuators_outcome->has_value()) << actuators_outcome->error().message;
    const std::optional<Cell> marker = (*actuators_outcome)->get("marker");
    ASSERT_TRUE(marker.has_value())
        << "read_actuators received a response body belonging to another request";
    EXPECT_TRUE(is_truthy(*marker));

    ASSERT_TRUE(step_outcome.has_value());
    ASSERT_TRUE(step_outcome->has_value()) << step_outcome->error().message;
    EXPECT_TRUE((*step_outcome)->sensor_a_exit_triggered);
    EXPECT_FALSE((*step_outcome)->part_at_b);
}

}  // namespace
}  // namespace relay_host
