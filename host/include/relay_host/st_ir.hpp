#pragma once

#include <cstdint>
#include <limits>
#include <string>
#include <variant>
#include <vector>

#include "relay_host/io_image.hpp"

namespace relay_host {

inline constexpr std::uint32_t kUnresolvedSlot = std::numeric_limits<std::uint32_t>::max();

using ExprId = std::uint32_t;

enum class BinKind {
    And,
    Or,
    Eq,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
    Add,
    Sub,
    Mul,
    Div,
};

enum class UnaryKind {
    Not,
    Neg,
    Plus,
};

enum class TimerField {
    PresetMs,
    AccumulatedMs,
    Running,
    Done,
    Q,
};

struct Literal {
    Cell value;
};

struct VarRef {
    std::string name;
    std::uint32_t slot = kUnresolvedSlot;
};

struct TimerAttr {
    std::string timer;
    std::string attr;
    TimerField field = TimerField::Q;
    std::uint32_t timer_slot = kUnresolvedSlot;
};

struct UnaryOp {
    UnaryKind kind;
    ExprId operand;
};

struct BinOp {
    BinKind kind;
    ExprId lhs;
    ExprId rhs;
};

using Expr = std::variant<Literal, VarRef, TimerAttr, UnaryOp, BinOp>;

struct Statement;

struct Assignment {
    std::string target;
    ExprId value;
    std::uint32_t slot = kUnresolvedSlot;
};

struct TimerCall {
    std::string timer;
    ExprId enable;
    double preset_ms;
    std::uint32_t timer_slot = kUnresolvedSlot;
};

struct IfBlock {
    ExprId condition;
    std::vector<Statement> body;
};

struct Statement : std::variant<Assignment, TimerCall, IfBlock> {
    using variant::variant;
};

struct StProgram {
    std::vector<Expr> exprs;
    std::vector<Statement> statements;
};

}  // namespace relay_host
