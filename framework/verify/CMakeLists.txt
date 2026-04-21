include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

set(FRAMEWORK_VERIF_PUBLIC_HEADERS
    include/analysis_port.h
    include/driver.h
    include/dut_adapter.h
    include/hierarchical_probe.h
    include/latency_shaper.h
    include/logging.h
    include/monitor.h
    include/rules.h
    include/scoreboard.h
    include/sequencer.h
    include/transaction_concepts.h
    include/xsi_loader.h
)

set(FRAMEWORK_VERIF_SOURCES
    src/logging.cpp
)

add_library(verif_core STATIC ${FRAMEWORK_VERIF_SOURCES})
add_library(framework::verif_core ALIAS verif_core)

target_compile_features(verif_core PUBLIC cxx_std_17)

target_include_directories(verif_core
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/framework/verify>
)

set_target_properties(verif_core PROPERTIES
    EXPORT_NAME verif_core
    PUBLIC_HEADER "${FRAMEWORK_VERIF_PUBLIC_HEADERS}"
)

install(TARGETS verif_core
    EXPORT framework_verify_targets
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
    PUBLIC_HEADER DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/framework/verify
)

install(EXPORT framework_verify_targets
    FILE frameworkVerifyTargets.cmake
    NAMESPACE framework::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/framework-verify
)

export(EXPORT framework_verify_targets
    FILE ${CMAKE_CURRENT_BINARY_DIR}/frameworkVerifyTargets.cmake
    NAMESPACE framework::
)

configure_package_config_file(
    ${CMAKE_CURRENT_SOURCE_DIR}/cmake/frameworkVerifyConfig.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/frameworkVerifyConfig.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/framework-verify
)

write_basic_package_version_file(
    ${CMAKE_CURRENT_BINARY_DIR}/frameworkVerifyConfigVersion.cmake
    VERSION 1.0.0
    COMPATIBILITY SameMajorVersion
)

install(FILES
    ${CMAKE_CURRENT_BINARY_DIR}/frameworkVerifyConfig.cmake
    ${CMAKE_CURRENT_BINARY_DIR}/frameworkVerifyConfigVersion.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/framework-verify
)

if(BUILD_TESTING)
    add_executable(framework_verify_smoke tests/framework_verify_smoke.cpp)
    target_link_libraries(framework_verify_smoke PRIVATE framework::verif_core)
    add_test(NAME framework.verify.smoke COMMAND framework_verify_smoke)
endif()
