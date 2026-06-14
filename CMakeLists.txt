cmake_minimum_required(VERSION 3.10)

project(arc_framework LANGUAGES CXX)

include(CTest)

option(FRAMEWORK_BUILD_VERIFY "Build the framework verification core" ON)

if(FRAMEWORK_BUILD_VERIFY)
    add_subdirectory(verify)
endif()
