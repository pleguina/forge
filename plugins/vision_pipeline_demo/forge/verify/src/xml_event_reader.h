#ifndef VISION_PIPELINE_XML_EVENT_READER_H
#define VISION_PIPELINE_XML_EVENT_READER_H

// ════════════════════════════════════════════════════════════════════════
// Lightweight golden-XML event reader for vision_pipeline_demo's
// quickstart tier. No external dependencies — parses the known
// vision_pipeline_quickstart_golden.xml format using stdio/sscanf, the
// same pattern plugins/trigger_demo/forge/verify/src/xml_event_reader.h
// established.
//
// Unlike trigger_demo's golden XML, this dataset carries only `<in ...>`
// values — no hand-authored `<golden ...>` tags (release-plan Phase 10,
// slice 10.1, Decision C). Expected output is computed here, inline,
// using the same closed-form formula
// algo/normalizer/pixel_normalizer.cpp implements — a second, independent
// implementation of the same trivial oracle for the C-sim testbench's
// own use, deliberately not shared code with the Python
// GoldenModelProvider that drives the RTL-level stimulus (see
// forge/verify/tools/golden_model_provider.py) — the algorithm is simple
// enough that this duplication is a cheap, honest choice, not an
// accidental drift risk.
// ════════════════════════════════════════════════════════════════════════

#include <vector>
#include <cstdio>
#include <cstdint>
#include <cstring>

struct VisionPixelEvent {
    int      id;
    uint32_t pixel;
    uint32_t x;
    uint32_t y;
    uint32_t frame_id;
    uint32_t tile_id;
    int      end_of_line;
    int      end_of_frame;
    int      pixel_valid;
};

inline std::vector<VisionPixelEvent> load_vision_pixel_events(const char *path) {
    std::vector<VisionPixelEvent> events;
    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "ERROR: cannot open golden XML: %s\n", path);
        return events;
    }

    char line[512];
    VisionPixelEvent ev;
    bool have_id = false;

    while (fgets(line, sizeof(line), f)) {
        int id;
        if (sscanf(line, " <event id=\"%d\">", &id) == 1) {
            memset(&ev, 0, sizeof(ev));
            ev.id = id;
            have_id = true;
            continue;
        }

        unsigned pixel, x, y, frame_id, tile_id, eol, eof, valid;
        if (have_id &&
            sscanf(line,
                   " <in pixel=\"0x%x\" x=\"%u\" y=\"%u\" frame_id=\"%u\" "
                   "tile_id=\"%u\" end_of_line=\"%u\" end_of_frame=\"%u\" "
                   "pixel_valid=\"%u\"",
                   &pixel, &x, &y, &frame_id, &tile_id, &eol, &eof, &valid) == 8) {
            ev.pixel        = pixel;
            ev.x            = x;
            ev.y            = y;
            ev.frame_id     = frame_id;
            ev.tile_id      = tile_id;
            ev.end_of_line  = static_cast<int>(eol);
            ev.end_of_frame = static_cast<int>(eof);
            ev.pixel_valid  = static_cast<int>(valid);
            events.push_back(ev);
            have_id = false;
            continue;
        }
    }

    fclose(f);
    return events;
}

#endif // VISION_PIPELINE_XML_EVENT_READER_H
