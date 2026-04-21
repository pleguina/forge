// ════════════════════════════════════════════════════════════════════════
// C-sim testbench for hit_decoder
// Reads trigger_demo_golden.xml, drives each channel, verifies phi+1
// correction and valid gating.
// Usage: tb_hit_decoder <golden.xml>
// ════════════════════════════════════════════════════════════════════════
#include "hit_decoder.h"
#include "xml_event_reader.h"
#include <cstdio>
#include <cstdlib>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <trigger_demo_golden.xml>\n", argv[0]);
        return 1;
    }

    auto events = load_trigger_events(argv[1]);
    if (events.empty()) { fprintf(stderr, "No events loaded\n"); return 1; }

    int errors = 0;

    for (const auto &ev : events) {
        for (int ch = 0; ch < 4; ch++) {
            ap_uint<RAW_HIT_W>     raw_hit   = ev.channels[ch].raw_hit;
            ap_uint<1>             raw_valid  = ev.channels[ch].valid;
            ap_uint<DECODED_HIT_W> decoded_hit;
            ap_uint<1>             decoded_valid;

            hit_decoder(raw_hit, raw_valid, decoded_hit, decoded_valid);

            // Expected: valid only if raw_valid AND raw_hit != 0
            bool expect_valid = (raw_valid && raw_hit != 0);
            if ((bool)decoded_valid != expect_valid) {
                printf("FAIL  event %d ch %d: decoded_valid=%d expected=%d\n",
                       ev.id, ch, (int)decoded_valid, (int)expect_valid);
                errors++;
                continue;
            }

            if (decoded_valid) {
                // Check phi correction: phi_out = phi_in + 1
                ap_uint<PHI_W> phi_in  = raw_hit.range(31, 16);
                ap_uint<PHI_W> phi_exp = phi_in + 1;
                ap_uint<PHI_W> phi_got = decoded_hit.range(31, 16);
                if (phi_got != phi_exp) {
                    printf("FAIL  event %d ch %d: phi=0x%04x expected=0x%04x\n",
                           ev.id, ch, (unsigned)phi_got, (unsigned)phi_exp);
                    errors++;
                }
                // Check eta passthrough
                ap_uint<ETA_W> eta_in  = raw_hit.range(15, 0);
                ap_uint<ETA_W> eta_got = decoded_hit.range(15, 0);
                if (eta_got != eta_in) {
                    printf("FAIL  event %d ch %d: eta=0x%04x expected=0x%04x\n",
                           ev.id, ch, (unsigned)eta_got, (unsigned)eta_in);
                    errors++;
                }
            }
        }
    }

    printf("tb_hit_decoder: %lu events, %d errors\n", events.size(), errors);
    return errors > 0 ? 1 : 0;
}
