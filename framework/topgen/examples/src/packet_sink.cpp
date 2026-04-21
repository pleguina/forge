#include "pipeline_types.h"

unsigned packet_sink(PipelineWord in_payload) {
    return in_payload.valid ? in_payload.value : 0U;
}
