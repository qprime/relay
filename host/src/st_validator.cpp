#include "relay_host/st_validator.hpp"

#include <algorithm>
#include <cctype>

namespace relay_host {

namespace {

bool starts_with(std::string_view text, std::string_view prefix) {
    return text.substr(0, prefix.size()) == prefix;
}

std::string to_lower(std::string_view text) {
    std::string lower;
    lower.reserve(text.size());
    for (const char c : text) {
        lower.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
    }
    return lower;
}

std::optional<TimerField> timer_field_of(std::string_view attr) {
    const std::string lower = to_lower(attr);
    if (lower == "preset_ms") return TimerField::PresetMs;
    if (lower == "accumulated_ms") return TimerField::AccumulatedMs;
    if (lower == "running") return TimerField::Running;
    if (lower == "done") return TimerField::Done;
    if (lower == "q") return TimerField::Q;
    return std::nullopt;
}

std::string known_plc_ids(std::span<const std::string> plc_ids) {
    if (plc_ids.empty()) {
        return "(none registered)";
    }
    std::vector<std::string> sorted(plc_ids.begin(), plc_ids.end());
    std::sort(sorted.begin(), sorted.end());
    std::string joined;
    for (const std::string& id : sorted) {
        if (!joined.empty()) {
            joined += ", ";
        }
        joined += id;
    }
    return joined;
}

class SlotResolver {
 public:
    SlotResolver(StProgram& program, const SignalTable& table,
                 std::span<const std::string> plc_ids)
        : program_(program), table_(table), plc_ids_(plc_ids) {}

    std::expected<void, ValidateError> run() {
        for (Statement& stmt : program_.statements) {
            if (auto resolved = resolve_statement(stmt); !resolved) {
                return resolved;
            }
        }
        for (Expr& expr : program_.exprs) {
            if (auto resolved = resolve_expr(expr); !resolved) {
                return resolved;
            }
        }
        return {};
    }

    std::vector<SlotBinding> take_slots() { return std::move(slots_); }
    std::vector<TimerDef> take_timers() { return std::move(timers_); }

 private:
    std::expected<void, ValidateError> resolve_statement(Statement& stmt) {
        if (Assignment* assignment = std::get_if<Assignment>(&stmt)) {
            auto slot = slot_for(assignment->target);
            if (!slot) {
                return std::unexpected(slot.error());
            }
            assignment->slot = *slot;
            return {};
        }
        if (TimerCall* call = std::get_if<TimerCall>(&stmt)) {
            call->timer_slot = timer_for(call->timer, call->preset_ms);
            return {};
        }
        IfBlock& block = std::get<IfBlock>(stmt);
        for (Statement& inner : block.body) {
            if (auto resolved = resolve_statement(inner); !resolved) {
                return resolved;
            }
        }
        return {};
    }

    std::expected<void, ValidateError> resolve_expr(Expr& expr) {
        if (VarRef* ref = std::get_if<VarRef>(&expr)) {
            auto slot = slot_for(ref->name);
            if (!slot) {
                return std::unexpected(slot.error());
            }
            ref->slot = *slot;
            return {};
        }
        if (TimerAttr* attr = std::get_if<TimerAttr>(&expr)) {
            const auto field = timer_field_of(attr->attr);
            if (!field) {
                return std::unexpected(ValidateError{
                    "st_validator: unknown timer attribute '" + attr->attr + "' on '" +
                    attr->timer +
                    "'; known attributes: accumulated_ms, done, preset_ms, q, running"});
            }
            attr->field = *field;
            const auto timer_it = timer_slots_.find(attr->timer);
            if (timer_it == timer_slots_.end()) {
                return std::unexpected(ValidateError{
                    "st_validator: '" + attr->timer + "." + attr->attr + "' references '" +
                    attr->timer + "', which is not a declared timer instance"});
            }
            attr->timer_slot = timer_it->second;
            return {};
        }
        return {};
    }

    std::expected<std::uint32_t, ValidateError> slot_for(const std::string& name) {
        const auto it = slot_ids_.find(name);
        if (it != slot_ids_.end()) {
            return it->second;
        }
        SlotBinding binding{name, SlotKind::Output, kNoSignal, 0};
        if (starts_with(name, kScratchPrefix)) {
            binding.kind = SlotKind::Scratch;
        } else if (starts_with(name, kSendPrefix)) {
            const auto target = parse_send_target(name, plc_ids_);
            if (!target) {
                return std::unexpected(ValidateError{
                    "st_validator: _send_* assignment '" + name +
                    "' does not resolve to a registered plc_id; known plc_ids: " +
                    known_plc_ids(plc_ids_)});
            }
            const auto key_id = table_.find_id(target->key);
            if (!key_id) {
                return std::unexpected(ValidateError{
                    "st_validator: send key '" + target->key + "' from '" + name +
                    "' is not in the signal table; the table must be built from the same "
                    "parsed blocks"});
            }
            binding.kind = SlotKind::Send;
            binding.signal_id = *key_id;
            binding.send_target_plc = target->target_plc_index;
        } else {
            const auto id = table_.find_id(name);
            binding.signal_id = id ? *id : kNoSignal;
        }
        const std::uint32_t slot = static_cast<std::uint32_t>(slots_.size());
        slots_.push_back(std::move(binding));
        slot_ids_.emplace(name, slot);
        return slot;
    }

