// ════════════════════════════════════════════════════════════════════════
// C-sim testbench for hit_collector
// Feeds decoded hits from golden XML into the 4-channel collector and
// verifies n_hits, phi_sum, collector_valid.
// Usage: tb_hit_collector <golden.xml>
// ════════════════════════════════════════════════════════════════════════
#include "hit_collector.h"
#include "hit_decoder.h"       // run decoder first to produce decoded hits
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
        // First pass through decoders to get what the collector actually sees
        ap_uint<DECODED_HIT_W> dec_hit[4];
        ap_uint<1>             dec_valid[4];

        for (int ch = 0; ch < 4; ch++) {
            hit_decoder(
                ap_uint<RAW_HIT_W>(ev.channels[ch].raw_hit),
                ap_uint<1>(ev.channels[ch].valid),
                dec_hit[ch], dec_valid[ch]
            );
        }

        // Run collector
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

        // Check against golden
        if ((int)n_hits != ev.golden.n_hits) {
            printf("FAIL  event %d: n_hits=%d expected=%d\n",
                   ev.id, (int)n_hits, ev.golden.n_hits);
            errors++;
        }
        if ((unsigned)phi_sum != ev.golden.phi_sum) {
            printf("FAIL  event %d: phi_sum=0x%04x expected=0x%04x\n",
                   ev.id, (unsigned)phi_sum, ev.golden.phi_sum);
            errors++;
        }
        bool expect_valid = (ev.golden.n_hits > 0);
        if ((bool)col_valid != expect_valid) {
            printf("FAIL  event %d: collector_valid=%d expected=%d\n",
                   ev.id, (int)col_valid, (int)expect_valid);
            errors++;
        }
    }

    printf("tb_hit_collector: %lu events, %d errors\n", events.size(), errors);
    return errors > 0 ? 1 : 0;
}
