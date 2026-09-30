// SPDX-License-Identifier: AGPL-3.0-only
#pragma once

#include <memory>
#include <string>

namespace namioto::audio {

/// Versions of the vendored DSP stack, for bug reports and a build smoke test.
std::string stretch_version();
std::string miniaudio_version();

/// Signalsmith Stretch for one mono float32 stream at a fixed sample rate.
///
/// Signalsmith has no rate setter: the rate is the ratio between the input and output frame
/// counts handed to `process`, so a caller picks the ratio per call. Only the input/output
/// latencies and the seek powder have to be accounted for by the caller.
class Stretcher {
public:
    Stretcher(int sample_rate, double block_ms, double overlap, bool split_computation);
    ~Stretcher();

    Stretcher(const Stretcher&) = delete;
    Stretcher& operator=(const Stretcher&) = delete;

    int input_latency() const;
    int output_latency() const;
    int output_seek_length(double speed) const;

    void reset();
    /// Aims at the start of `input` while producing at `speed` output frames per input frame.
    void output_seek(const float* input, int count);
    /// Consumes `input_count` input frames and writes `output_count` output frames.
    void process(const float* input, int input_count, float* output, int output_count);
    /// Drains the tail that is still inside the stretcher after the last `process`.
    void flush(float* output, int output_count);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace namioto::audio
