#pragma once
// ════════════════════════════════════════════════════════════════════════
// hit_collector DutAdapter — wires hit_collector() into verif_fw::Driver<>
// Pre-runs hit_decoder internally so the adapter accepts raw channel data.
// ════════════════════════════════════════════════════════════════════════
#include "dut_adapter.h"
#include "transaction_concepts.h"
#include "hit_decoder.h"
#include "hit_collector.h"
#include "trigger_types.h"

namespace trigger_demo {

struct HitCollectorStim : verif_fw::BaseStimTxn {
    ap_uint<RAW_HIT_W> raw_hit[4]{};
    ap_uint<1>         raw_valid[4]{};
    bool valid() const {
        for (int i = 0; i < 4; i++) if (raw_valid[i]) return true;
        return false;
    }
};

struct HitCollectorOut : verif_fw::BaseOutTxn {
    ap_uint<NHITS_W> n_hits{0};
    ap_uint<PHI_W>   phi_sum{0};
    ap_uint<1>       col_valid{0};
    bool valid() const { return static_cast<bool>(col_valid); }
};

class HitCollectorAdapter
    : public verif_fw::DutAdapterBase<HitCollectorStim, HitCollectorOut> {
public:
    void configure() override {}
    void reset()     override {}
    int latency()       const override { return 0; }
    int drain_latency() const override { return 0; }

    HitCollectorOut tick(const HitCollectorStim& s) override {
        // Decode each channel first
        ap_uint<DECODED_HIT_W> dec_hit[4];
        ap_uint<1>             dec_valid[4];
        for (int ch = 0; ch < 4; ch++)
            hit_decoder(s.raw_hit[ch], s.raw_valid[ch], dec_hit[ch], dec_valid[ch]);

        HitCollectorOut out;
        out.global_cycle = s.global_cycle;
        hit_collector(
            dec_hit[0], dec_valid[0],
            dec_hit[1], dec_valid[1],
            dec_hit[2], dec_valid[2],
            dec_hit[3], dec_valid[3],
            out.n_hits, out.phi_sum, out.col_valid
        );
        return out;
    }
};

} // namespace trigger_demo
