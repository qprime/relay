#include "relay_host/verify/verdict.hpp"

#include <array>
#include <cstdio>
#include <string>
#include <tuple>

#include "relay_host/json_text.hpp"
#include "relay_host/verify/assertion_parser.hpp"

namespace relay_host::verify {

namespace {

std::string fixed1(double value) {
    std::array<char, 64> buf;
    std::snprintf(buf.data(), buf.size(), "%.1f", value);
    return std::string(buf.data());
}

std::string quoted(std::string_view name) {
    return "'" + std::string(name) + "'";
}

// outputs before the I/O image, matching relay/verify/assertions.py: an output
// the block wrote this scan is what the name denotes on the PLC that wrote it.
const Cell* signal_value(const TraceRecord& record, std::string_view name) {
    if (const auto it = record.outputs.find(name); it != record.outputs.end()) {
        return &it->second;
    }
    if (const auto it = record.io_snapshot.find(name); it != record.io_snapshot.end()) {
        return &it->second;
    }
    return nullptr;
}

bool signal_true(const TraceRecord& record, std::string_view name) {
    const Cell* cell = signal_value(record, name);
    return cell != nullptr && is_truthy(*cell);
}

// Derived from the trace, never from the spec: the trace is the verifier's sole
// input, and a rule that consults Comm.tags diverges wherever the two disagree.
bool is_comm_tag(std::string_view name, const Trace& trace) {
    for (const TraceRecord& record : trace.records) {
        if (record.sends.contains(name)) {
            return true;
        }
    }
    return false;
}

// Selection is minimum-by-(elapsed_ms, plc_id). Record order is scan-completion
// order, which is nondeterministic under free-running clocks, so "first in the
// list" names a property of the container rather than of the trace.
template <typename Pred>
const TraceRecord* earliest(const Trace& trace, Pred pred) {
    const TraceRecord* best = nullptr;
    for (const TraceRecord& record : trace.records) {
        if (!pred(record)) {
            continue;
        }
        if (best == nullptr || std::tie(record.elapsed_ms, record.plc_id) <
                                   std::tie(best->elapsed_ms, best->plc_id)) {
            best = &record;
        }
    }
    return best;
}

// Deliberately a different key from `earliest`: this one orders by tick, not by
// elapsed_ms, because its only caller is CAUSES — a form that reads no clock on
// the pass/fail path, which is what lets it survive independent per-PLC clocks.
// Within one PLC the two orderings coincide today; #23 does not change that,
// since elapsed_ms stays monotonic in tick for a given PLC. Do not "unify" the
// two by giving this one elapsed_ms.
template <typename Pred>
const TraceRecord* earliest_by_tick_on_plc(const Trace& trace, std::string_view plc_id,
                                           Pred pred) {
    const TraceRecord* best = nullptr;
    for (const TraceRecord& record : trace.records) {
        if (record.plc_id != plc_id || !pred(record)) {
            continue;
        }
        if (best == nullptr || record.tick < best->tick) {
            best = &record;
        }
    }
    return best;
}

bool sent_truthy(const TraceRecord& record, std::string_view name) {
    const auto it = record.sends.find(name);
    return it != record.sends.end() && is_truthy(it->second.value);
}

bool received_truthy(const TraceRecord& record, std::string_view name) {
    const auto it = record.recvs.find(name);
    return it != record.recvs.end() && is_truthy(it->second.value);
}

// A comm tag is emitted on its producer and delivered to its consumers, and the
// merged signal view cannot tell those apart — the producer never writes a tag
// to its own output image, so a tag read through `signal_value` resolves on
// whichever consumer appears first. Reading it from `sends` anchors it to the
// emission the name actually denotes. The value is read, not merely counted: a
// producer that sends every scan carries false long before the real event.
std::optional<double> first_true_ms(std::string_view name, const Trace& trace) {
    const TraceRecord* record =
        is_comm_tag(name, trace)
            ? earliest(trace, [&](const TraceRecord& r) { return sent_truthy(r, name); })
            : earliest(trace, [&](const TraceRecord& r) { return signal_true(r, name); });
    if (record == nullptr) {
        return std::nullopt;
    }
    return record->elapsed_ms;
}

AssertionResult failed(std::string assertion, std::string reason) {
    return AssertionResult{std::move(assertion), false, std::move(reason),
                           std::nullopt,         std::nullopt, std::nullopt};
}

AssertionResult check_eventually(std::string assertion, const std::string& signal,
                                 double within_ms, const Trace& trace) {
    const std::optional<double> first = first_true_ms(signal, trace);
    if (first.has_value() && *first <= within_ms) {
        return AssertionResult{std::move(assertion),
                               true,
                               "signal " + quoted(signal) + " true at " + fixed1(*first) +
                                   "ms",
                               std::nullopt,
                               *first,
                               std::nullopt};
    }
    if (first.has_value()) {
        return AssertionResult{std::move(assertion),
                               false,
                               "signal " + quoted(signal) + " first true at " +
                                   fixed1(*first) + "ms, after the " +
                                   format_json_double(within_ms) + "ms budget",
                               std::nullopt,
                               *first,
                               std::nullopt};
    }
    return failed(std::move(assertion), "signal " + quoted(signal) + " never true within " +
                                            format_json_double(within_ms) + "ms");
}

// Ordering is non-strict and is checked before the budget: two signals true in
// the same scan share an elapsed_ms, and a reversed pair reports the ordering
// violation rather than a budget overrun that would mislead. observed_gap_ms is
// set whenever both endpoints became true, pass or fail.
AssertionResult check_precedes(std::string assertion, const std::string& first,
                               const std::string& second, double budget_ms,
                               const Trace& trace) {
    const std::optional<double> first_ms = first_true_ms(first, trace);
    const std::optional<double> second_ms = first_true_ms(second, trace);
    if (!first_ms.has_value()) {
        return failed(std::move(assertion), "signal " + quoted(first) + " never became true");
    }
    if (!second_ms.has_value()) {
        return failed(std::move(assertion), "signal " + quoted(second) + " never became true");
    }
    const double gap = *second_ms - *first_ms;
    if (gap < 0.0) {
        return AssertionResult{std::move(assertion),
                               false,
                               quoted(second) + " at " + fixed1(*second_ms) +
                                   "ms preceded " + quoted(first) + " at " +
                                   fixed1(*first_ms) + "ms (gap " + fixed1(gap) + "ms)",
                               gap,
                               std::nullopt,
                               std::nullopt};
    }
    if (gap > budget_ms) {
        return AssertionResult{std::move(assertion),
                               false,
                               quoted(first) + " at " + fixed1(*first_ms) + "ms precedes " +
                                   quoted(second) + " at " + fixed1(*second_ms) +
                                   "ms but gap " + fixed1(gap) + "ms exceeds budget " +
                                   fixed1(budget_ms) + "ms",
                               gap,
                               std::nullopt,
                               std::nullopt};
    }
    return AssertionResult{std::move(assertion),
                           true,
                           quoted(first) + " at " + fixed1(*first_ms) + "ms precedes " +
                               quoted(second) + " at " + fixed1(*second_ms) + "ms (gap " +
                               fixed1(gap) + "ms, budget " + fixed1(budget_ms) + "ms)",
                           gap,
                           std::nullopt,
                           std::nullopt};
}

// Attribution, not timing. Every part of the claim is read from the receipt
// where the message was delivered: an output sharing the tag's name would
// shadow a false delivery through `signal_value`, and sequence numbers are
// per-sender, so two senders of one key have overlapping number spaces and a
// count search alone can land on a PLC that never sent to this consumer.
AssertionResult check_causes(std::string assertion, const std::string& cause,
                             const std::string& effect, const Trace& trace) {
    const TraceRecord* acting =
        earliest(trace, [&](const TraceRecord& r) { return signal_true(r, effect); });
    if (acting == nullptr) {
        return failed(std::move(assertion),
                      "signal " + quoted(effect) + " never became true");
    }

    const std::string plc_id = acting->plc_id;
    const std::string effect_where = quoted(effect) + " became true on " + quoted(plc_id) +
                                     " at tick " + std::to_string(acting->tick);
    const TraceRecord* activating =
        earliest_by_tick_on_plc(trace, plc_id, [&](const TraceRecord& r) {
            return received_truthy(r, cause) && r.tick <= acting->tick;
        });
    if (activating == nullptr) {
        const TraceRecord* any_receipt = earliest_by_tick_on_plc(
            trace, plc_id, [&](const TraceRecord& r) { return r.recvs.contains(cause); });
        if (any_receipt == nullptr) {
            return failed(std::move(assertion), effect_where + " but " + quoted(cause) +
                                                    " was never received there");
        }
        const TraceRecord* truthy_receipt = earliest_by_tick_on_plc(
            trace, plc_id, [&](const TraceRecord& r) { return received_truthy(r, cause); });
        if (truthy_receipt == nullptr) {
            return failed(std::move(assertion),
                          effect_where + " but every received " + quoted(cause) +
                              " message carried a false value");
        }
        return failed(std::move(assertion),
                      quoted(effect) + " became true on " + quoted(plc_id) + " at tick " +
                          std::to_string(acting->tick) + ", before the first activating " +
                          quoted(cause) + " receipt at tick " +
                          std::to_string(truthy_receipt->tick));
    }

    const ReceiptEntry& receipt = activating->recvs.find(cause)->second;
    if (!receipt.sender.has_value()) {
        return failed(std::move(assertion),
                      quoted(cause) + " received on " + quoted(plc_id) + " at tick " +
                          std::to_string(activating->tick) +
                          " records no sender; plant-routed and strategy-routed signals "
                          "are not attributable");
    }

    // Identity is already settled by receipt.sender; this locates which of that
    // sender's scans emitted the seq. `sends` records the scan's high-water
    // count, so a multi-consumer tag emitting two messages of one key in a
    // single scan stores only the last — hence `>=` rather than exact match.
    const TraceRecord* sender =
        earliest_by_tick_on_plc(trace, *receipt.sender, [&](const TraceRecord& r) {
            const auto it = r.sends.find(cause);
            return it != r.sends.end() && it->second.count >= receipt.seq;
        });
    if (sender == nullptr) {
        return failed(std::move(assertion),
                      quoted(cause) + " receipt seq " + std::to_string(receipt.seq) +
                          " on " + quoted(plc_id) + " claims sender " +
                          quoted(*receipt.sender) + ", which recorded no such send");
    }

    return AssertionResult{
        std::move(assertion),
        true,
        quoted(effect) + " true on " + quoted(plc_id) + " at tick " +
            std::to_string(acting->tick) + " is caused by " + quoted(cause) + " seq " +
            std::to_string(receipt.seq) + " sent by " + quoted(*receipt.sender) +
            " at tick " + std::to_string(sender->tick) + " and received at tick " +
            std::to_string(activating->tick),
        std::nullopt,
        std::nullopt,
        Attribution{plc_id, acting->tick, *receipt.sender, receipt.seq, sender->tick,
                    activating->tick}};
}

std::string trimmed(std::string_view text) {
    constexpr std::string_view kSpace = " \t\n\r\f\v";
    const std::size_t begin = text.find_first_not_of(kSpace);
    if (begin == std::string_view::npos) {
        return std::string();
    }
    const std::size_t end = text.find_last_not_of(kSpace);
    return std::string(text.substr(begin, end - begin + 1));
}

}  // namespace

AssertionResult evaluate_assertion(std::string_view assertion, const Trace& trace) {
    const std::string text = trimmed(assertion);
    const std::optional<ParsedAssertion> parsed = parse_assertion(assertion);
    if (!parsed.has_value()) {
        return failed(text, "unrecognized assertion form: " + text);
    }
    switch (parsed->form) {
        case AssertionForm::Causes:
            return check_causes(text, parsed->signals[0], parsed->signals[1], trace);
        case AssertionForm::Eventually:
            return check_eventually(text, parsed->signals[0], *parsed->within_ms, trace);
        case AssertionForm::Precedes:
            return check_precedes(text, parsed->signals[0], parsed->signals[1],
                                  *parsed->within_ms, trace);
    }
    return failed(text, "unrecognized assertion form: " + text);
}

std::vector<AssertionResult> evaluate_all(std::span<const std::string> assertions,
                                          const Trace& trace) {
    std::vector<AssertionResult> results;
    results.reserve(assertions.size());
    for (const std::string& assertion : assertions) {
        results.push_back(evaluate_assertion(assertion, trace));
    }
    return results;
}

}  // namespace relay_host::verify
