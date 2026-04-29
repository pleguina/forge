#pragma once
// ════════════════════════════════════════════════════════════════════════
// trigger_logic DutAdapter — wires trigger_logic() into verif_fw::Driver<>
// Accepts pre-collected (n_hits, phi_sum, col_valid) as stimulus.
// ════════════════════════════════════════════════════════════════════════
#include "dut_adapter.h"
#include "transaction_concepts.h"
#include "trigger_logic.h"
#include "trigger_types.h"

namespace trigger_demo {

struct TriggerLogicStim : verif_fw::BaseStimTxn {
    ap_uint<NHITS_W> n_hits{0};
    ap_uint<PHI_W>   phi_sum{0};
    ap_uint<1>       col_valid{0};
    bool valid() const { return static_cast<bool>(col_valid); }
};

struct TriggerLogicOut : verif_fw::BaseOutTxn {
    ap_uint<1>         trig_accept{0};
    ap_uint<QUALITY_W> trig_quality{0};
    ap_uint<1>         trig_valid{0};
    bool valid() const { return static_cast<bool>(trig_valid); }
};

class TriggerLogicAdapter
    : public verif_fw::DutAdapterBase<TriggerLogicStim, TriggerLogicOut> {
public:
    void configure() override {}
    void reset()     override {}
    int latency()       const override { return 0; }
    int drain_latency() const override { return 0; }

    TriggerLogicOut tick(const TriggerLogicStim& s) override {
        TriggerLogicOut out;
        out.global_cycle = s.global_cycle;
        trigger_logic(s.n_hits, s.phi_sum, s.col_valid,
                      out.trig_accept, out.trig_quality, out.trig_valid);
        return out;
    }
};

} // namespace trigger_demo