    std::uint32_t timer_for(const std::string& name, double preset_ms) {
        const auto it = timer_slots_.find(name);
        if (it != timer_slots_.end()) {
            return it->second;
        }
        const std::uint32_t slot = static_cast<std::uint32_t>(timers_.size());
        timers_.push_back(TimerDef{name, preset_ms});
        timer_slots_.emplace(name, slot);
        return slot;
    }

    StProgram& program_;
    const SignalTable& table_;
    std::span<const std::string> plc_ids_;
    std::vector<SlotBinding> slots_;
    std::vector<TimerDef> timers_;
    std::unordered_map<std::string, std::uint32_t> slot_ids_;
    std::unordered_map<std::string, std::uint32_t> timer_slots_;
};

void collect_targets(const Statement& stmt, std::span<const std::string> plc_ids,
                     SignalTable& table) {
    if (const Assignment* assignment = std::get_if<Assignment>(&stmt)) {
        const std::string& name = assignment->target;
        if (starts_with(name, kScratchPrefix)) {
            return;
        }
        if (starts_with(name, kSendPrefix)) {
            if (const auto target = parse_send_target(name, plc_ids)) {
                table.add(target->key);
            }
            return;
        }
        table.add(name);
        return;
    }
    if (const IfBlock* block = std::get_if<IfBlock>(&stmt)) {
        for (const Statement& inner : block->body) {
            collect_targets(inner, plc_ids, table);
        }
    }
}

}  // namespace

std::optional<SendTarget> parse_send_target(std::string_view name,
                                            std::span<const std::string> plc_ids) {
    if (!starts_with(name, kSendPrefix)) {
        return std::nullopt;
    }
    const std::string_view rest = name.substr(kSendPrefix.size());
    std::optional<std::uint32_t> match;
    std::size_t match_len = 0;
    for (std::uint32_t index = 0; index < plc_ids.size(); ++index) {
        const std::string& plc_id = plc_ids[index];
        if (rest.size() > plc_id.size() && starts_with(rest, plc_id) &&
            rest[plc_id.size()] == '_' && plc_id.size() > match_len) {
            match = index;
            match_len = plc_id.size();
        }
    }
    if (!match || rest.size() <= match_len + 1) {
        return std::nullopt;
    }
    return SendTarget{*match, std::string(rest.substr(match_len + 1))};
}

std::expected<ValidatedSt, ValidateError> ValidatedSt::try_from(
    StProgram program, const SignalTable& table, std::span<const std::string> plc_ids) {
    ValidatedSt validated;
    validated.program_ = std::move(program);
    SlotResolver resolver(validated.program_, table, plc_ids);
    if (auto resolved = resolver.run(); !resolved) {
        return std::unexpected(resolved.error());
    }
    validated.slots_ = resolver.take_slots();
    validated.timers_ = resolver.take_timers();
    for (std::uint32_t slot = 0; slot < validated.slots_.size(); ++slot) {
        const SlotBinding& binding = validated.slots_[slot];
        if (binding.kind == SlotKind::Output && binding.signal_id != kNoSignal) {
            validated.signal_to_slot_.emplace(binding.signal_id, slot);
        }
    }
    return validated;
}

std::optional<std::uint32_t> ValidatedSt::slot_of_signal(std::uint32_t signal_id) const {
    const auto it = signal_to_slot_.find(signal_id);
    if (it == signal_to_slot_.end()) {
        return std::nullopt;
    }
    return it->second;
}

SignalTable build_signal_table(const ResolvedTaskSpec& spec,
                               std::span<const StProgram> programs) {
    SignalTable table;
    for (const ResolvedTag& tag : spec.comm.tags) {
        table.add(tag.name);
    }
    for (const ResolvedRoute& route : spec.plant.routes) {
        table.add(route.as_key);
    }
    for (const ResolvedActuator& actuator : spec.plant.actuators) {
        table.add(actuator.key);
        table.add(actuator.as);
    }
    for (const StProgram& program : programs) {
        for (const Statement& stmt : program.statements) {
            collect_targets(stmt, spec.plc_ids, table);
        }
    }
    return table;
}

}  // namespace relay_host
