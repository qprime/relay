#include "relay_host/scan_executor.hpp"

#include <chrono>

namespace relay_host {

PlcScanState::PlcScanState(std::uint32_t plc_index_arg, const ValidatedSt* block_arg,
                           std::uint32_t signal_count)
    : plc_index(plc_index_arg), block(block_arg), ctx(*block_arg),
      io_cells(signal_count), send_counts(signal_count, 0) {}

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
    entry.send_count = 0;
    entry.recv_count = 0;
    entry.error.reset();
    for (const std::uint32_t signal_id : comm.present_ids()) {
        if (entry.recv_count >= kMaxCellsPerScan) {
            entry.error = ScanError{ScanErrorKind::CellOverflow, signal_id};
            return entry.error;
        }
        const std::optional<Receipt> receipt = comm.receipt_of(signal_id);
        const std::optional<Cell> delivered = comm.get(signal_id);
        if (!receipt.has_value() || !delivered.has_value()) {
            continue;
        }
        entry.recv_slots[entry.recv_count] = ReceiptSlot{
            signal_id, receipt->sender_plc, receipt->seq, *delivered};
        ++entry.recv_count;
    }
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
            case SlotKind::Send: {
                if (outgoing.count >= outgoing.items.size()) {
                    entry.error =
                        ScanError{ScanErrorKind::OutgoingOverflow, binding.signal_id};
                    return entry.error;
                }
                if (entry.send_count >= kMaxCellsPerScan) {
                    entry.error =
                        ScanError{ScanErrorKind::CellOverflow, binding.signal_id};
                    return entry.error;
                }
                const std::int64_t seq = ++state.send_counts[binding.signal_id];
                outgoing.items[outgoing.count] = OutgoingMessage{
                    binding.send_target_plc,
                    Message{binding.signal_id, value, state.plc_index, seq}};
                ++outgoing.count;
                entry.send_slots[entry.send_count] = SeqSlot{binding.signal_id, seq};
                ++entry.send_count;
                break;
            }
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
    SimClock clock = SimClock::zero();
    IOImage prior_outputs = IOImage::empty();
    std::vector<std::int64_t> routed_send_counts(ctx.table->size(), 0);
    std::vector<Routed> routed_copy;
    asio::steady_timer timer(co_await asio::this_coro::executor);
    const auto period = std::chrono::duration_cast<asio::steady_timer::duration>(
        std::chrono::duration<double, std::milli>(ctx.scan_period_ms));
    auto deadline = asio::steady_timer::clock_type::now();

    for (std::int64_t scan = 0; scan < ctx.max_scans && !ctx.run->stop; ++scan) {
        deadline += period;
        timer.expires_at(deadline);
        const auto [wait_ec] =
            co_await timer.async_wait(asio::as_tuple(asio::use_awaitable));
        if (wait_ec || ctx.run->stop) {
            break;
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
            ctx.run->error =
                RunError{RunErrorKind::ScanFailed, ctx.plc_index, *error, {}};
            ctx.run->stop = true;
            break;
        }
        for (std::uint32_t index = 0; index < outgoing.count; ++index) {
            const OutgoingMessage& message = outgoing.items[index];
            co_await ctx.bus->send(message.target_plc, message.msg);
        }

        IOImage outputs = IOImage::empty();
        for (std::uint32_t cell = 0; cell < ctx.state->output_count; ++cell) {
            outputs = outputs.with_value(
                ctx.table->name_of(ctx.state->outputs[cell].signal_id),
                ctx.state->outputs[cell].value);
        }
        const std::span<const Routed> routed = std::visit(
            [&](auto& strategy) {
                return strategy.route(ctx.plc_index, outputs, prior_outputs);
            },
            *ctx.strategy);
        routed_copy.assign(routed.begin(), routed.end());
        for (const Routed& message : routed_copy) {
            const std::int64_t seq = ++routed_send_counts[message.signal_id];
            co_await ctx.bus->send(
                message.consumer_index,
                Message{message.signal_id, message.value, kNoSender, seq});
        }

        *ctx.latest_output = outputs;
        prior_outputs = std::move(outputs);
        clock = clock.advance(ctx.scan_period_ms);
    }
    ++ctx.run->plcs_done;
    co_return;
}

}  // namespace relay_host
