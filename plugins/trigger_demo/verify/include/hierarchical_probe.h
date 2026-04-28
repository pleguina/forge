#ifndef HIERARCHICAL_PROBE_H
#define HIERARCHICAL_PROBE_H

#include <string>
#include <fstream>
#include <vector>
#include <iomanip>
#include <iostream>

namespace verif_fw {

// ============================================================================
// Hierarchical Probe - Captures Internal Module Outputs
// Allows "spying" on intermediate stages without modifying adapter logic
// Retained as a dormant helper for direct-C++ RTL backends; not used by the
// primary XSIM file-based flow.
// ============================================================================

template<typename DUT_TYPE>
class HierarchicalProbe {
public:
    HierarchicalProbe(const std::string& probe_name, const std::string& output_dir = "probe_logs")
        : name_(probe_name), output_dir_(output_dir), cycle_(0), enabled_(true) {}
    
    virtual ~HierarchicalProbe() {
        if (log_file_.is_open()) {
            log_file_.close();
        }
    }
    
    // Open log file for this probe
    void open_log() {
        if (!enabled_) return;
        
        std::string filename = output_dir_ + "/" + name_ + ".log";
        log_file_.open(filename);
        if (!log_file_.is_open()) {
            std::cerr << "[Probe] ERROR: Cannot open " << filename << std::endl;
            enabled_ = false;
            return;
        }
        
        log_file_ << "# Hierarchical Probe: " << name_ << std::endl;
        write_header();
    }
    
    // Sample data from DUT at current cycle
    virtual void sample(DUT_TYPE* dut) = 0;
    
    // Increment cycle counter
    void tick() { cycle_++; }
    
    // Get current cycle
    int get_cycle() const { return cycle_; }
    
    // Enable/disable probe
    void set_enabled(bool en) { enabled_ = en; }
    bool is_enabled() const { return enabled_; }
    
protected:
    std::string name_;
    std::string output_dir_;
    std::ofstream log_file_;
    int cycle_;
    bool enabled_;
    
    // Write header (override in derived class)
    virtual void write_header() {
        log_file_ << "# Cycle, Data" << std::endl;
    }
};

// ============================================================================
// Probe Manager - Manages Multiple Probes
// ============================================================================

template<typename DUT_TYPE>
class ProbeManager {
public:
    ProbeManager(const std::string& output_dir = "probe_logs")
        : output_dir_(output_dir) {}
    
    ~ProbeManager() {
        for (auto* probe : probes_) {
            delete probe;
        }
    }
    
    // Add probe (takes ownership)
    void add_probe(HierarchicalProbe<DUT_TYPE>* probe) {
        probes_.push_back(probe);
        probe->open_log();
    }
    
    // Sample all probes
    void sample_all(DUT_TYPE* dut) {
        for (auto* probe : probes_) {
            probe->sample(dut);
        }
    }
    
    // Tick all probes
    void tick_all() {
        for (auto* probe : probes_) {
            probe->tick();
        }
    }
    
private:
    std::string output_dir_;
    std::vector<HierarchicalProbe<DUT_TYPE>*> probes_;
};

} // namespace verif_fw

#endif // HIERARCHICAL_PROBE_H
