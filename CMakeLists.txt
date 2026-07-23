cmake_minimum_required(VERSION 3.10)

project(forge_framework LANGUAGES CXX)

include(CTest)

# There is currently no top-level C++ verification core to build — each
# plugin owns its own C++ verify sources and CMake target under
# plugins/<plugin>/forge/verify/ (see e.g. plugins/trigger_demo/forge/verify/
# CMakeLists.txt). This option/subdirectory is a hook for a future shared
# top-level target; it's a no-op today because verify/ has no CMakeLists.txt
# of its own.
option(FRAMEWORK_BUILD_VERIFY "Build the framework verification core" OFF)

if(FRAMEWORK_BUILD_VERIFY AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/verify/CMakeLists.txt")
    add_subdirectory(verify)
endif()
