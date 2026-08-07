# ADR 0003: Vision packet record format

## Status

Accepted.

## Context

The packetizer path (`design_packetizer.yml` onward) multiplexes two
different record kinds — pixel-result and tile-statistics — from two
independent upstream paths onto one shared 256-bit
`forge.packet_stream.v1` beat (`data{256}`, `keep{32}`, `last{1}`, plus
ready/valid). Both record kinds, and the beat that carries them, need a
frozen, documented bit layout so the packer RTL, the golden model, and
any future consumer of `forge.packet_stream.v1` agree on the same shape
without re-deriving it from the RTL each time.

## Decision

- **Record layout (128 bits each, MSB-first):**
  `record_kind{2}`, then kind-specific fields
  (`normalized_pixel{8}`, `gradient_magnitude{12}`, `threshold_mask{1}`,
  `x{12}`, `y{12}`, `frame_id{16}`, `tile_id{16}`, `end_of_line{1}`,
  `end_of_frame{1}`, `reserved{...}` for pixel-result; the analogous
  tile-statistics fields for the other kind). `record_kind` alone
  disambiguates which layout applies — position within the beat carries
  no kind meaning.
- **Beat packing:** bits `[127:0]` carry whichever record is ready first
  in a given beat, bits `[255:128]` carry a second record if one is also
  ready the same beat, from either path. A beat carrying only one record
  packs it into `[127:0]` with `keep[15:0] = 16'hFFFF`,
  `keep[31:16] = 16'h0000`. Whether any given beat ever carries two
  records depends on real, clock-phase-dependent CDC crossing timing
  between the two independent `async_fifo` crossings feeding the
  packetizer — this is observed behavior from real simulation, not an
  engineered guarantee, and the packetizer's own stimulus checker
  verifies every record by content regardless of which slot it lands in.
- **Multiplexing point:** both record kinds cross into the output domain
  via their own independent `async_fifo` (not a shared arbiter/FIFO
  upstream of the packetizer) — `packetizer_rtl` is the only place the
  two record kinds meet.

## Consequences

- Any new record kind added to this packet stream must pick an unused
  `record_kind` value and document its own 128-bit field layout next to
  the existing two, in the same MSB-first convention.
- A golden model or downstream consumer decoding `forge.packet_stream.v1`
  must always branch on `record_kind` first — it must never assume a
  fixed slot-to-kind mapping.
- This format is specific to `vision_pipeline_demo`'s two record kinds;
  `forge.packet_stream.v1` itself (the generic 256-bit beat shape) is a
  FORGE-core schema and stays domain-neutral — this ADR only fixes how
  *this plugin* fills that beat.
