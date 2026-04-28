#include "latency_shaper.h"
#include "logging.h"

#include <cstdio>
#include <stdexcept>
#include <string>

namespace {

struct DummyTxn {
    bool is_valid = false;

    bool valid() const {
        return is_valid;
    }
};

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

} // namespace

int main() {
    verif_fw::LatencyShaper<DummyTxn> shaper(1);
    shaper.push(DummyTxn{true});

    DummyTxn first = shaper.pop();
    require(!first.valid(), "Latency shaper released a transaction too early");

    shaper.push(DummyTxn{false});
    DummyTxn second = shaper.pop();
    require(second.valid(), "Latency shaper did not preserve queued transaction order");

    const char* error_log = "framework_verify_smoke_errors.csv";
    const char* run_log = "framework_verify_smoke_run.json";

    verif_fw::ErrorLogger error_logger;
    require(error_logger.open(error_log), "Failed to open smoke-test error log");
    error_logger.log_error(7, "smoke check");
    error_logger.close();

    verif_fw::RunLogger run_logger;
    require(run_logger.open(run_log), "Failed to open smoke-test run log");
    run_logger.log_run_info("framework_verify_smoke", "standalone", 1, "local");
    run_logger.close();

    std::remove(error_log);
    std::remove(run_log);

    return 0;
}
