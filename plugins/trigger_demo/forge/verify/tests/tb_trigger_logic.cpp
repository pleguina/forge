// ════════════════════════════════════════════════════════════════════════
// C-sim testbench for trigger_logic — uses verif_fw::Driver<>
// Drives HitCollectorAdapter for the collect stage, then feeds output
// into TriggerLogicAdapter.  Verifies accept, quality, and valid.
// Usage: tb_trigger_logic <golden.xml>
// ════════════════════════════════════════════════════════════════════════
#include "hit_collector_adapter.h"
#include "trigger_logic_adapter.h"
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

    // Two-stage chain: collect → logic
    trigger_demo::HitCollectorAdapter  col_adapter;
    trigger_demo::TriggerLogicAdapter  logic_adapter;
    verif_fw::Driver<trigger_demo::HitCollectorStim,
                     trigger_demo::HitCollectorOut> col_driver(&col_adapter);
    verif_fw::Driver<trigger_demo::TriggerLogicStim,
                     trigger_demo::TriggerLogicOut> logic_driver(&logic_adapter);

    verif_fw::ErrorLogger err_log;
    verif_fw::RunLogger   run_log;
    err_log.open("tb_trigger_logic_errors.csv");
    run_log.open("tb_trigger_logic_run.json");

    int global_cycle = 0;
    int errors       = 0;

    for (const auto &ev : events) {
        // Stage 1: collect
        trigger_demo::HitCollectorStim col_stim;
        col_stim.global_cycle = global_cycle;
        col_stim.event_id     = ev.id;
        col_stim.new_event    = true;
        for (int ch = 0; ch < 4; ch++) {
            col_stim.raw_hit[ch]   = ev.channels[ch].raw_hit;
            col_stim.raw_valid[ch] = ev.channels[ch].valid;
        }
        auto col_out = col_driver.drive(col_stim);

        // Stage 2: trigger logic
        trigger_demo::TriggerLogicStim logic_stim;
        logic_stim.global_cycle = global_cycle++;
        logic_stim.event_id     = ev.id;
        logic_stim.new_event    = true;
        logic_stim.n_hits       = col_out.n_hits;
        logic_stim.phi_sum      = col_out.phi_sum;
        logic_stim.col_valid    = col_out.col_valid;
        auto logic_out = logic_driver.drive(logic_stim);

        if (static_cast<int>(logic_out.trig_accept) != ev.golden.accept) {
            char msg[128];
            snprintf(msg, sizeof(msg), "event %d: trig_accept=%d expected=%d",
                     ev.id, (int)logic_out.trig_accept, ev.golden.accept);
            err_log.log_error(logic_stim.global_cycle, msg);
            ++errors;
        }
        if (static_cast<int>(logic_out.trig_quality) != ev.golden.quality) {
            char msg[128];
            snprintf(msg, sizeof(msg), "event %d: trig_quality=%d expected=%d",
                     ev.id, (int)logic_out.trig_quality, ev.golden.quality);
            err_log.log_error(logic_stim.global_cycle, msg);
            ++errors;
        }
    }

    run_log.log_run_info("trigger_logic", "csim", logic_adapter.latency(), "local");
    err_log.close();
    run_log.close();

    printf("tb_trigger_logic: %zu events, %d errors\n", events.size(), errors);
    return errors > 0 ? 1 : 0;
}
