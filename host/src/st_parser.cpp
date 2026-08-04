#include "relay_host/st_parser.hpp"

#include <cctype>
#include <charconv>
#include <optional>
#include <vector>

namespace relay_host {

namespace {

enum class TokKind {
    Ident,
    Keyword,
    Int,
    Float,
    TimeMs,
    Assign,
    Op,
    LParen,
    RParen,
    Comma,
    Semicolon,
    End,
};

struct Token {
    TokKind kind;
    std::string text;
    double number = 0.0;
    std::size_t line = 0;
};

std::string to_upper(std::string_view text) {
    std::string upper;
    upper.reserve(text.size());
    for (const char c : text) {
        upper.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(c))));
    }
    return upper;
}

bool is_keyword(const std::string& upper) {
    return upper == "IF" || upper == "THEN" || upper == "END_IF" || upper == "AND" ||
           upper == "OR" || upper == "NOT" || upper == "TRUE" || upper == "FALSE" ||
           upper == "IN" || upper == "PT";
}

class Tokenizer {
 public:
    explicit Tokenizer(std::string_view source) : source_(source) {}

    std::expected<std::vector<Token>, ParseError> run() {
        std::vector<Token> tokens;
        while (pos_ < source_.size()) {
            const char c = source_[pos_];
            if (c == '\n') {
                ++line_;
                ++pos_;
                continue;
            }
            if (std::isspace(static_cast<unsigned char>(c))) {
                ++pos_;
                continue;
            }
            if (c == '(' && pos_ + 1 < source_.size() && source_[pos_ + 1] == '*') {
                if (auto skipped = skip_comment(); !skipped) {
                    return std::unexpected(skipped.error());
                }
                continue;
            }
            if ((c == 'T' || c == 't') && pos_ + 1 < source_.size() && source_[pos_ + 1] == '#') {
                auto tok = read_time_literal();
                if (!tok) {
                    return std::unexpected(tok.error());
                }
                tokens.push_back(*tok);
                continue;
            }
            if (std::isalpha(static_cast<unsigned char>(c)) || c == '_') {
                tokens.push_back(read_ident());
                continue;
            }
            if (std::isdigit(static_cast<unsigned char>(c))) {
                tokens.push_back(read_number());
                continue;
            }
            auto tok = read_operator();
            if (!tok) {
                return std::unexpected(tok.error());
            }
            tokens.push_back(*tok);
        }
        tokens.push_back(Token{TokKind::End, "", 0.0, line_});
        return tokens;
    }

 private:
    std::expected<void, ParseError> skip_comment() {
        const std::size_t start_line = line_;
        pos_ += 2;
        while (pos_ + 1 < source_.size()) {
            if (source_[pos_] == '*' && source_[pos_ + 1] == ')') {
                pos_ += 2;
                return {};
            }
            if (source_[pos_] == '\n') {
                ++line_;
            }
            ++pos_;
        }
        return std::unexpected(
            ParseError{"st_parser: comment opened with '(*' is never closed with '*)'",
                       start_line});
    }

    Token read_ident() {
        const std::size_t start = pos_;
        while (pos_ < source_.size() &&
               (std::isalnum(static_cast<unsigned char>(source_[pos_])) ||
                source_[pos_] == '_')) {
            ++pos_;
        }
        if (pos_ < source_.size() && source_[pos_] == '.' && pos_ + 1 < source_.size() &&
            (std::isalpha(static_cast<unsigned char>(source_[pos_ + 1])) ||
             source_[pos_ + 1] == '_')) {
            ++pos_;
            while (pos_ < source_.size() &&
                   (std::isalnum(static_cast<unsigned char>(source_[pos_])) ||
                    source_[pos_] == '_')) {
                ++pos_;
            }
        }
        std::string text(source_.substr(start, pos_ - start));
        const std::string upper = to_upper(text);
        if (text.find('.') == std::string::npos && is_keyword(upper)) {
            return Token{TokKind::Keyword, upper, 0.0, line_};
        }
        return Token{TokKind::Ident, std::move(text), 0.0, line_};
    }

