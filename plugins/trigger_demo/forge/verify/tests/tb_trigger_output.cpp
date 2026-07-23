// ════════════════════════════════════════════════════════════════════════
// C-sim testbench for trigger_output — full pipeline via verif_fw::Driver<>
// TriggerOutputAdapter runs all 4 stages in a single tick().
// Verifies the packed trigger_word and out_valid against golden XML.
// Usage: tb_trigger_output <golden.xml>
// ════════════════════════════════════════════════════════════════════════
#include "trigger_output_adapter.h"
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

    trigger_demo::TriggerOutputAdapter adapter;
    verif_fw::Driver<trigger_demo::TriggerPipelineStim,
                     trigger_demo::TriggerPipelineOut> driver(&adapter);

    verif_fw::ErrorLogger err_log;
    verif_fw::RunLogger   run_log;
    err_log.open("tb_trigger_output_errors.csv");
    run_log.open("tb_trigger_output_run.json");

    int global_cycle = 0;
    int errors       = 0;

    for (const auto &ev : events) {
        trigger_demo::TriggerPipelineStim stim;
        stim.global_cycle = global_cycle++;
        stim.event_id     = ev.id;
        stim.new_event    = true;
        for (int ch = 0; ch < 4; ch++)
            stim.channels[ch] = ev.channels[ch];

        auto out = driver.drive(stim);

        if (static_cast<unsigned>(out.trigger_word) != ev.golden.word) {
            char msg[128];
            snprintf(msg, sizeof(msg),
                     "event %d: trigger_word=0x%08x expected=0x%08x",
                     ev.id, (unsigned)out.trigger_word, ev.golden.word);
            err_log.log_error(stim.global_cycle, msg);
            ++errors;
        }
        bool expect_valid = (ev.golden.n_hits > 0);
        if (static_cast<bool>(out.out_valid) != expect_valid) {
            char msg[128];
            snprintf(msg, sizeof(msg), "event %d: out_valid=%d expected=%d",
                     ev.id, (int)out.out_valid, (int)expect_valid);
            err_log.log_error(stim.global_cycle, msg);
            ++errors;
        }
    }

    run_log.log_run_info("trigger_output", "csim", adapter.latency(), "local");
    err_log.close();
    run_log.close();

    printf("tb_trigger_output (end-to-end): %zu events, %d errors\n",
           events.size(), errors);
    return errors > 0 ? 1 : 0;
}
