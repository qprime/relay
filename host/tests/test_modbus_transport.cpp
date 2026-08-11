#include <gtest/gtest.h>

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "harness_helpers.hpp"
#include "relay_host/comm_transport_registry.hpp"
#include "relay_host/modbus_codec.hpp"

namespace relay_host {
namespace {

using testing::address_conveyor_spec;

struct StubRequest {
    std::uint8_t function;
    std::uint16_t address;
    std::uint16_t value;
};

struct StubServer {
    std::vector<StubRequest> requests;
    bool coil = false;
    std::optional<std::uint8_t> exception_code;
    bool die_on_first_request = false;
    bool never_answer = false;
    std::optional<std::uint8_t> answer_with_unit_id;
    std::optional<std::uint16_t> echo_address_instead;
    std::optional<std::uint16_t> answer_with_transaction_id;
};

std::vector<std::uint8_t> stub_frame(std::uint16_t transaction_id, std::uint8_t unit_id,
                                     const std::vector<std::uint8_t>& pdu) {
    std::vector<std::uint8_t> frame{
        static_cast<std::uint8_t>(transaction_id >> 8),
        static_cast<std::uint8_t>(transaction_id & 0xFF),
        0,
        0,
        static_cast<std::uint8_t>((pdu.size() + 1) >> 8),
        static_cast<std::uint8_t>((pdu.size() + 1) & 0xFF),
        unit_id};
    frame.insert(frame.end(), pdu.begin(), pdu.end());
    return frame;
}

asio::awaitable<void> run_stub(asio::ip::tcp::acceptor& acceptor, StubServer& stub,
                               int max_requests) {
    auto socket = co_await acceptor.async_accept(asio::use_awaitable);
    for (int served = 0; served < max_requests; ++served) {
        std::vector<std::uint8_t> frame(modbus::kLengthPrefixBytes);
        const auto [header_ec, header_size] = co_await asio::async_read(
            socket, asio::buffer(frame), asio::as_tuple(asio::use_awaitable));
        if (header_ec) {
            co_return;
        }
        const std::uint16_t transaction_id =
            static_cast<std::uint16_t>((frame[0] << 8) | frame[1]);
        const std::size_t length = static_cast<std::size_t>((frame[4] << 8) | frame[5]);
        std::vector<std::uint8_t> body(length);
        const auto [body_ec, body_size] = co_await asio::async_read(
            socket, asio::buffer(body), asio::as_tuple(asio::use_awaitable));
        if (body_ec) {
            co_return;
        }
        const std::uint8_t unit_id = body[0];
        const std::uint8_t function = body[1];
        const auto address = static_cast<std::uint16_t>((body[2] << 8) | body[3]);
        const auto value = static_cast<std::uint16_t>((body[4] << 8) | body[5]);
        stub.requests.push_back(StubRequest{function, address, value});

        if (stub.die_on_first_request) {
            socket.close();
            co_return;
        }
        if (stub.never_answer) {
            continue;
        }

        std::vector<std::uint8_t> pdu;
        if (stub.exception_code.has_value()) {
            pdu = {static_cast<std::uint8_t>(function | modbus::kExceptionMask),
                   *stub.exception_code};
        } else if (function == modbus::kReadCoils) {
            pdu = {modbus::kReadCoils, 0x01, static_cast<std::uint8_t>(stub.coil ? 1 : 0)};
        } else {
            stub.coil = value == modbus::kCoilOn;
            const std::uint16_t echoed = stub.echo_address_instead.value_or(address);
            pdu = {modbus::kWriteSingleCoil, static_cast<std::uint8_t>(echoed >> 8),
                   static_cast<std::uint8_t>(echoed & 0xFF), body[4], body[5]};
        }
        const auto response =
            stub_frame(stub.answer_with_transaction_id.value_or(transaction_id),
                       stub.answer_with_unit_id.value_or(unit_id), pdu);
        co_await asio::async_write(socket, asio::buffer(response), asio::use_awaitable);
    }
}

struct TransportRig {
    asio::io_context io;
    asio::ip::tcp::acceptor acceptor{
        io, asio::ip::tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0)};
    ResolvedTaskSpec spec = address_conveyor_spec();
    SignalTable table;
    std::uint32_t handoff = 0;
    std::optional<CommBus> bus;
    StubServer stub;