    Token read_number() {
        const std::size_t start = pos_;
        bool has_dot = false;
        while (pos_ < source_.size()) {
            const char c = source_[pos_];
            if (std::isdigit(static_cast<unsigned char>(c))) {
                ++pos_;
                continue;
            }
            if (c == '.' && !has_dot && pos_ + 1 < source_.size() &&
                std::isdigit(static_cast<unsigned char>(source_[pos_ + 1]))) {
                has_dot = true;
                ++pos_;
                continue;
            }
            break;
        }
        const std::string text(source_.substr(start, pos_ - start));
        double value = 0.0;
        std::from_chars(text.data(), text.data() + text.size(), value);
        return Token{has_dot ? TokKind::Float : TokKind::Int, text, value, line_};
    }

    std::expected<Token, ParseError> read_time_literal() {
        const std::size_t start_line = line_;
        pos_ += 2;
        const std::size_t start = pos_;
        bool has_dot = false;
        while (pos_ < source_.size()) {
            const char c = source_[pos_];
            if (std::isdigit(static_cast<unsigned char>(c))) {
                ++pos_;
                continue;
            }
            if (c == '.' && !has_dot) {
                has_dot = true;
                ++pos_;
                continue;
            }
            break;
        }
        if (pos_ == start) {
            return std::unexpected(ParseError{
                "st_parser: time literal 'T#' must be followed by digits", start_line});
        }
        const std::string digits(source_.substr(start, pos_ - start));
        if (pos_ + 1 >= source_.size() + 0 ||
            std::toupper(static_cast<unsigned char>(source_[pos_])) != 'M' ||
            pos_ + 1 >= source_.size() ||
            std::toupper(static_cast<unsigned char>(source_[pos_ + 1])) != 'S') {
            return std::unexpected(ParseError{
                "st_parser: time literal 'T#" + digits + "' must end in 'ms'", start_line});
        }
        pos_ += 2;
        double value = 0.0;
        std::from_chars(digits.data(), digits.data() + digits.size(), value);
        return Token{TokKind::TimeMs, digits, value, start_line};
    }

    std::expected<Token, ParseError> read_operator() {
        const char c = source_[pos_];
        const auto two = [&](char a, char b) {
            return c == a && pos_ + 1 < source_.size() && source_[pos_ + 1] == b;
        };
        if (two(':', '=')) {
            pos_ += 2;
            return Token{TokKind::Assign, ":=", 0.0, line_};
        }
        if (two('<', '=')) {
            pos_ += 2;
            return Token{TokKind::Op, "<=", 0.0, line_};
        }
        if (two('>', '=')) {
            pos_ += 2;
            return Token{TokKind::Op, ">=", 0.0, line_};
        }
        if (two('<', '>')) {
            pos_ += 2;
            return Token{TokKind::Op, "<>", 0.0, line_};
        }
        switch (c) {
            case '=':
            case '<':
            case '>':
            case '+':
            case '-':
            case '*':
            case '/':
                ++pos_;
                return Token{TokKind::Op, std::string(1, c), 0.0, line_};
            case '(':
                ++pos_;
                return Token{TokKind::LParen, "(", 0.0, line_};
            case ')':
                ++pos_;
                return Token{TokKind::RParen, ")", 0.0, line_};
            case ',':
                ++pos_;
                return Token{TokKind::Comma, ",", 0.0, line_};
            case ';':
                ++pos_;
                return Token{TokKind::Semicolon, ";", 0.0, line_};
            default:
                return std::unexpected(ParseError{
                    std::string("st_parser: unexpected character '") + c + "' in source",
                    line_});
        }
    }

    std::string_view source_;
    std::size_t pos_ = 0;
    std::size_t line_ = 1;
};

class Parser {
 public:
    explicit Parser(std::vector<Token> tokens) : tokens_(std::move(tokens)) {}

    std::expected<StProgram, ParseError> run() {
        while (!at(TokKind::End)) {
            auto stmt = parse_statement();
            if (!stmt) {
                return std::unexpected(stmt.error());
            }
            program_.statements.push_back(std::move(*stmt));
        }
        return std::move(program_);
    }

 private:
    const Token& peek() const { return tokens_[pos_]; }

    const Token& advance() { return tokens_[pos_++]; }

    bool at(TokKind kind) const { return peek().kind == kind; }

    bool at_keyword(std::string_view kw) const {
        return peek().kind == TokKind::Keyword && peek().text == kw;
    }

