#ifndef TRIGGER_TYPES_H
#define TRIGGER_TYPES_H

#include <ap_int.h>

// ── Widths ──────────────────────────────────────────────────────────────
static const int RAW_HIT_W      = 32;   // packed detector hit
static const int PHI_W          = 16;   // phi coordinate
static const int ETA_W          = 16;   // eta coordinate
static const int DECODED_HIT_W  = 32;   // decoded hit (phi:eta)
static const int N_CHANNELS     = 4;    // number of decoder channels
static const int NHITS_W        = 3;    // 0..4 valid hits
static const int QUALITY_W      = 8;    // trigger quality metric
static const int TRIGGER_WORD_W = 32;   // packed trigger output

// ── Trigger threshold ───────────────────────────────────────────────────
static const int TRIGGER_THRESHOLD = 2; // minimum hits for trigger accept

#endif // TRIGGER_TYPES_H