    explicit TransportRig(double timeout_ms = 1000.0) {
        handoff = table.add("handoff_signal");
        bus.emplace(io.get_executor(), 2, table.size(), 64);
        timeout = timeout_ms;
    }

    std::expected<ModbusTcpTransport, TransportError> connect() {
        return ModbusTcpTransport::try_create(
            ModbusEndpoint{"127.0.0.1:" + std::to_string(acceptor.local_endpoint().port()),
                           1, timeout},
            spec, table, *bus, io.get_executor());
    }

    OutgoingMessage handoff_send(bool value, std::int64_t seq) const {
        return OutgoingMessage{1, Message{handoff, Cell{value}, 0, seq}};
    }

    double timeout = 1000.0;
};

TEST(ModbusTransportTest, EmitWritesTheDeclaredAddress) {
    TransportRig rig;
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;

    std::optional<std::expected<void, TransportError>> outcome;
    asio::co_spawn(rig.io, run_stub(rig.acceptor, rig.stub, 1), asio::detached);
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            outcome = co_await transport->emit(rig.handoff_send(true, 1));
        },
        asio::detached);
    rig.io.run();

    ASSERT_TRUE(outcome.has_value());
    ASSERT_TRUE(outcome->has_value()) << outcome->error().message;
    ASSERT_EQ(rig.stub.requests.size(), 1u);
    EXPECT_EQ(rig.stub.requests[0].function, modbus::kWriteSingleCoil);
    EXPECT_EQ(rig.stub.requests[0].address, 0);
    EXPECT_EQ(rig.stub.requests[0].value, modbus::kCoilOn);
}

TEST(ModbusTransportTest, PollReadsOnlyTheConsumedSignals) {
    TransportRig rig;
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;

    std::optional<std::vector<PolledValue>> producer_side;
    std::optional<std::vector<PolledValue>> consumer_side;
    asio::co_spawn(rig.io, run_stub(rig.acceptor, rig.stub, 2), asio::detached);
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            co_await transport->emit(rig.handoff_send(true, 1));
            producer_side = *co_await transport->poll(0);
            consumer_side = *co_await transport->poll(1);
        },
        asio::detached);
    rig.io.run();

    ASSERT_TRUE(producer_side.has_value());
    EXPECT_TRUE(producer_side->empty())
        << "plc_a produces handoff_signal and consumes nothing; it must issue no reads";
    ASSERT_TRUE(consumer_side.has_value());
    ASSERT_EQ(consumer_side->size(), 1u);
    EXPECT_EQ((*consumer_side)[0].signal_id, rig.handoff);
    EXPECT_TRUE(is_truthy((*consumer_side)[0].value));

    ASSERT_EQ(rig.stub.requests.size(), 2u);
    EXPECT_EQ(rig.stub.requests[1].function, modbus::kReadCoils);
    EXPECT_EQ(rig.stub.requests[1].address, 0);
}

TEST(ModbusTransportTest, PollDrainsPlantRoutedBusTraffic) {
    TransportRig rig;
    const std::uint32_t sensor = rig.table.add("part_at_b");
    rig.bus.emplace(rig.io.get_executor(), 2, rig.table.size(), 64);
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;

    ASSERT_TRUE(rig.bus->try_send(1, Message{sensor, Cell{true}, kNoSender, 3}));

    std::optional<std::vector<PolledValue>> polled;
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> { polled = *co_await transport->poll(1); },
        asio::detached);
    rig.io.run();

    ASSERT_TRUE(polled.has_value());
    ASSERT_EQ(polled->size(), 1u)
        << "a plant route rides the in-process channel in both transports; a "
           "register-only poll leaves every spec with a plant deaf";
    EXPECT_EQ((*polled)[0].signal_id, sensor);
    EXPECT_EQ((*polled)[0].sender_plc, kNoSender);
    EXPECT_EQ((*polled)[0].seq, 3);
    EXPECT_TRUE(rig.stub.requests.empty());
}