    std::unexpected<ParseError> unexpected_token(const std::string& expected) {
        const Token& tok = peek();
        const std::string got =
            tok.kind == TokKind::End ? "end of source" : "'" + tok.text + "'";
        return std::unexpected(
            ParseError{"st_parser: expected " + expected + ", got " + got, tok.line});
    }

    void consume_optional_semicolon() {
        if (at(TokKind::Semicolon)) {
            advance();
        }
    }

    std::expected<Statement, ParseError> parse_statement() {
        if (at_keyword("IF")) {
            return parse_if();
        }
        if (at(TokKind::Ident)) {
            const Token& next = tokens_[pos_ + 1];
            if (next.kind == TokKind::LParen) {
                return parse_timer_call();
            }
            if (next.kind == TokKind::Assign) {
                return parse_assignment();
            }
            return unexpected_token("':=' or '(' after identifier");
        }
        return unexpected_token("a statement (assignment, IF, or timer call)");
    }

    std::expected<Statement, ParseError> parse_if() {
        advance();
        auto condition = parse_expr();
        if (!condition) {
            return std::unexpected(condition.error());
        }
        if (!at_keyword("THEN")) {
            return unexpected_token("'THEN' after IF condition");
        }
        advance();
        IfBlock block{*condition, {}};
        while (!at_keyword("END_IF")) {
            if (at(TokKind::End)) {
                return std::unexpected(ParseError{
                    "st_parser: IF block is never closed with END_IF", peek().line});
            }
            auto stmt = parse_statement();
            if (!stmt) {
                return std::unexpected(stmt.error());
            }
            block.body.push_back(std::move(*stmt));
        }
        advance();
        consume_optional_semicolon();
        return Statement{std::move(block)};
    }

    std::expected<Statement, ParseError> parse_assignment() {
        const Token target = advance();
        if (target.text.find('.') != std::string::npos) {
            return std::unexpected(ParseError{
                "st_parser: assignment target '" + target.text + "' must not contain '.'",
                target.line});
        }
        advance();
        auto value = parse_expr();
        if (!value) {
            return std::unexpected(value.error());
        }
        consume_optional_semicolon();
        return Statement{Assignment{target.text, *value, kUnresolvedSlot}};
    }

    std::expected<Statement, ParseError> parse_timer_call() {
        const Token name = advance();
        advance();
        if (!at_keyword("IN")) {
            return unexpected_token("'IN' in timer call");
        }
        advance();
        if (!at(TokKind::Assign)) {
            return unexpected_token("':=' after 'IN'");
        }
        advance();
        auto enable = parse_expr();
        if (!enable) {
            return std::unexpected(enable.error());
        }
        if (!at(TokKind::Comma)) {
            return unexpected_token("',' between IN and PT arguments");
        }
        advance();
        if (!at_keyword("PT")) {
            return unexpected_token("'PT' in timer call");
        }
        advance();
        if (!at(TokKind::Assign)) {
            return unexpected_token("':=' after 'PT'");
        }
        advance();
        if (!at(TokKind::TimeMs)) {
            return unexpected_token("a time literal 'T#<n>ms'");
        }
        const Token preset = advance();
        if (!at(TokKind::RParen)) {
            return unexpected_token("')' closing timer call");
        }
        advance();
        consume_optional_semicolon();
        return Statement{TimerCall{name.text, *enable, preset.number, kUnresolvedSlot}};
    }

    std::expected<ExprId, ParseError> parse_expr() { return parse_or(); }

    ExprId push_expr(Expr expr) {
        program_.exprs.push_back(std::move(expr));
        return static_cast<ExprId>(program_.exprs.size() - 1);
    }

    std::expected<ExprId, ParseError> parse_or() {
        auto left = parse_and();
        if (!left) {
            return left;
        }
        while (at_keyword("OR")) {
            advance();
            auto right = parse_and();
            if (!right) {
                return right;
            }
            left = push_expr(BinOp{BinKind::Or, *left, *right});
        }
        return left;
    }

    std::expected<ExprId, ParseError> parse_and() {
        auto left = parse_not();
        if (!left) {
            return left;
        }
        while (at_keyword("AND")) {
            advance();
            auto right = parse_not();
            if (!right) {
                return right;
            }
            left = push_expr(BinOp{BinKind::And, *left, *right});
        }
        return left;
    }

