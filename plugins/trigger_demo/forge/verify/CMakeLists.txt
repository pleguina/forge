cmake_minimum_required(VERSION 3.16)
project(trigger_demo_verify CXX)

# ── Vitis HLS headers (required for ap_uint<> in algo sources) ──────────
# Set XILINX_HLS env var or let CMake search standard install paths.
if(DEFINED ENV{XILINX_HLS})
    set(XILINX_HLS_INCLUDE "$ENV{XILINX_HLS}/include")
else()
    find_path(XILINX_HLS_INCLUDE ap_int.h
        PATHS
            /tools/Xilinx/Vitis_HLS/2024.1/include
            /tools/Xilinx/Vitis_HLS/2023.2/include
            /tools/Xilinx/Vitis_HLS/2022.2/include
    )
endif()

if(NOT EXISTS "${XILINX_HLS_INCLUDE}/ap_int.h")
    message(STATUS
        "[trigger_demo verify] Vitis HLS headers not found — "
        "set XILINX_HLS or install Vitis HLS.  Skipping csim tests.")
    return()
endif()

message(STATUS "[trigger_demo verify] HLS headers: ${XILINX_HLS_INCLUDE}")

# ── verif_core library (moved from framework verify/) ───────────────────
add_library(verif_core STATIC src/logging.cpp)
target_compile_features(verif_core PUBLIC cxx_std_17)
target_include_directories(verif_core PUBLIC include)

# ── Algo source paths ────────────────────────────────────────────────────
set(ALGO_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../algo)

set(ALGO_INCLUDES
    ${ALGO_ROOT}/common
    ${ALGO_ROOT}/hit_decoder
    ${ALGO_ROOT}/hit_collector
    ${ALGO_ROOT}/trigger_logic
    ${ALGO_ROOT}/trigger_output
    ${XILINX_HLS_INCLUDE}
)

set(ALGO_SOURCES
    ${ALGO_ROOT}/hit_decoder/hit_decoder.cpp
    ${ALGO_ROOT}/hit_collector/hit_collector.cpp
    ${ALGO_ROOT}/trigger_logic/trigger_logic.cpp
    ${ALGO_ROOT}/trigger_output/trigger_output.cpp
)

# ── Golden dataset ───────────────────────────────────────────────────────
set(GOLDEN_XML ${CMAKE_CURRENT_SOURCE_DIR}/schemas/data/trigger_demo_golden.xml)

# ── Helper: register one csim testbench as a CTest ──────────────────────
enable_testing()

function(add_csim_tb target src)
    add_executable(${target}
        tests/${src}
        ${ALGO_SOURCES}
    )
    target_include_directories(${target} PRIVATE
        src                # adapter headers + xml_event_reader.h
        ${ALGO_INCLUDES}
    )
    target_link_libraries(${target} PRIVATE verif_core)
    add_test(
        NAME    trigger_demo.csim.${target}
        COMMAND ${target} ${GOLDEN_XML}
    )
endfunction()

add_csim_tb(tb_hit_decoder    tb_hit_decoder.cpp)
add_csim_tb(tb_hit_collector  tb_hit_collector.cpp)
add_csim_tb(tb_trigger_logic  tb_trigger_logic.cpp)
add_csim_tb(tb_trigger_output tb_trigger_output.cpp)
