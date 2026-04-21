#include "pipeline_types.h"

PipelineWord merge_stage(PipelineWord in_stream_0, PipelineWord in_stream_1) {
    return in_stream_0.valid ? in_stream_0 : in_stream_1;
}