    std::expected<ExprId, ParseError> parse_not() {
        if (at_keyword("NOT")) {
            advance();
            auto operand = parse_not();
            if (!operand) {
                return operand;
            }
            return push_expr(UnaryOp{UnaryKind::Not, *operand});
        }
        return parse_comparison();
    }

    std::expected<ExprId, ParseError> parse_comparison() {
        auto left = parse_additive();
        if (!left) {
            return left;
        }
        if (at(TokKind::Op)) {
            const std::string& op = peek().text;
            std::optional<BinKind> kind;
            if (op == "=") kind = BinKind::Eq;
            else if (op == "<>") kind = BinKind::Ne;
            else if (op == "<") kind = BinKind::Lt;
            else if (op == "<=") kind = BinKind::Le;
            else if (op == ">") kind = BinKind::Gt;
            else if (op == ">=") kind = BinKind::Ge;
            if (kind) {
                advance();
                auto right = parse_additive();
                if (!right) {
                    return right;
                }
                return push_expr(BinOp{*kind, *left, *right});
            }
        }
        return left;
    }

    std::expected<ExprId, ParseError> parse_additive() {
        auto left = parse_multiplicative();
        if (!left) {
            return left;
        }
        while (at(TokKind::Op) && (peek().text == "+" || peek().text == "-")) {
            const BinKind kind = peek().text == "+" ? BinKind::Add : BinKind::Sub;
            advance();
            auto right = parse_multiplicative();
            if (!right) {
                return right;
            }
            left = push_expr(BinOp{kind, *left, *right});
        }
        return left;
    }

    std::expected<ExprId, ParseError> parse_multiplicative() {
        auto left = parse_unary();
        if (!left) {
            return left;
        }
        while (at(TokKind::Op) && (peek().text == "*" || peek().text == "/")) {
            const BinKind kind = peek().text == "*" ? BinKind::Mul : BinKind::Div;
            advance();
            auto right = parse_unary();
            if (!right) {
                return right;
            }
            left = push_expr(BinOp{kind, *left, *right});
        }
        return left;
    }

    std::expected<ExprId, ParseError> parse_unary() {
        if (at(TokKind::Op) && peek().text == "-") {
            advance();
            auto operand = parse_unary();
            if (!operand) {
                return operand;
            }
            return push_expr(UnaryOp{UnaryKind::Neg, *operand});
        }
        if (at(TokKind::Op) && peek().text == "+") {
            advance();
            auto operand = parse_unary();
            if (!operand) {
                return operand;
            }
            return push_expr(UnaryOp{UnaryKind::Plus, *operand});
        }
        return parse_primary();
    }

    std::expected<ExprId, ParseError> parse_primary() {
        if (at(TokKind::LParen)) {
            advance();
            auto inner = parse_expr();
            if (!inner) {
                return inner;
            }
            if (!at(TokKind::RParen)) {
                return unexpected_token("')'");
            }
            advance();
            return inner;
        }
        if (at(TokKind::Int)) {
            const Token tok = advance();
            return push_expr(Literal{Cell{static_cast<std::int64_t>(tok.number)}});
        }
        if (at(TokKind::Float)) {
            const Token tok = advance();
            return push_expr(Literal{Cell{tok.number}});
        }
        if (at_keyword("TRUE")) {
            advance();
            return push_expr(Literal{Cell{true}});
        }
        if (at_keyword("FALSE")) {
            advance();
            return push_expr(Literal{Cell{false}});
        }
        if (at(TokKind::Ident)) {
            const Token tok = advance();
            const std::size_t dot = tok.text.find('.');
            if (dot != std::string::npos) {
                return push_expr(
                    TimerAttr{tok.text.substr(0, dot), tok.text.substr(dot + 1)});
            }
            return push_expr(VarRef{tok.text});
        }
        return unexpected_token("an expression");
    }

    std::vector<Token> tokens_;
    std::size_t pos_ = 0;
    StProgram program_;
};

}  // namespace

std::expected<StProgram, ParseError> parse_st(std::string_view source) {
    Tokenizer tokenizer(source);
    auto tokens = tokenizer.run();
    if (!tokens) {
        return std::unexpected(tokens.error());
    }
    Parser parser(std::move(*tokens));
    return parser.run();
}

}  // namespace relay_host
