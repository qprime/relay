#include "relay_host/comm_strategy.hpp"

#include <algorithm>

namespace relay_host {

namespace {

std::optional<std::uint32_t> index_of(std::span<const std::string> plc_ids,
                                      const std::string& plc_id) {
    const auto it = std::find(plc_ids.begin(), plc_ids.end(), plc_id);
    if (it == plc_ids.end()) {
        return std::nullopt;
    }
    return static_cast<std::uint32_t>(it - plc_ids.begin());
}

}  // namespace

std::expected<TagStrategy, StrategyError> TagStrategy::try_create(
    const ResolvedComm& comm, const SignalTable& table,
    std::span<const std::string> plc_ids) {
    TagStrategy strategy;
    for (const ResolvedTag& tag : comm.tags) {
        if (tag.name.empty() || tag.produced_by.empty()) {
            continue;
        }
        const auto producer = index_of(plc_ids, tag.produced_by);
        if (!producer) {
            return std::unexpected(StrategyError{
                "comm_strategy: tag '" + tag.name + "' produced_by '" + tag.produced_by +
                "' is not a declared plc_id"});
        }
        const auto signal_id = table.find_id(tag.name);
        if (!signal_id) {
            return std::unexpected(StrategyError{
                "comm_strategy: tag '" + tag.name +
                "' is not in the signal table; comm tags must be registered at startup"});
        }
        Tag resolved{tag.name, *signal_id, *producer, {}};
        for (const std::string& consumer : tag.consumed_by) {
            const auto consumer_index = index_of(plc_ids, consumer);
            if (!consumer_index) {
                return std::unexpected(StrategyError{
                    "comm_strategy: tag '" + tag.name + "' consumed_by '" + consumer +
                    "' is not a declared plc_id"});
            }
            resolved.consumer_indices.push_back(*consumer_index);
        }
        strategy.tags_.push_back(std::move(resolved));
    }
    return strategy;
}

std::expected<CommStrategy, StrategyError> build_comm_strategy(
    const ResolvedTaskSpec& spec, const SignalTable& table) {
    if (spec.comm.strategy == "tag") {
        auto strategy = TagStrategy::try_create(spec.comm, table, spec.plc_ids);
        if (!strategy) {
            return std::unexpected(strategy.error());
        }
        return CommStrategy{std::move(*strategy)};
    }
    return std::unexpected(StrategyError{"comm_strategy: unknown comm strategy '" +
                                         spec.comm.strategy + "'; known: tag"});
}

}  // namespace relay_host
