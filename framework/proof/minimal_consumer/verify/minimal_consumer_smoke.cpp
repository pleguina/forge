#include "analysis_port.h"
#include "logging.h"

#include <cstdio>
#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

} // namespace

int main() {
    int observed_value = 0;
    verif_fw::AnalysisPort<int> port;
    port.subscribe([&observed_value](const int& value) {
        observed_value = value;
    });
    port.write(42);
    require(observed_value == 42, "Analysis port did not deliver the published value");

    const char* error_log = "framework_minimal_consumer_errors.csv";
    verif_fw::ErrorLogger error_logger;
    require(error_logger.open(error_log), "Failed to open minimal-consumer error log");
    error_logger.log_error(1, "minimal consumer smoke");
    error_logger.close();

    std::remove(error_log);
    return 0;
}