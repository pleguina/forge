#pragma once
// ════════════════════════════════════════════════════════════════════════
// pixel_normalizer DutAdapter — wires pixel_normalizer() into
// verif_fw::Driver<>.
// ════════════════════════════════════════════════════════════════════════
#include "dut_adapter.h"
#include "transaction_concepts.h"
#include "pixel_normalizer.h"

namespace vision_pipeline_demo {

struct PixelNormalizerStim : verif_fw::BaseStimTxn {
    pixel_t    pixel{0};
    coord_t    x{0};
    coord_t    y{0};
    frame_id_t frame_id{0};
    tile_id_t  tile_id{0};
    flag_t     end_of_line{0};
    flag_t     end_of_frame{0};
    flag_t     pixel_valid{0};
    bool valid() const { return static_cast<bool>(pixel_valid); }
};

struct PixelNormalizerOut : verif_fw::BaseOutTxn {
    pixel_t    normalized_pixel{0};
    coord_t    x{0};
    coord_t    y{0};
    frame_id_t frame_id{0};
    tile_id_t  tile_id{0};
    flag_t     end_of_line{0};
    flag_t     end_of_frame{0};
    flag_t     normalized_valid{0};
    bool valid() const { return static_cast<bool>(normalized_valid); }
};

class PixelNormalizerAdapter
    : public verif_fw::DutAdapterBase<PixelNormalizerStim, PixelNormalizerOut> {
public:
    void configure() override {}
    void reset()     override {}
    int latency()       const override { return 3; }  // HLS LATENCY min=3 max=3
    int drain_latency() const override { return 0; }

    PixelNormalizerOut tick(const PixelNormalizerStim& s) override {
        PixelNormalizerOut out;
        out.global_cycle = s.global_cycle;
        pixel_normalizer(
            s.pixel, s.x, s.y, s.frame_id, s.tile_id,
            s.end_of_line, s.end_of_frame, s.pixel_valid,
            out.normalized_pixel, out.x, out.y, out.frame_id, out.tile_id,
            out.end_of_line, out.end_of_frame, out.normalized_valid
        );
        return out;
    }
};

} // namespace vision_pipeline_demo
