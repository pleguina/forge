#ifndef XML_EVENT_READER_H
#define XML_EVENT_READER_H

// ════════════════════════════════════════════════════════════════════════
// Lightweight golden-XML event reader for the trigger_demo plugin.
// No external dependencies — parses the known trigger_demo_golden.xml
// format using stdio / sscanf.
// ════════════════════════════════════════════════════════════════════════

#include <vector>
#include <cstdio>
#include <cstdint>
#include <cstring>

struct TriggerChannel {
    uint32_t raw_hit;
    int      valid;
};

struct TriggerGolden {
    int      n_hits;
    uint32_t phi_sum;
    int      accept;
    int      quality;
    uint32_t word;
};

struct TriggerEvent {
    int             id;
    int             bx;
    TriggerChannel  channels[4];
    TriggerGolden   golden;
};

inline std::vector<TriggerEvent> load_trigger_events(const char *path) {
    std::vector<TriggerEvent> events;
    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "ERROR: cannot open golden XML: %s\n", path);
        return events;
    }

    char line[512];
    TriggerEvent ev;
    bool in_event = false;

    while (fgets(line, sizeof(line), f)) {
        int id, bx;
        if (sscanf(line, " <event id=\"%d\" bx=\"%d\">", &id, &bx) == 2) {
            memset(&ev, 0, sizeof(ev));
            ev.id = id;
            ev.bx = bx;
            in_event = true;
            continue;
        }

        unsigned raw;
        int cid, vid;
        if (in_event &&
            sscanf(line, " <ch id=\"%d\" raw=\"0x%x\" valid=\"%d\"",
                   &cid, &raw, &vid) == 3) {
            if (cid >= 0 && cid < 4) {
                ev.channels[cid].raw_hit = raw;
                ev.channels[cid].valid   = vid;
            }
            continue;
        }

        unsigned phi_s, word;
        int nh, acc, qual;
        if (in_event &&
            sscanf(line,
                   " <golden n_hits=\"%d\" phi_sum=\"0x%x\" accept=\"%d\" "
                   "quality=\"%d\" word=\"0x%x\"",
                   &nh, &phi_s, &acc, &qual, &word) == 5) {
            ev.golden.n_hits   = nh;
            ev.golden.phi_sum  = phi_s;
            ev.golden.accept   = acc;
            ev.golden.quality  = qual;
            ev.golden.word     = word;
            continue;
        }

        if (in_event && strstr(line, "</event>")) {
            events.push_back(ev);
            in_event = false;
        }
    }

    fclose(f);
    return events;
}

#endif // XML_EVENT_READER_H
