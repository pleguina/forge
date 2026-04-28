#ifndef SCOREBOARD_H
#define SCOREBOARD_H

#include "rules.h"
#include <map>
#include <vector>
#include <string>
#include <sstream>

namespace verif_fw {

// Forward declarations for specializations
template<typename OutTxn>
class ScoreboardChecker;

// Template-based scoreboard using cycle-indexed expected outputs (matching original design)
template<typename OutTxn>
class Scoreboard {
    int latency_;
    int drain_latency_;
    ComparisonRules rules_;
    std::map<int, std::vector<OutTxn>> expected_;  // cycle -> expected outputs
    int errors_;
    std::vector<std::string> error_log_;
    ScoreboardChecker<OutTxn> checker_;

public:
    Scoreboard(int latency, int drain_latency, const ComparisonRules& rules)
        : latency_(latency), drain_latency_(drain_latency), rules_(rules), 
          errors_(0), checker_(rules_) {}

    // Enqueue an expected output at a specific cycle
    void enqueue_expected(int cycle, const OutTxn& txn) {
        expected_[cycle].push_back(txn);
    }

    // Get size of expected outputs (total across all cycles)
    size_t expected_size() const {
        size_t total = 0;
        for (const auto& kv : expected_) {
            total += kv.second.size();
        }
        return total;
    }

    // Check an observed output against expected
    void check(const OutTxn& actual) {
        checker_.check(actual, expected_, errors_, error_log_);
    }

    int get_errors() const {
        return errors_;
    }

    const std::vector<std::string>& get_error_log() const {
        return error_log_;
    }

    void finalize() {
        checker_.finalize(expected_, errors_, error_log_);
    }
};

} // namespace verif_fw

#endif // SCOREBOARD_H
