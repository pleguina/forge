#include "pipeline_types.h"

PipelineWord stream_source(unsigned in_data) {
    return PipelineWord{in_data, true};
}
