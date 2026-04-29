#ifndef ANALYSIS_PORT_H
#define ANALYSIS_PORT_H

#include <vector>
#include <functional>

namespace verif_fw {

// Template-based analysis port - publish-subscribe pattern
template<typename TxnType>
class AnalysisPort {
    std::vector<std::function<void(const TxnType&)>> subscribers_;

public:
    AnalysisPort() = default;

    // Subscribe a callback
    void subscribe(std::function<void(const TxnType&)> callback) {
        subscribers_.push_back(callback);
    }

    // Publish transaction to all subscribers
    void write(const TxnType& txn) {
        for (auto& callback : subscribers_) {
            callback(txn);
        }
    }
};

} // namespace verif_fw

#endif // ANALYSIS_PORT_H
