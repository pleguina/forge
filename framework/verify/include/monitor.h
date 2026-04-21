#ifndef MONITOR_H
#define MONITOR_H

#include "transaction_concepts.h"
#include "analysis_port.h"

namespace verif_fw {

// Template-based monitor
template<typename OutTxn>
class Monitor {
    AnalysisPort<OutTxn>* ap_;

public:
    Monitor(AnalysisPort<OutTxn>* ap) : ap_(ap) {}

    // Observe an output transaction and broadcast to subscribers
    void observe(const OutTxn& txn) {
        if (ap_) {
            ap_->write(txn);
        }
    }
};

} // namespace verif_fw

#endif // MONITOR_H
