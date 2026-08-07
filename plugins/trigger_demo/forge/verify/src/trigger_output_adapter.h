#pragma once
// ════════════════════════════════════════════════════════════════════════
// trigger_output DutAdapter — full 4-stage pipeline adapter.
// Takes raw channel data and runs decoder→collector→logic→output
// in a single tick(), exercising the full end-to-end path.
// ════════════════════════════════════════════════════════════════════════
#include "dut_adapter.h"
#include "transaction_concepts.h"
#include "hit_decoder.h"
#include "hit_collector.h"
#include "trigger_logic.h"
#include "trigger_output.h"
#include "trigger_types.h"
#include "xml_event_reader.h"  // for TriggerChannel

namespace trigger_demo {

struct TriggerPipelineStim : verif_fw::BaseStimTxn {
    TriggerChannel channels[4]{};
    bool valid() const {
        for (const auto& c : channels) if (c.valid) return true;
        return false;
    }
};

struct TriggerPipelineOut : verif_fw::BaseOutTxn {
    ap_uint<TRIGGER_WORD_W> trigger_word{0};
    ap_uint<1>              out_valid{0};
    bool valid() const { return static_cast<bool>(out_valid); }
};

class TriggerOutputAdapter
    : public verif_fw::DutAdapterBase<TriggerPipelineStim, TriggerPipelineOut> {
public:
    void configure() override {}
    void reset()     override {}
    int latency()       const override { return 0; }
    int drain_latency() const override { return 0; }

    TriggerPipelineOut tick(const TriggerPipelineStim& s) override {
        // Stage 1: decode
        ap_uint<DECODED_HIT_W> dec_hit[4];
        ap_uint<1>             dec_valid[4];
        for (int ch = 0; ch < 4; ch++)
            hit_decoder(ap_uint<RAW_HIT_W>(s.channels[ch].raw_hit),
                        ap_uint<1>(s.channels[ch].valid),
                        dec_hit[ch], dec_valid[ch]);

        // Stage 2: collect
        ap_uint<NHITS_W> n_hits;
        ap_uint<PHI_W>   phi_sum;
        ap_uint<1>       col_valid;
        hit_collector(dec_hit[0], dec_valid[0], dec_hit[1], dec_valid[1],
                      dec_hit[2], dec_valid[2], dec_hit[3], dec_valid[3],
                      n_hits, phi_sum, col_valid);

        // Stage 3: trigger logic
        ap_uint<1>         trig_accept;
        ap_uint<QUALITY_W> trig_quality;
        ap_uint<1>         trig_valid;
        trigger_logic(n_hits, phi_sum, col_valid,
                      trig_accept, trig_quality, trig_valid);

        // Stage 4: trigger output
        TriggerPipelineOut out;
        out.global_cycle = s.global_cycle;
        trigger_output(trig_accept, trig_quality, trig_valid,
                       out.trigger_word, out.out_valid);
        return out;
    }
};

} // namespace trigger_demo
