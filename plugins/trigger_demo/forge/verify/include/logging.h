#ifndef LOGGING_H
#define LOGGING_H

#include <string>
#include <fstream>
#include <iostream>

namespace verif_fw {

// Template class for trace logging
template<typename StimTxn, typename OutTxn>
class TraceLogger {
    std::ofstream trace_file_;
    bool enabled_;

public:
    TraceLogger() : enabled_(false) {}
    
    bool open(const std::string& path) {
        trace_file_.open(path);
        enabled_ = trace_file_.is_open();
        if (enabled_) {
            trace_file_ << "cycle,stim_valid,out_valid,match\n";
        }
        return enabled_;
    }
    
    void log_cycle(int cycle, const StimTxn& stim, const OutTxn& out, bool match) {
        if (!enabled_) return;
        trace_file_ << cycle << "," << stim.valid() << "," << out.valid() << "," << match << "\n";
    }
    
    void close() {
        if (enabled_) {
            trace_file_.close();
            enabled_ = false;
        }
    }
};

class ErrorLogger {
    std::ofstream error_file_;
    bool enabled_;

public:
    ErrorLogger();
    bool open(const std::string& path);
    void log_error(int cycle, const std::string& message);
    void close();
};

class RunLogger {
    std::ofstream run_file_;
    bool enabled_;

public:
    RunLogger();
    bool open(const std::string& path);
    void log_run_info(const std::string& module, const std::string& backend,
                      int latency, const std::string& git_sha);
    void close();
};

// Template class for detailed logging
template<typename StimTxn, typename OutTxn>
class DetailedLogger {
    std::ofstream detailed_file_;
    std::streambuf* original_cout_buf_;
    bool enabled_;
    bool cout_redirected_;

public:
    DetailedLogger() : original_cout_buf_(nullptr), enabled_(false), cout_redirected_(false) {}
    
    bool open(const std::string& path) {
        detailed_file_.open(path);
        enabled_ = detailed_file_.is_open();
        return enabled_;
    }
    
    void start_redirect() {
        if (enabled_ && !cout_redirected_) {
            original_cout_buf_ = std::cout.rdbuf();
            std::cout.rdbuf(detailed_file_.rdbuf());
            cout_redirected_ = true;
        }
    }
    
    void stop_redirect() {
        if (enabled_ && cout_redirected_ && original_cout_buf_) {
            std::cout.rdbuf(original_cout_buf_);
            cout_redirected_ = false;
        }
    }

    // Framework logging (writes directly to file, bypassing cout redirect)
    void log_cycle_header(int cycle, const StimTxn& stim) {
        if (!enabled_) return;
        detailed_file_ << "\n=== Cycle " << cycle << " ===\n";
    }
    
    void log_cycle_output(int cycle, const OutTxn& out) {
        if (!enabled_) return;
        detailed_file_ << "Output: cycle=" << cycle << "\n";
    }
    
    void log_separator() {
        if (!enabled_) return;
        detailed_file_ << "================\n";
    }

    void close() {
        stop_redirect();
        if (enabled_) {
            detailed_file_.close();
            enabled_ = false;
        }
    }
    
    ~DetailedLogger() {
        close();
    }
};

} // namespace verif_fw

#endif // LOGGING_H
