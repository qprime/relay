#include "relay_host/json_text.hpp"

#include <array>
#include <charconv>
#include <cstdio>
#include <string>

namespace relay_host {

std::string format_json_double(double value) {
    std::array<char, 40> buf;
    const auto result = std::to_chars(buf.data(), buf.data() + buf.size(), value,
                                      std::chars_format::scientific);
    std::string sci(buf.data(), result.ptr);
    bool negative = false;
    if (!sci.empty() && sci[0] == '-') {
        negative = true;
        sci.erase(0, 1);
    }
    const std::size_t epos = sci.find('e');
    const std::string mantissa = sci.substr(0, epos);
    const int exp10 = std::stoi(sci.substr(epos + 1));
    std::string digits;
    for (const char c : mantissa) {
        if (c != '.') {
            digits.push_back(c);
        }
    }

    std::string out;
    if (exp10 >= -4 && exp10 < 16) {
        const int ndigits = static_cast<int>(digits.size());
        if (exp10 >= ndigits - 1) {
            out = digits + std::string(static_cast<std::size_t>(exp10 - (ndigits - 1)), '0') +
                  ".0";
        } else if (exp10 >= 0) {
            out = digits.substr(0, static_cast<std::size_t>(exp10 + 1)) + "." +
                  digits.substr(static_cast<std::size_t>(exp10 + 1));
        } else {
            out = "0." + std::string(static_cast<std::size_t>(-exp10 - 1), '0') + digits;
        }
    } else {
        out = digits.substr(0, 1);
        if (digits.size() > 1) {
            out += "." + digits.substr(1);
        }
        out += "e";
        out += exp10 < 0 ? "-" : "+";
        std::string exp_text = std::to_string(exp10 < 0 ? -exp10 : exp10);
        if (exp_text.size() < 2) {
            exp_text.insert(0, "0");
        }
        out += exp_text;
    }
    return negative ? "-" + out : out;
}

std::string escape_json_string(std::string_view text) {
    std::string out;
    out.reserve(text.size() + 2);
    out.push_back('"');
    for (const char c : text) {
        const unsigned char uc = static_cast<unsigned char>(c);
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (uc < 0x20 || uc > 0x7e) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", uc);
                    out += buf;
                } else {
                    out.push_back(c);
                }
        }
    }
    out.push_back('"');
    return out;
}

}  // namespace relay_host
