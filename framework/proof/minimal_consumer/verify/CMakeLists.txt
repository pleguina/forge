cmake_minimum_required(VERSION 3.10)

project(framework_minimal_consumer LANGUAGES CXX)

include(CTest)

find_package(frameworkVerify CONFIG REQUIRED)

add_executable(framework_minimal_consumer_smoke minimal_consumer_smoke.cpp)
target_link_libraries(framework_minimal_consumer_smoke PRIVATE framework::verif_core)
target_compile_features(framework_minimal_consumer_smoke PRIVATE cxx_std_17)

if(BUILD_TESTING)
    add_test(NAME framework.minimal_consumer.smoke COMMAND framework_minimal_consumer_smoke)
endif()