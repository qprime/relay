#include "relay_host/st_eval.hpp"

#include <algorithm>

namespace relay_host {

void Timer::tick(double dt_ms, bool enable) noexcept {
    if (enable) {
        running = true;
        accumulated_ms = std::min(accumulated_ms + dt_ms, preset_ms);
        done = accumulated_ms >= preset_ms;
    } else {
        running = false;
        accumulated_ms = 0.0;
        done = false;
    }
}

STContext::STContext(const ValidatedSt& block) {
    const std::size_t slot_count = block.slots().size();
    values_.assign(slot_count, Cell{false});
    assigned_.assign(slot_count, 0);
    assigned_order_.reserve(slot_count);
    timers_.reserve(block.timers().size());
    for (const TimerDef& def : block.timers()) {
        timers_.push_back(Timer{def.preset_ms});
    }
}

void STContext::begin_scan() noexcept {
    for (const std::uint32_t slot : assigned_order_) {
        assigned_[slot] = 0;
    }
    assigned_order_.clear();
}

void STContext::set_slot(std::uint32_t slot, Cell value) noexcept {
    values_[slot] = value;
}

void STContext::assign_slot(std::uint32_t slot, Cell value) noexcept {
    values_[slot] = value;
    if (assigned_[slot] == 0) {
        assigned_[slot] = 1;
        assigned_order_.push_back(slot);
    }
}

const Cell& STContext::slot_value(std::uint32_t slot) const noexcept {
    return values_[slot];
}

std::span<const std::uint32_t> STContext::assigned_slots() const noexcept {
    return assigned_order_;
}

Timer& STContext::timer_at(std::uint32_t timer_slot) noexcept {
    return timers_[timer_slot];
}

namespace {

struct Numeric {
    bool is_double;
    std::int64_t as_int;
    double as_double;
};

Numeric to_numeric(const Cell& cell) noexcept {
    if (const bool* b = std::get_if<bool>(&cell)) {
        return Numeric{false, *b ? 1 : 0, *b ? 1.0 : 0.0};
    }
    if (const std::int64_t* i = std::get_if<std::int64_t>(&cell)) {
        return Numeric{false, *i, static_cast<double>(*i)};
    }
    return Numeric{true, 0, std::get<double>(cell)};
}

bool compare(const Cell& lhs, const Cell& rhs, BinKind kind) noexcept {
    if (kind == BinKind::Eq) {
        return cells_equal(lhs, rhs);
    }
    if (kind == BinKind::Ne) {
        return !cells_equal(lhs, rhs);
    }
    const Numeric l = to_numeric(lhs);
    const Numeric r = to_numeric(rhs);
    if (!l.is_double && !r.is_double) {
        switch (kind) {
            case BinKind::Lt: return l.as_int < r.as_int;
            case BinKind::Le: return l.as_int <= r.as_int;
            case BinKind::Gt: return l.as_int > r.as_int;
            default: return l.as_int >= r.as_int;
        }
    }
    const double ld = l.is_double ? l.as_double : static_cast<double>(l.as_int);
    const double rd = r.is_double ? r.as_double : static_cast<double>(r.as_int);
    switch (kind) {
        case BinKind::Lt: return ld < rd;
        case BinKind::Le: return ld <= rd;
        case BinKind::Gt: return ld > rd;
        default: return ld >= rd;
    }
}

std::expected<Cell, EvalError> arithmetic(const Cell& lhs, const Cell& rhs,
                                          BinKind kind) noexcept {
    const Numeric l = to_numeric(lhs);
    const Numeric r = to_numeric(rhs);
    if (kind == BinKind::Div) {
        const double divisor = r.is_double ? r.as_double : static_cast<double>(r.as_int);
        if (divisor == 0.0) {
            return std::unexpected(EvalError{EvalErrorKind::DivisionByZero});
        }
        const double dividend = l.is_double ? l.as_double : static_cast<double>(l.as_int);
        return Cell{dividend / divisor};
    }
    if (!l.is_double && !r.is_double) {
        switch (kind) {
            case BinKind::Add: return Cell{l.as_int + r.as_int};
            case BinKind::Sub: return Cell{l.as_int - r.as_int};
            default: return Cell{l.as_int * r.as_int};
        }
    }
    const double ld = l.is_double ? l.as_double : static_cast<double>(l.as_int);
    const double rd = r.is_double ? r.as_double : static_cast<double>(r.as_int);
    switch (kind) {
        case BinKind::Add: return Cell{ld + rd};
        case BinKind::Sub: return Cell{ld - rd};
        default: return Cell{ld * rd};
    }
}

class Evaluator {
 public:
    Evaluator(const ValidatedSt& block, STContext& ctx, double dt_ms) noexcept
        : block_(block), ctx_(ctx), dt_ms_(dt_ms) {}

