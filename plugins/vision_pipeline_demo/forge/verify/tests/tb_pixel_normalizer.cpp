// ════════════════════════════════════════════════════════════════════════
// C-sim testbench for pixel_normalizer — uses verif_fw::Driver<>
// Drives every pixel event through PixelNormalizerAdapter, checks
// normalized_pixel against an inline closed-form oracle (the same
// clamp(scale*pixel+offset, 0, 255) formula pixel_normalizer.cpp
// implements — see xml_event_reader.h's header comment for why this is
// duplicated rather than shared with the Python GoldenModelProvider).
// Usage: tb_pixel_normalizer <vision_pipeline_quickstart_golden.xml>
// ════════════════════════════════════════════════════════════════════════
#include "pixel_normalizer_adapter.h"
#include "xml_event_reader.h"
#include "driver.h"
#include "logging.h"
#include <cstdio>

static int expected_normalized_pixel(unsigned pixel) {
    int scaled = (static_cast<int>(pixel) * NORMALIZER_SCALE_NUM) / NORMALIZER_SCALE_DEN
                 + NORMALIZER_OFFSET;
    if (scaled < 0)   return 0;
    if (scaled > 255) return 255;
    return scaled;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <vision_pipeline_quickstart_golden.xml>\n", argv[0]);
        return 1;
    }

    auto events = load_vision_pixel_events(argv[1]);
    if (events.empty()) { fprintf(stderr, "No events loaded\n"); return 1; }

    vision_pipeline_demo::PixelNormalizerAdapter adapter;
    verif_fw::Driver<vision_pipeline_demo::PixelNormalizerStim,
                     vision_pipeline_demo::PixelNormalizerOut> driver(&adapter);

    verif_fw::ErrorLogger err_log;
    verif_fw::RunLogger   run_log;
    err_log.open("tb_pixel_normalizer_errors.csv");
    run_log.open("tb_pixel_normalizer_run.json");

    int global_cycle = 0;
    int errors       = 0;

    for (const auto &ev : events) {
        vision_pipeline_demo::PixelNormalizerStim stim;
        stim.global_cycle  = global_cycle++;
        stim.event_id       = ev.id;
        stim.new_event      = true;
        stim.pixel          = ev.pixel;
        stim.x              = ev.x;
        stim.y              = ev.y;
        stim.frame_id       = ev.frame_id;
        stim.tile_id        = ev.tile_id;
        stim.end_of_line    = ev.end_of_line;
        stim.end_of_frame   = ev.end_of_frame;
        stim.pixel_valid    = ev.pixel_valid;

        auto out = driver.drive(stim);

        int expected = expected_normalized_pixel(ev.pixel);
        if (static_cast<int>(out.normalized_pixel) != expected) {
            char msg[128];
            snprintf(msg, sizeof(msg), "event %d: normalized_pixel=%d expected=%d",
                     ev.id, (int)out.normalized_pixel, expected);
            err_log.log_error(stim.global_cycle, msg);
            ++errors;
        }
        if (static_cast<int>(out.normalized_valid) != ev.pixel_valid) {
            char msg[128];
            snprintf(msg, sizeof(msg), "event %d: normalized_valid=%d expected=%d",
                     ev.id, (int)out.normalized_valid, ev.pixel_valid);
            err_log.log_error(stim.global_cycle, msg);
            ++errors;
        }
    }

    run_log.log_run_info("pixel_normalizer", "csim", adapter.latency(), "local");
    err_log.close();
    run_log.close();

    printf("tb_pixel_normalizer: %zu events, %d errors\n", events.size(), errors);
    return errors > 0 ? 1 : 0;
}
