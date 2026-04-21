#ifndef TRANSACTION_CONCEPTS_H
#define TRANSACTION_CONCEPTS_H

namespace verif_fw {

// Base stimulus transaction interface
// All stimulus transactions must have these fields
struct BaseStimTxn {
    int global_cycle;
    bool new_event;  // true on first frame of 9-frame event
    int event_id;    // actual XML event ID

    virtual ~BaseStimTxn() = default;
};

// Base output transaction interface
// All output transactions must have these fields
struct BaseOutTxn {
    int global_cycle;

    virtual ~BaseOutTxn() = default;
};

} // namespace verif_fw

#endif // TRANSACTION_CONCEPTS_H