TEST(ModbusTransportTest, PollStampsSenderFromTheRegisterMap) {
    TransportRig rig;
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;

    std::optional<std::vector<PolledValue>> polled;
    asio::co_spawn(rig.io, run_stub(rig.acceptor, rig.stub, 2), asio::detached);
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            co_await transport->emit(rig.handoff_send(true, 1));
            polled = *co_await transport->poll(1);
        },
        asio::detached);
    rig.io.run();

    ASSERT_TRUE(polled.has_value());
    ASSERT_EQ(polled->size(), 1u);
    EXPECT_EQ((*polled)[0].sender_plc, 0u)
        << "Modbus carries no sender field; identity comes from the declared "
           "produced_by, and leaving kNoSender here makes CAUSES unattributable";
}

TEST(ModbusTransportTest, SeqStampsTheLastAcknowledgedWrite) {
    TransportRig rig;
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;

    std::vector<std::int64_t> stamps;
    asio::co_spawn(rig.io, run_stub(rig.acceptor, rig.stub, 4), asio::detached);
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            co_await transport->emit(rig.handoff_send(false, 4));
            stamps.push_back((*co_await transport->poll(1))[0].seq);
            co_await transport->emit(rig.handoff_send(true, 5));
            stamps.push_back((*co_await transport->poll(1))[0].seq);
        },
        asio::detached);
    rig.io.run();

    EXPECT_EQ(stamps, (std::vector<std::int64_t>{4, 5}));
}

TEST(ModbusTransportTest, NoReceiptBeforeTheFirstAcknowledgedWrite) {
    TransportRig rig;
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;
    rig.stub.coil = true;

    std::optional<std::vector<PolledValue>> polled;
    asio::co_spawn(rig.io, run_stub(rig.acceptor, rig.stub, 1), asio::detached);
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            polled = *co_await transport->poll(1);
            co_await transport->emit(rig.handoff_send(true, 1));
        },
        asio::detached);
    rig.io.run();

    ASSERT_TRUE(polled.has_value());
    EXPECT_TRUE(polled->empty())
        << "value and receipt are one unit; folding a coil with no acknowledged "
           "write would promote a tag that recvs denies ever arrived";
    ASSERT_EQ(rig.stub.requests.size(), 1u);
    EXPECT_EQ(rig.stub.requests[0].function, modbus::kWriteSingleCoil)
        << "the poll must issue no read at all before an acknowledged write";
}

TEST(ModbusTransportTest, ExceptionResponseFailsTheScan) {
    TransportRig rig;
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;
    rig.stub.exception_code = modbus::kIllegalDataAddress;

    std::optional<std::expected<void, TransportError>> outcome;
    asio::co_spawn(rig.io, run_stub(rig.acceptor, rig.stub, 1), asio::detached);
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            outcome = co_await transport->emit(rig.handoff_send(true, 1));
        },
        asio::detached);
    rig.io.run();

    ASSERT_TRUE(outcome.has_value());
    ASSERT_FALSE(outcome->has_value());
    EXPECT_NE(outcome->error().message.find("exception code 2"), std::string::npos)
        << outcome->error().message;
}

// The three fatal paths that turn a subtly-wrong server into a run halt rather
// than a silently-wrong trace. Each must also latch: a transport that reported
// the frame and kept going would fold the next answer it could not trust.
struct MisbehavingServerCase {
    const char* name;
    void (*misbehave)(StubServer&);
    const char* expected_fragment;
};

class ModbusMisbehavingServerTest
    : public ::testing::TestWithParam<MisbehavingServerCase> {};

TEST_P(ModbusMisbehavingServerTest, IsFatalAndLatches) {
    TransportRig rig;
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;
    GetParam().misbehave(rig.stub);

    std::vector<std::string> failures;
    asio::co_spawn(rig.io, run_stub(rig.acceptor, rig.stub, 2), asio::detached);
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            auto first = co_await transport->emit(rig.handoff_send(true, 1));
            if (!first) failures.push_back(first.error().message);
            auto second = co_await transport->emit(rig.handoff_send(true, 2));
            if (!second) failures.push_back(second.error().message);
        },
        asio::detached);
    rig.io.run();

    ASSERT_EQ(failures.size(), 2u);
    EXPECT_NE(failures[0].find(GetParam().expected_fragment), std::string::npos)
        << failures[0];
    EXPECT_EQ(failures[1], failures[0]) << "the failure must latch, not recur";
    EXPECT_EQ(rig.stub.requests.size(), 1u)
        << "a failed transport must not touch the socket again";
}

