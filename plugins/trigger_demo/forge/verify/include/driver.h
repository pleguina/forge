#ifndef DRIVER_H
#define DRIVER_H

#include "transaction_concepts.h"

namespace verif_fw {

// Forward declaration of adapter interface
template<typename StimTxn, typename OutTxn>
class DutAdapterBase;

// Template-based driver
template<typename StimTxn, typename OutTxn>
class Driver {
    DutAdapterBase<StimTxn, OutTxn>* dut_;

public:
    Driver(DutAdapterBase<StimTxn, OutTxn>* dut) : dut_(dut) {}

    // Drive one stimulus transaction and return output
    OutTxn drive(const StimTxn& stim) {
        return dut_->tick(stim);
    }
};

} // namespace verif_fw

#endif // DRIVER_H
