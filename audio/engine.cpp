// SPDX-License-Identifier: AGPL-3.0-only
#include "engine.h"

#include <algorithm>
#include <cmath>

namespace namioto::audio {

Engine::Engine(int sample_rate) : sample_rate_(sample_rate) {}

Engine::~Engine() = default;

void Engine::load(const float* samples, int64_t count) {
    source_.assign(samples, samples + std::max<int64_t>(0, count));
    stretcher_ = std::make_unique<Stretcher>(sample_rate_, 120.0, 4.0, false);
    feed_pos_ = 0.0;
    position_ = 0.0;
    origin_source_ = 0.0;
    generated_frames_ = 0;
    buffers_.clear();
    playing_ = false;
    finished_ = false;
}

void Engine::unload() {
    source_.clear();
    stretcher_.reset();
    feed_pos_ = 0.0;
    position_ = 0.0;
    origin_source_ = 0.0;
    generated_frames_ = 0;
    buffers_.clear();
    playing_ = false;
    finished_ = false;
}

double Engine::duration() const {
    return source_.empty() ? 0.0 : double(source_.size()) / sample_rate_;
}

void Engine::start_at(double seconds) {
    if (source_.empty() || stretcher_ == nullptr) {
        // no song: the submitted buffers are the whole source, so only the origin moves
        const double target = std::max(0.0, seconds);
        origin_source_ = target;
        generated_frames_ = 0;
        position_ = target;
        finished_ = false;
        return;
    }
    const double target = std::clamp(seconds, 0.0, duration());
    const int64_t start_frame = static_cast<int64_t>(std::llround(target * sample_rate_));
    const int lead = std::max(1, stretcher_->output_seek_length(speed_));

    // `outputSeek` is handed the frames that follow the target: their beginning is where the next
    // processed output will line up, so the read cursor resumes right after them.
    pre_roll_.assign(lead, 0.0f);
    for (int i = 0; i < lead; ++i) {
        const int64_t index = start_frame + i;
        if (index < static_cast<int64_t>(source_.size())) {
            pre_roll_[i] = source_[index];
        }
    }
    stretcher_->output_seek(pre_roll_.data(), lead);

    feed_pos_ = double(start_frame) + lead;
    position_ = target;
    origin_source_ = target;
    generated_frames_ = 0;
    finished_ = false;
}

void Engine::play(double seconds) {
    start_at(seconds);
    playing_ = true;
}

void Engine::pause() {
    playing_ = false;
}

void Engine::seek(double seconds) {
    const bool was_playing = playing_;
    start_at(seconds);
    playing_ = was_playing;
}

void Engine::set_speed(double speed) {
    speed_ = std::clamp(speed, 0.01, 100.0);
}

void Engine::set_gain(double gain) {
    gain_ = std::max(0.0, gain);
}

void Engine::submit_buffer(const float* data, int64_t count, double start_seconds, double gain, int id) {
    if (count <= 0) {
        return;
    }
    NoteBuffer buffer;
    buffer.data.assign(data, data + count);
    buffer.start_seconds = start_seconds;
    buffer.gain = static_cast<float>(gain);
    buffer.id = id;

    for (auto& existing : buffers_) {
        if (existing.id == id) {
            existing = std::move(buffer);
            return;
        }
    }
    buffers_.push_back(std::move(buffer));
}

void Engine::clear_buffers() {
    buffers_.clear();
}

void Engine::mix_buffers(float* output, int frames, uint64_t generated) const {
    const int64_t block_start = static_cast<int64_t>(generated);
    const int64_t block_end = block_start + frames;
    for (const auto& buffer : buffers_) {
        const double start_frame = (buffer.start_seconds - origin_source_) / speed_ * sample_rate_;
        const int64_t start = static_cast<int64_t>(std::llround(start_frame));
        const int64_t end = start + static_cast<int64_t>(buffer.data.size());
        const int64_t from = std::max(start, block_start);
        const int64_t to = std::min(end, block_end);
        for (int64_t frame = from; frame < to; ++frame) {
            output[frame - block_start] += buffer.data[frame - start] * buffer.gain;
        }
    }
}

void Engine::pull(float* output, int frames) {
    if (frames <= 0) {
        return;
    }
    if (!playing_) {
        std::fill(output, output + frames, 0.0f);
        return;
    }

    if (source_.empty() || stretcher_ == nullptr) {
        // no song: the output is whatever buffers were submitted
        std::fill(output, output + frames, 0.0f);
    } else {
        // Playback speed is source frames per output frame, so a rate of two hands the stretcher
        // twice the input for the same output. The cursor keeps its fraction so the average stays
        // exact across rounded blocks.
        const double next_pos = feed_pos_ + frames * speed_;
        const int64_t feed_start = static_cast<int64_t>(std::llround(feed_pos_));
        int input_count = static_cast<int>(std::max<int64_t>(1, std::llround(next_pos) - feed_start));

        input_.resize(input_count);
        for (int i = 0; i < input_count; ++i) {
            const int64_t index = feed_start + i;
            input_[i] = (index >= 0 && index < static_cast<int64_t>(source_.size())) ? source_[index] : 0.0f;
        }

        stretcher_->process(input_.data(), input_count, output, frames);
        feed_pos_ = next_pos;

        if (gain_ != 1.0) {
            const float gain = static_cast<float>(gain_);
            for (int i = 0; i < frames; ++i) {
                output[i] *= gain;
            }
        }
    }

    mix_buffers(output, frames, generated_frames_);
    generated_frames_ += static_cast<uint64_t>(frames);
    position_ += double(frames) / sample_rate_ * speed_;

    if (!source_.empty() && position_ >= duration()) {
        // the stretcher still holds an outputLatency tail; it is dropped rather than flushed, which
        // is what the player this replaces did at the end of a song
        position_ = duration();
        finished_ = true;
        playing_ = false;
    }
}

}  // namespace namioto::audio
