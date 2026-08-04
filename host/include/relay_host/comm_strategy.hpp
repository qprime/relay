#pragma once

#include <cstdint>
#include <expected>
#include <span>
#include <string>
#include <variant>
#include <vector>

#include "relay_host/io_image.hpp"
#include "relay_host/signal_table.hpp"
#include "relay_host/spec_loader.hpp"

namespace relay_host {

struct Routed {
    std::uint32_t consumer_index;
    std::uint32_t signal_id;
    Cell value;
};

struct StrategyError {
    std::string message;
};

class TagStrategy {
 public:
    [[nodiscard]] static std::expected<TagStrategy, StrategyError> try_create(
        const ResolvedComm& comm, const SignalTable& table,
        std::span<const std::string> plc_ids);

    [[nodiscard]] std::span<const Routed> route(std::uint32_t producer_index,
                                                const IOImage& outputs,
                                                const IOImage& prior_outputs,
                                                std::span<Routed> scratch) const noexcept;

    [[nodiscard]] std::size_t max_routed_per_producer() const noexcept;

 private:
    struct Tag {
        std::string name;
        std::uint32_t signal_id;
        std::uint32_t producer_index;
        std::vector<std::uint32_t> consumer_indices;
    };

    std::vector<Tag> tags_;
};

using CommStrategy = std::variant<TagStrategy>;

[[nodiscard]] std::expected<CommStrategy, StrategyError> build_comm_strategy(
    const ResolvedTaskSpec& spec, const SignalTable& table);

}  // namespace relay_host
