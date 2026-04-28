// ════════════════════════════════════════════════════════════════════════
// C-sim testbench for hit_decoder — uses verif_fw::Driver<>
// Reads golden XML, drives each channel through HitDecoderAdapter,
// verifies phi+1 correction and valid gating.
// Usage: tb_hit_decoder <golden.xml>
// ════════════════════════════════════════════════════════════════════════
#include "hit_decoder_adapter.h"
#include "xml_event_reader.h"
#include "driver.h"
#include "logging.h"
#include <cstdio>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <trigger_demo_golden.xml>\n", argv[0]);
        return 1;
    }

    auto events = load_trigger_events(argv[1]);
    if (events.empty()) { fprintf(stderr, "No events loaded\n"); return 1; }

    trigger_demo::HitDecoderAdapter adapter;
    verif_fw::Driver<trigger_demo::HitDecoderStim,
                     trigger_demo::HitDecoderOut> driver(&adapter);

    verif_fw::ErrorLogger err_log;
    verif_fw::RunLogger   run_log;
    err_log.open("tb_hit_decoder_errors.csv");
    run_log.open("tb_hit_decoder_run.json");

    int global_cycle = 0;
    int errors       = 0;

    for (const auto &ev : events) {
        for (int ch = 0; ch < 4; ch++, ++global_cycle) {
            trigger_demo::HitDecoderStim stim;
            stim.global_cycle = global_cycle;
            stim.event_id     = ev.id;
            stim.new_event    = (ch == 0);
            stim.channel_id   = ch;
            stim.raw_hit      = ev.channels[ch].raw_hit;
            stim.raw_valid    = ev.channels[ch].valid;

            auto out = driver.drive(stim);

            bool expect_valid = stim.raw_valid && (ev.channels[ch].raw_hit != 0);
            if (static_cast<bool>(out.decoded_valid) != expect_valid) {
                char msg[128];
                snprintf(msg, sizeof(msg),
                         "event %d ch %d: decoded_valid=%d expected=%d",
                         ev.id, ch, (int)out.decoded_valid, (int)expect_valid);
                err_log.log_error(global_cycle, msg);
                ++errors;
                continue;
            }

            if (out.decoded_valid) {
                ap_uint<PHI_W> phi_exp =
                    ap_uint<PHI_W>(ev.channels[ch].raw_hit >> 16) + 1;
                ap_uint<PHI_W> phi_got = out.decoded_hit.range(31, 16);
                if (phi_got != phi_exp) {
                    char msg[128];
                    snprintf(msg, sizeof(msg),
                             "event %d ch %d: phi=0x%04x expected=0x%04x",
                             ev.id, ch, (unsigned)phi_got, (unsigned)phi_exp);
                    err_log.log_error(global_cycle, msg);
                    ++errors;
                }
                ap_uint<ETA_W> eta_exp =
                    ap_uint<ETA_W>(ev.channels[ch].raw_hit & 0xFFFF);
                ap_uint<ETA_W> eta_got = out.decoded_hit.range(15, 0);
                if (eta_got != eta_exp) {
                    char msg[128];
                    snprintf(msg, sizeof(msg),
                             "event %d ch %d: eta=0x%04x expected=0x%04x",
                             ev.id, ch, (unsigned)eta_got, (unsigned)eta_exp);
                    err_log.log_error(global_cycle, msg);
                    ++errors;
                }
            }
        }
    }

    run_log.log_run_info("hit_decoder", "csim", adapter.latency(), "local");
    err_log.close();
    run_log.close();

    printf("tb_hit_decoder: %zu events, %d errors\n", events.size(), errors);
    return errors > 0 ? 1 : 0;
}
