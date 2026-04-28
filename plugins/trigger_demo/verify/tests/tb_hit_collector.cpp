// ════════════════════════════════════════════════════════════════════════
// C-sim testbench for hit_collector — uses verif_fw::Driver<>
// Feeds raw channel data through HitCollectorAdapter (which runs
// hit_decoder internally) and verifies n_hits, phi_sum, col_valid.
// Usage: tb_hit_collector <golden.xml>
// ════════════════════════════════════════════════════════════════════════
#include "hit_collector_adapter.h"
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

    trigger_demo::HitCollectorAdapter adapter;
    verif_fw::Driver<trigger_demo::HitCollectorStim,
                     trigger_demo::HitCollectorOut> driver(&adapter);

    verif_fw::ErrorLogger err_log;
    verif_fw::RunLogger   run_log;
    err_log.open("tb_hit_collector_errors.csv");
    run_log.open("tb_hit_collector_run.json");

    int global_cycle = 0;
    int errors       = 0;

    for (const auto &ev : events) {
        trigger_demo::HitCollectorStim stim;
        stim.global_cycle = global_cycle++;
        stim.event_id     = ev.id;
        stim.new_event    = true;
        for (int ch = 0; ch < 4; ch++) {
            stim.raw_hit[ch]   = ev.channels[ch].raw_hit;
            stim.raw_valid[ch] = ev.channels[ch].valid;
        }

        auto out = driver.drive(stim);

        if (static_cast<int>(out.n_hits) != ev.golden.n_hits) {
            char msg[128];
            snprintf(msg, sizeof(msg), "event %d: n_hits=%d expected=%d",
                     ev.id, (int)out.n_hits, ev.golden.n_hits);
            err_log.log_error(stim.global_cycle, msg);
            ++errors;
        }
        if (static_cast<unsigned>(out.phi_sum) != ev.golden.phi_sum) {
            char msg[128];
            snprintf(msg, sizeof(msg), "event %d: phi_sum=0x%04x expected=0x%04x",
                     ev.id, (unsigned)out.phi_sum, ev.golden.phi_sum);
            err_log.log_error(stim.global_cycle, msg);
            ++errors;
        }
        bool expect_valid = (ev.golden.n_hits > 0);
        if (static_cast<bool>(out.col_valid) != expect_valid) {
            char msg[128];
            snprintf(msg, sizeof(msg), "event %d: col_valid=%d expected=%d",
                     ev.id, (int)out.col_valid, (int)expect_valid);
            err_log.log_error(stim.global_cycle, msg);
            ++errors;
        }
    }

    run_log.log_run_info("hit_collector", "csim", adapter.latency(), "local");
    err_log.close();
    run_log.close();

    printf("tb_hit_collector: %zu events, %d errors\n", events.size(), errors);
    return errors > 0 ? 1 : 0;
}
