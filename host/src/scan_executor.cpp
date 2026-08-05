#include "relay_host/scan_executor.hpp"

namespace relay_host {

PlcScanState::PlcScanState(std::uint32_t plc_index_arg, const ValidatedSt* block_arg,
                           std::uint32_t signal_count)
    : plc_index(plc_index_arg), block(block_arg), ctx(*block_arg),
      io_cells(signal_count) {}

std::optional<ScanError> execute_one_scan(PlcScanState& state, const CommBuffer& comm,
                                          SimClock clock, double dt_ms,
                                          ScanTraceEntry& entry,
                                          OutgoingBuffer& outgoing) {
    for (const std::uint32_t signal_id : comm.present_ids()) {
        state.io_cells[signal_id] = comm.get(signal_id);
    }

    for (std::uint32_t signal_id = 0; signal_id < state.io_cells.size(); ++signal_id) {
        const std::optional<Cell>& cell = state.io_cells[signal_id];
        if (!cell.has_value()) {
            continue;
        }
        const auto slot = state.block->slot_of_signal(signal_id);
        if (slot.has_value()) {
            state.ctx.set_slot(*slot, *cell);
        }
    }

    entry.plc_index = state.plc_index;
    entry.clock = clock;
    entry.input_count = 0;
    entry.output_count = 0;
    entry.error.reset();
    for (std::uint32_t signal_id = 0; signal_id < state.io_cells.size(); ++signal_id) {
        const std::optional<Cell>& cell = state.io_cells[signal_id];
        if (!cell.has_value()) {
            continue;
        }
        if (entry.input_count >= kMaxCellsPerScan) {
            entry.error = ScanError{ScanErrorKind::CellOverflow, signal_id};
            return entry.error;
        }
        entry.input_cells[entry.input_count] = CellSlot{signal_id, *cell};
        ++entry.input_count;
    }

    state.ctx.begin_scan();
    if (const auto evaluated = evaluate(*state.block, state.ctx, dt_ms); !evaluated) {
        entry.error = ScanError{ScanErrorKind::EvalDivisionByZero, 0};
        return entry.error;
    }

    outgoing.count = 0;
    state.output_count = 0;
    for (const std::uint32_t slot : state.ctx.assigned_slots()) {
        const SlotBinding& binding = state.block->slots()[slot];
        const Cell& value = state.ctx.slot_value(slot);
        switch (binding.kind) {
            case SlotKind::Scratch:
                break;
            case SlotKind::Send:
                if (outgoing.count >= outgoing.items.size()) {
                    entry.error =
                        ScanError{ScanErrorKind::OutgoingOverflow, binding.signal_id};
                    return entry.error;
                }
                outgoing.items[outgoing.count] = OutgoingMessage{
                    binding.send_target_plc, Message{binding.signal_id, value}};
                ++outgoing.count;
                break;
            case SlotKind::Output:
                if (binding.signal_id == kNoSignal) {
                    entry.error = ScanError{ScanErrorKind::SignalLookupMiss, slot};
                    return entry.error;
                }
                if (state.output_count >= kMaxCellsPerScan) {
                    entry.error =
                        ScanError{ScanErrorKind::CellOverflow, binding.signal_id};
                    return entry.error;
                }
                state.outputs[state.output_count] = CellSlot{binding.signal_id, value};
                ++state.output_count;
                break;
        }
    }

    entry.output_count = state.output_count;
    for (std::uint32_t index = 0; index < state.output_count; ++index) {
        entry.output_cells[index] = state.outputs[index];
    }

    for (std::uint32_t index = 0; index < state.output_count; ++index) {
        const CellSlot& slot = state.outputs[index];
        state.io_cells[slot.signal_id] = slot.value;
    }

    return std::nullopt;
}

Task run_plc_scan_loop(PlcExecutionContext ctx) {
    OutgoingBuffer outgoing;
    for (std::int64_t scan = 0; scan < ctx.max_scans; ++scan) {
        const auto [clock_ec, clock] = co_await ctx.clock_chan->async_receive(
            asio::as_tuple(asio::use_awaitable));
        if (clock_ec) {
            co_return;
        }
        ctx.bus->begin_drain(ctx.plc_index);
        while (true) {
            Message msg{};
            const bool received = ctx.bus->channel_of(ctx.plc_index)
                                      .try_receive([&](asio::error_code, Message m) {
                                          msg = m;
                                      });
            if (!received) {
                break;
            }
            ctx.bus->fold(ctx.plc_index, msg);
        }
        ScanTraceEntry& entry = ctx.trace->next_entry();
        const std::optional<ScanError> error =
            execute_one_scan(*ctx.state, ctx.bus->buffer(ctx.plc_index), clock,
                             ctx.scan_period_ms, entry, outgoing);
        if (error.has_value()) {
            co_await ctx.done_chan->async_send(asio::error_code{},
                                               ScanDone{ctx.plc_index, false, *error},
                                               asio::as_tuple(asio::use_awaitable));
            co_return;
        }
        for (std::uint32_t index = 0; index < outgoing.count; ++index) {
            const OutgoingMessage& message = outgoing.items[index];
            co_await ctx.bus->send(message.target_plc, message.msg);
        }
        co_await ctx.done_chan->async_send(asio::error_code{},
                                           ScanDone{ctx.plc_index, true, std::nullopt},
                                           asio::as_tuple(asio::use_awaitable));
    }
    co_return;
}

}  // namespace relay_host
