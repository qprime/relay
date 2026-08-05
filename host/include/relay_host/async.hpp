#pragma once

#include <asio.hpp>
#include <asio/experimental/channel.hpp>

namespace relay_host {

using Executor = asio::any_io_executor;

template <typename T>
using Channel = asio::experimental::channel<void(asio::error_code, T)>;

using Task = asio::awaitable<void>;

}  // namespace relay_host