    std::expected<void, EvalError> run() noexcept {
        for (const Statement& stmt : block_.program().statements) {
            if (auto executed = execute_statement(stmt); !executed) {
                return executed;
            }
        }
        return {};
    }

 private:
    std::expected<void, EvalError> execute_statement(const Statement& stmt) noexcept {
        if (const Assignment* assignment = std::get_if<Assignment>(&stmt)) {
            auto value = eval_expr(assignment->value);
            if (!value) {
                return std::unexpected(value.error());
            }
            ctx_.assign_slot(assignment->slot, *value);
            return {};
        }
        if (const TimerCall* call = std::get_if<TimerCall>(&stmt)) {
            auto enable = eval_expr(call->enable);
            if (!enable) {
                return std::unexpected(enable.error());
            }
            ctx_.timer_at(call->timer_slot).tick(dt_ms_, is_truthy(*enable));
            return {};
        }
        const IfBlock& block = std::get<IfBlock>(stmt);
        auto condition = eval_expr(block.condition);
        if (!condition) {
            return std::unexpected(condition.error());
        }
        if (is_truthy(*condition)) {
            for (const Statement& inner : block.body) {
                if (auto executed = execute_statement(inner); !executed) {
                    return executed;
                }
            }
        }
        return {};
    }

    std::expected<Cell, EvalError> eval_expr(ExprId id) noexcept {
        const Expr& expr = block_.program().exprs[id];
        if (const Literal* literal = std::get_if<Literal>(&expr)) {
            return literal->value;
        }
        if (const VarRef* ref = std::get_if<VarRef>(&expr)) {
            return ctx_.slot_value(ref->slot);
        }
        if (const TimerAttr* attr = std::get_if<TimerAttr>(&expr)) {
            const Timer& timer = ctx_.timer_at(attr->timer_slot);
            switch (attr->field) {
                case TimerField::PresetMs: return Cell{timer.preset_ms};
                case TimerField::AccumulatedMs: return Cell{timer.accumulated_ms};
                case TimerField::Running: return Cell{timer.running};
                case TimerField::Done: return Cell{timer.done};
                case TimerField::Q: return Cell{timer.done};
            }
            return Cell{false};
        }
        if (const UnaryOp* unary = std::get_if<UnaryOp>(&expr)) {
            auto operand = eval_expr(unary->operand);
            if (!operand) {
                return operand;
            }
            if (unary->kind == UnaryKind::Not) {
                return Cell{!is_truthy(*operand)};
            }
            const Numeric n = to_numeric(*operand);
            if (unary->kind == UnaryKind::Neg) {
                return n.is_double ? Cell{-n.as_double} : Cell{-n.as_int};
            }
            return n.is_double ? Cell{n.as_double} : Cell{n.as_int};
        }
        const BinOp& bin = std::get<BinOp>(expr);
        auto lhs = eval_expr(bin.lhs);
        if (!lhs) {
            return lhs;
        }
        auto rhs = eval_expr(bin.rhs);
        if (!rhs) {
            return rhs;
        }
        switch (bin.kind) {
            case BinKind::And:
                return Cell{is_truthy(*lhs) && is_truthy(*rhs)};
            case BinKind::Or:
                return Cell{is_truthy(*lhs) || is_truthy(*rhs)};
            case BinKind::Eq:
            case BinKind::Ne:
            case BinKind::Lt:
            case BinKind::Le:
            case BinKind::Gt:
            case BinKind::Ge:
                return Cell{compare(*lhs, *rhs, bin.kind)};
            default:
                return arithmetic(*lhs, *rhs, bin.kind);
        }
    }

    const ValidatedSt& block_;
    STContext& ctx_;
    double dt_ms_;
};

}  // namespace

std::expected<void, EvalError> evaluate(const ValidatedSt& block, STContext& ctx,
                                        double dt_ms) noexcept {
    Evaluator evaluator(block, ctx, dt_ms);
    return evaluator.run();
}

}  // namespace relay_host
