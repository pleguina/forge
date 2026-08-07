cmake_minimum_required(VERSION 3.16)
project(vision_pipeline_demo_verify CXX)

# ── Vitis HLS headers (required for ap_uint<> in algo sources) ──────────
if(DEFINED ENV{XILINX_HLS})
    set(XILINX_HLS_INCLUDE "$ENV{XILINX_HLS}/include")
else()
    find_path(XILINX_HLS_INCLUDE ap_int.h
        PATHS
            /tools/Xilinx/Vitis_HLS/2024.1/include
            /tools/Xilinx/Vitis_HLS/2023.2/include
            /tools/Xilinx/Vitis_HLS/2022.2/include
            /opt/Xlx/Vitis_HLS/2024.1/include
    )
endif()

if(NOT EXISTS "${XILINX_HLS_INCLUDE}/ap_int.h")
    message(STATUS
        "[vision_pipeline_demo verify] Vitis HLS headers not found — "
        "set XILINX_HLS or install Vitis HLS.  Skipping csim tests.")
    return()
endif()

message(STATUS "[vision_pipeline_demo verify] HLS headers: ${XILINX_HLS_INCLUDE}")

# ── verif_core library (shared with the other reference plugins' own
#    copies — not linked across plugins, each has its own) ─────────────
add_library(verif_core STATIC src/logging.cpp)
target_compile_features(verif_core PUBLIC cxx_std_17)
target_include_directories(verif_core PUBLIC include)

# ── Algo source paths ────────────────────────────────────────────────────
set(ALGO_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../algo)

set(ALGO_INCLUDES
    ${ALGO_ROOT}/normalizer
    ${XILINX_HLS_INCLUDE}
)

set(ALGO_SOURCES
    ${ALGO_ROOT}/normalizer/pixel_normalizer.cpp
)

# ── Golden dataset ───────────────────────────────────────────────────────
set(GOLDEN_XML ${CMAKE_CURRENT_SOURCE_DIR}/schemas/data/vision_pipeline_quickstart_golden.xml)

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
        NAME    vision_pipeline_demo.csim.${target}
        COMMAND ${target} ${GOLDEN_XML}
    )
endfunction()

add_csim_tb(tb_pixel_normalizer tb_pixel_normalizer.cpp)
