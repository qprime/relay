cmake_minimum_required(VERSION 3.22)
project(relay_host_project CXX)

include(FetchContent)
FetchContent_Declare(asio
    GIT_REPOSITORY https://github.com/chriskohlhoff/asio.git
    GIT_TAG asio-1-38-2)
FetchContent_Declare(googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG v1.17.0)
FetchContent_MakeAvailable(asio googletest)

find_package(Threads REQUIRED)

add_library(asio_standalone INTERFACE)
target_include_directories(asio_standalone SYSTEM INTERFACE ${asio_SOURCE_DIR}/asio/include)
target_compile_definitions(asio_standalone INTERFACE ASIO_NO_DEPRECATED)
target_link_libraries(asio_standalone INTERFACE Threads::Threads)

set(RELAY_HOST_WARNINGS -Wall -Wextra -Wpedantic -Werror)

add_library(relay_host STATIC
    src/clock.cpp
    src/io_image.cpp
    src/spec_loader.cpp
    src/st_parser.cpp
    src/st_validator.cpp
    src/st_eval.cpp
    src/signal_table.cpp
    src/comm_bus.cpp
    src/comm_strategy.cpp
    src/plant_adapter.cpp
    src/plant_registry.cpp
    src/remote_socket_plant.cpp
    src/scan_executor.cpp
    src/host_harness.cpp
    src/trace.cpp
)
target_compile_features(relay_host PUBLIC cxx_std_23)
target_compile_options(relay_host PRIVATE ${RELAY_HOST_WARNINGS})
target_include_directories(relay_host PUBLIC include)
target_include_directories(relay_host SYSTEM PUBLIC third_party)
target_link_libraries(relay_host PUBLIC asio_standalone)

add_executable(relay_host_main src/host_main.cpp)
target_compile_options(relay_host_main PRIVATE ${RELAY_HOST_WARNINGS})
target_link_libraries(relay_host_main PRIVATE relay_host)

enable_testing()

foreach(_gt_target gtest gtest_main)
    get_target_property(_gt_includes ${_gt_target} INTERFACE_INCLUDE_DIRECTORIES)
    set_target_properties(${_gt_target} PROPERTIES
        INTERFACE_SYSTEM_INCLUDE_DIRECTORIES "${_gt_includes}")
endforeach()

add_executable(relay_host_tests
    tests/test_io_image.cpp
    tests/test_st_parser.cpp
    tests/test_st_eval.cpp
    tests/test_comm_bus.cpp
    tests/test_comm_strategy.cpp
    tests/test_host_harness.cpp
    tests/test_plant_registry.cpp
    tests/test_remote_socket_plant.cpp
    tests/test_scan_executor.cpp
    tests/test_ring_buffer_trace.cpp
)
target_compile_options(relay_host_tests PRIVATE ${RELAY_HOST_WARNINGS})
target_link_libraries(relay_host_tests PRIVATE relay_host gtest gtest_main)
add_test(NAME relay_host_tests COMMAND relay_host_tests)
