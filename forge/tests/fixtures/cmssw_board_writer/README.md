# BoardDataWriter output from a CMSSW job

`gt_input_packets.json` holds the frame span (`[first, last]`) of every
packet on every link of the input pattern files `L1GTEvaluationProducer`
wrote, with the writer's settings and the job's `Begin processing` log
lines. The data words are left out; the spans are what
`cmssw_sidecar.layout` has to reproduce.

The input writer is the one in `L1Trigger/Phase2L1GT/plugins/L1GTEvaluationProducer.cc`:
EMPv2, 9 frames per BX, board TMUX 1, channels GCT/GTT/CL2 at TMUX 6 and
GMT at TMUX 18, indices from `l1tGTBoardWriterVU13P_cff`.

Produced in CMSSW_20_0_0 (el9_amd64_gcc13), random seed 0:

| run | configuration | events | files |
|---|---|---|---|
| 1 | `L1Trigger/Phase2L1GT/test/gt_firmware_evaluation.py` as released (sha256 `a1dbd2c6…c321a3`) | 94 | `inputPattern_0.txt` (sha256 `0f690e48…ec613a2`) |
| 2 | the same with `maxEvents` 30 and the producer's `maxFrames` 300 | 30 | `inputPattern_0.txt` (16 events), `inputPattern_1.txt` (14 events) |

```
cmsRun gt_firmware_evaluation.py platform=VU13P > cmsRun.log
```
