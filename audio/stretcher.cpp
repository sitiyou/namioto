// SPDX-License-Identifier: AGPL-3.0-only
#include "audio.h"

#include <algorithm>
#include <cmath>

#include "signalsmith-stretch.h"

namespace namioto::audio {

std::string stretch_version() {
    using Stretch = signalsmith::stretch::SignalsmithStretch<float>;
    const auto& version = Stretch::version;
    return std::to_string(version[0]) + "." + std::to_string(version[1]) + "." + std::to_string(version[2]);
}

struct Stretcher::Impl {
    using Stretch = signalsmith::stretch::SignalsmithStretch<float>;

    Stretch stretch;
    std::vector<float> empty;  // planar input/output for the single channel
    float* channels[1] = {nullptr};
};

Stretcher::Stretcher(int sample_rate, double block_ms, double overlap, bool split_computation)
    : impl_(std::make_unique<Impl>()) {
    const int block = std::max(1, static_cast<int>(sample_rate * block_ms / 1000.0));
    const int interval = std::max(1, static_cast<int>(block / overlap));
    impl_->stretch.configure(1, block, interval, split_computation);
}

Stretcher::~Stretcher() = default;

int Stretcher::input_latency() const {
    return impl_->stretch.inputLatency();
}

int Stretcher::output_latency() const {
    return impl_->stretch.outputLatency();
}

int Stretcher::output_seek_length(double speed) const {
    return impl_->stretch.outputSeekLength(static_cast<float>(speed));
}

void Stretcher::reset() {
    impl_->stretch.reset();
}

void Stretcher::output_seek(const float* input, int count) {
    impl_->channels[0] = const_cast<float*>(input);
    impl_->stretch.outputSeek(impl_->channels, count);
}

void Stretcher::process(const float* input, int input_count, float* output, int output_count) {
    if (output_count <= 0) {
        return;
    }
    impl_->channels[0] = const_cast<float*>(input);
    float* out_channels[1] = {output};
    impl_->stretch.process(impl_->channels, input_count, out_channels, output_count);
}

void Stretcher::flush(float* output, int output_count) {
    if (output_count <= 0) {
        return;
    }
    float* out_channels[1] = {output};
    impl_->stretch.flush(out_channels, output_count);
}

}  // namespace namioto::audio
