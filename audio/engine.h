// SPDX-License-Identifier: AGPL-3.0-only
#pragma once

#include <cstdint>
#include <memory>
#include <vector>

#include "audio.h"

namespace namioto::audio {

/// The song's transport: source samples in, stretched frames out.
///
/// `pull` is the whole realtime path and is device-agnostic, so it can be driven frame by frame in
/// a test. The position stays on the source timeline and is counted in output frames pulled at the
/// current speed, which is the ground truth a device clock would otherwise provide.
class Engine {
public:
    explicit Engine(int sample_rate);
    ~Engine();

    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    void load(const float* samples, int64_t count);
    void unload();
    bool loaded() const { return !source_.empty(); }

    double duration() const;
    double position() const { return position_; }
    bool playing() const { return playing_; }
    bool finished() const { return finished_; }

    void play(double seconds);
    void pause();
    void seek(double seconds);

    void set_speed(double speed);
    double speed() const { return speed_; }
    void set_gain(double gain);
    double gain() const { return gain_; }

    /// Queues a rendered mono buffer to be mixed into the output from `start_seconds` on the source
    /// timeline, replacing any buffer with the same `id`. Python renders note programs and previews
    /// at the current speed; the engine only mixes them.
    void submit_buffer(const float* data, int64_t count, double start_seconds, double gain, int id);
    void clear_buffers();

    /// Fills `frames` output frames; silence while paused, the source while playing.
    void pull(float* output, int frames);

private:
    struct NoteBuffer {
        std::vector<float> data;
        double start_seconds = 0.0;
        double gain = 1.0;
        int id = 0;
    };

    void start_at(double seconds);
    void mix_buffers(float* output, int frames, uint64_t generated) const;

    int sample_rate_;
    std::vector<float> source_;
    std::unique_ptr<Stretcher> stretcher_;

    double feed_pos_ = 0.0;  // source frame position handed to the stretcher next, fractional
    double position_ = 0.0;  // source seconds heard next
    double speed_ = 1.0;
    double gain_ = 1.0;
    bool playing_ = false;
    bool finished_ = false;

    double origin_source_ = 0.0;      // source seconds the current generation started at
    uint64_t generated_frames_ = 0;   // output frames produced since that origin
    std::vector<NoteBuffer> buffers_;

    std::vector<float> input_;    // scratch for the source block handed to the stretcher
    std::vector<float> pre_roll_;  // scratch for the seek block
};

}  // namespace namioto::audio
