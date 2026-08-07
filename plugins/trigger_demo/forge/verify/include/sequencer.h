#ifndef SEQUENCER_H
#define SEQUENCER_H

#include "transaction_concepts.h"
#include <vector>
#include <queue>
#include <stdexcept>

namespace verif_fw {

// Template-based sequencer - works with any stimulus transaction type
template<typename StimTxn>
class Sequencer {
    std::queue<StimTxn> sequence_;

public:
    Sequencer() = default;

    // Load a pre-generated sequence
    void load_sequence(const std::vector<StimTxn>& seq) {
        for (const auto& txn : seq) {
            sequence_.push(txn);
        }
    }

    // Enqueue a single transaction
    void enqueue(const StimTxn& txn) {
        sequence_.push(txn);
    }

    // Get next transaction
    StimTxn next() {
        if (sequence_.empty()) {
            throw std::runtime_error("Sequencer: No more transactions");
        }
        StimTxn txn = sequence_.front();
        sequence_.pop();
        return txn;
    }

    bool has_next() const {
        return !sequence_.empty();
    }

    size_t size() const {
        return sequence_.size();
    }

    void clear() {
        while (!sequence_.empty()) {
            sequence_.pop();
        }
    }
};

} // namespace verif_fw

#endif // SEQUENCER_H
