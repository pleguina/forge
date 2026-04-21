#ifndef DUT_ADAPTER_H
#define DUT_ADAPTER_H

#include "transaction_concepts.h"

namespace verif_fw {

// Abstract base adapter - template-based
template<typename StimTxn, typename OutTxn>
class DutAdapterBase {
public:
    virtual ~DutAdapterBase() = default;

    // Core interface
    virtual void configure() = 0;
    virtual void reset() = 0;
    virtual OutTxn tick(const StimTxn& stim) = 0;

    // Latency information
    virtual int latency() const = 0;
    virtual int drain_latency() const = 0;
};

} // namespace verif_fw

#endif // DUT_ADAPTER_H
