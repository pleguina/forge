// ════════════════════════════════════════════════════════════════════════
// C-sim testbench for trigger_logic
// Runs the full pipeline (decoder→collector→trigger) against golden XML.
// Verifies trigger_accept, trigger_quality, trigger_valid.
// Usage: tb_trigger_logic <golden.xml>
// ════════════════════════════════════════════════════════════════════════
#include "trigger_logic.h"
#include "hit_collector.h"
#include "hit_decoder.h"
#include "xml_event_reader.h"
#include <cstdio>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <trigger_demo_golden.xml>\n", argv[0]);
        return 1;
    }

    auto events = load_trigger_events(argv[1]);
    if (events.empty()) { fprintf(stderr, "No events loaded\n"); return 1; }

    int errors = 0;

    for (const auto &ev : events) {
        // Decoder stage
        ap_uint<DECODED_HIT_W> dec_hit[4];
        ap_uint<1>             dec_valid[4];
        for (int ch = 0; ch < 4; ch++) {
            hit_decoder(
                ap_uint<RAW_HIT_W>(ev.channels[ch].raw_hit),
                ap_uint<1>(ev.channels[ch].valid),
                dec_hit[ch], dec_valid[ch]
            );
        }

        // Collector stage
        ap_uint<NHITS_W> n_hits;
        ap_uint<PHI_W>   phi_sum;
        ap_uint<1>       col_valid;
        hit_collector(
            dec_hit[0], dec_valid[0],
            dec_hit[1], dec_valid[1],
            dec_hit[2], dec_valid[2],
            dec_hit[3], dec_valid[3],
            n_hits, phi_sum, col_valid
        );

        // Trigger logic
        ap_uint<1>         trig_accept;
        ap_uint<QUALITY_W> trig_quality;
        ap_uint<1>         trig_valid;
        trigger_logic(n_hits, phi_sum, col_valid,
                      trig_accept, trig_quality, trig_valid);

        // Check against golden
        if ((int)trig_accept != ev.golden.accept) {
            printf("FAIL  event %d: trigger_accept=%d expected=%d\n",
                   ev.id, (int)trig_accept, ev.golden.accept);
            errors++;
        }
        if ((int)trig_quality != ev.golden.quality) {
            printf("FAIL  event %d: trigger_quality=%d expected=%d\n",
                   ev.id, (int)trig_quality, ev.golden.quality);
            errors++;
        }
    }

    printf("tb_trigger_logic: %lu events, %d errors\n", events.size(), errors);
    return errors > 0 ? 1 : 0;
}
