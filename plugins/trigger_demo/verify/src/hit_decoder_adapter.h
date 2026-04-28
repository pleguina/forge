#pragma once
// ════════════════════════════════════════════════════════════════════════
// hit_decoder DutAdapter — wires hit_decoder() into verif_fw::Driver<>
// ════════════════════════════════════════════════════════════════════════
#include "dut_adapter.h"
#include "transaction_concepts.h"
#include "hit_decoder.h"
#include "trigger_types.h"

namespace trigger_demo {

struct HitDecoderStim : verif_fw::BaseStimTxn {
    ap_uint<RAW_HIT_W> raw_hit{0};
    ap_uint<1>         raw_valid{0};
    int                channel_id{0};
    bool valid() const { return static_cast<bool>(raw_valid); }
};

struct HitDecoderOut : verif_fw::BaseOutTxn {
    ap_uint<DECODED_HIT_W> decoded_hit{0};
    ap_uint<1>             decoded_valid{0};
    bool valid() const { return static_cast<bool>(decoded_valid); }
};

class HitDecoderAdapter
    : public verif_fw::DutAdapterBase<HitDecoderStim, HitDecoderOut> {
public:
    void configure() override {}
    void reset()     override {}
    int latency()       const override { return 0; }  // combinational
    int drain_latency() const override { return 0; }

    HitDecoderOut tick(const HitDecoderStim& s) override {
        HitDecoderOut out;
        out.global_cycle = s.global_cycle;
        hit_decoder(s.raw_hit, s.raw_valid, out.decoded_hit, out.decoded_valid);
        return out;
    }
};

} // namespace trigger_demo
