#include "logging.h"
#include <iostream>

namespace verif_fw {

// TraceLogger and DetailedLogger are now templated in the header

ErrorLogger::ErrorLogger() : enabled_(false) {}

bool ErrorLogger::open(const std::string& path) {
    error_file_.open(path);
    if (!error_file_) {
        return false;
    }
    enabled_ = true;
    error_file_ << "cycle,error\n";
    return true;
}

void ErrorLogger::log_error(int cycle, const std::string& message) {
    if (!enabled_) return;
    error_file_ << cycle << ",\"" << message << "\"\n";
}

void ErrorLogger::close() {
    if (enabled_) {
        error_file_.close();
        enabled_ = false;
    }
}

RunLogger::RunLogger() : enabled_(false) {}

bool RunLogger::open(const std::string& path) {
    run_file_.open(path);
    if (!run_file_) {
        return false;
    }
    enabled_ = true;
    return true;
}

void RunLogger::log_run_info(const std::string& module, const std::string& backend,
                               int latency, const std::string& git_sha) {
    if (!enabled_) return;
    run_file_ << "{\n"
              << "  \"module\": \"" << module << "\",\n"
              << "  \"backend\": \"" << backend << "\",\n"
              << "  \"latency\": " << latency << ",\n"
              << "  \"git_sha\": \"" << git_sha << "\"\n"
              << "}\n";
}

void RunLogger::close() {
    if (enabled_) {
        run_file_.close();
        enabled_ = false;
    }
}

} // namespace verif_fw
