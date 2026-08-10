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

add_library(nlohmann_header INTERFACE)
target_include_directories(nlohmann_header SYSTEM INTERFACE third_party)

set(RELAY_HOST_WARNINGS -Wall -Wextra -Wpedantic -Werror)

# The value vocabulary the runtime and the verifier share, depending on nothing.
# Extracting it is what lets relay_verify's purity be a property of the link
# graph rather than a review convention.
add_library(relay_core STATIC
    src/io_image.cpp
    src/json_text.cpp
)
target_compile_features(relay_core PUBLIC cxx_std_23)
target_compile_options(relay_core PRIVATE ${RELAY_HOST_WARNINGS})
target_include_directories(relay_core PUBLIC include)

# relay_verify links relay_core and nothing else: no asio, no nlohmann, no
# relay_host. See docs/invariants/host_verification_path_purity.md — a
# convenience include here turns a mechanical guarantee back into a convention.
add_library(relay_verify STATIC
    src/verify/assertion_parser.cpp
    src/verify/verifier.cpp
)
target_compile_options(relay_verify PRIVATE ${RELAY_HOST_WARNINGS})
target_link_libraries(relay_verify PUBLIC relay_core)

# Both wire-format directions sit outside the purity boundary, exactly as
# relay/trace_io.py and relay/verdict_io.py sit outside Python's.
add_library(relay_verify_io STATIC
    src/verify/trace_reader.cpp
    src/verify/verdict_writer.cpp
)
target_compile_options(relay_verify_io PRIVATE ${RELAY_HOST_WARNINGS})
target_link_libraries(relay_verify_io PUBLIC relay_core nlohmann_header)

add_library(relay_host STATIC
    src/clock.cpp
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
target_link_libraries(relay_host PUBLIC relay_core nlohmann_header asio_standalone)

add_executable(relay_host_main src/host_main.cpp)
target_compile_options(relay_host_main PRIVATE ${RELAY_HOST_WARNINGS})
target_link_libraries(relay_host_main PRIVATE relay_host)

add_executable(relay_host_verify src/verify/verify_main.cpp)
target_compile_options(relay_host_verify PRIVATE ${RELAY_HOST_WARNINGS})
target_link_libraries(relay_host_verify PRIVATE relay_verify relay_verify_io)

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
    tests/test_trace_reader.cpp
    tests/test_assertion_parser.cpp
    tests/test_verifier.cpp
)
target_compile_options(relay_host_tests PRIVATE ${RELAY_HOST_WARNINGS})
target_compile_definitions(relay_host_tests PRIVATE
    RELAY_REPO_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/..")
target_link_libraries(relay_host_tests PRIVATE relay_host relay_verify relay_verify_io
    gtest gtest_main)
add_test(NAME relay_host_tests COMMAND relay_host_tests)