INSTANTIATE_TEST_SUITE_P(
    ProtocolViolations, ModbusMisbehavingServerTest,
    ::testing::Values(
        MisbehavingServerCase{"WriteEchoMismatch",
                              [](StubServer& stub) { stub.echo_address_instead = 9; },
                              "acknowledged for coil 9"},
        MisbehavingServerCase{"UnitIdMismatch",
                              [](StubServer& stub) { stub.answer_with_unit_id = 7; },
                              "unit id 7"},
        MisbehavingServerCase{"UnknownTransactionId",
                              [](StubServer& stub) {
                                  stub.answer_with_transaction_id = 4242;
                              },
                              "transaction id 4242"}),
    [](const ::testing::TestParamInfo<MisbehavingServerCase>& info) {
        return info.param.name;
    });

TEST(ModbusTransportTest, ServerDeathFailsSubsequentCallsFast) {
    TransportRig rig;
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;
    rig.stub.die_on_first_request = true;

    std::vector<std::string> failures;
    asio::co_spawn(rig.io, run_stub(rig.acceptor, rig.stub, 1), asio::detached);
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            auto first = co_await transport->emit(rig.handoff_send(true, 1));
            if (!first) failures.push_back(first.error().message);
            auto second = co_await transport->emit(rig.handoff_send(true, 2));
            if (!second) failures.push_back(second.error().message);
        },
        asio::detached);
    rig.io.run();

    ASSERT_EQ(failures.size(), 2u);
    EXPECT_EQ(rig.stub.requests.size(), 1u)
        << "a dead transport must not touch the socket again";
}

TEST(ModbusTransportTest, TimeoutIsFatalNotRetried) {
    TransportRig rig(20.0);
    auto transport = rig.connect();
    ASSERT_TRUE(transport.has_value()) << transport.error().message;
    rig.stub.never_answer = true;

    std::vector<std::string> failures;
    asio::co_spawn(rig.io, run_stub(rig.acceptor, rig.stub, 2), asio::detached);
    asio::co_spawn(
        rig.io,
        [&]() -> asio::awaitable<void> {
            auto first = co_await transport->emit(rig.handoff_send(true, 1));
            if (!first) failures.push_back(first.error().message);
            auto second = co_await transport->emit(rig.handoff_send(true, 2));
            if (!second) failures.push_back(second.error().message);
        },
        asio::detached);
    rig.io.run();

    ASSERT_EQ(failures.size(), 2u);
    EXPECT_NE(failures[0].find("timed out"), std::string::npos) << failures[0];
    EXPECT_EQ(failures[1], failures[0]) << "a timeout is fatal, not retried";
    EXPECT_EQ(rig.stub.requests.size(), 1u);
}

TEST(ModbusTransportTest, TagSpecCarriesNoBindingToAddress) {
    TransportRig rig;
    rig.spec.comm.strategy = "tag";
    rig.spec.comm.signals = {
        ResolvedSignal{"handoff_signal", "plc_a", {"plc_b"}, std::nullopt, std::nullopt}};
    const auto transport = rig.connect();
    ASSERT_FALSE(transport.has_value());
    EXPECT_NE(transport.error().message.find("no register binding"), std::string::npos)
        << transport.error().message;
}

TEST(ModbusTransportTest, BuildSelectsInProcessWithoutAnEndpoint) {
    asio::io_context io;
    SignalTable table;
    CommBus bus(io.get_executor(), 2, table.size(), 64);
    const auto transport = build_comm_transport(std::nullopt, address_conveyor_spec(),
                                                table, bus, io.get_executor());
    ASSERT_TRUE(transport.has_value()) << transport.error().message;
    EXPECT_TRUE(std::holds_alternative<InProcessTransport>(*transport));
}

}  // namespace
}  // namespace relay_host
