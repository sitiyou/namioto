// SPDX-License-Identifier: AGPL-3.0-only
#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "engine.h"
#include "ring.h"

namespace namioto::audio {

/// The realtime output: an audio device whose callback only copies from a ring a background thread
/// keeps full by pulling from the `Engine`.
///
/// The device callback never touches the engine, so it never takes a lock and never runs the
/// stretcher; `mutex_` serialises the engine between that producer thread and the caller's
/// transport commands. The position is counted in frames the device consumed, which is the clock
/// the drawn playhead follows, and it is re-anchored whenever the speed or the transport changes so
/// the frames buffered before the change do not count against the new one.
class Output {
public:
    Output(int sample_rate, int block_frames);
    ~Output();

    Output(const Output&) = delete;
    Output& operator=(const Output&) = delete;

    /// Opens the device and starts the producer; `null_backend` keeps the suite device-free.
    bool open(bool null_backend);
    void close();
    bool is_open() const { return open_.load(); }
    std::string error() const;

    /// Copies what the ring holds into `out`, pads the rest with silence and counts the frame as
    /// played; this is the whole device callback body.
    void render(float* out, size_t frames);

    void load(const float* samples, int64_t count);
    void unload();
    bool loaded() const;
    double duration() const;
    double position() const;
    bool playing() const;
    bool finished() const;

    void play(double seconds);
    void pause();
    void seek(double seconds);
    void set_speed(double speed);
    double speed() const;
    void set_gain(double gain);
    double gain() const;

    /// Hands a rendered buffer to the engine, to be mixed from `start_seconds` on the source
    /// timeline; a buffer replaces any other with the same `id`.
    void submit_buffer(const float* data, int64_t count, double start_seconds, double gain, int id);
    void clear_buffers();

    int sample_rate() const { return sample_rate_; }
    int block_frames() const { return block_frames_; }

private:
    struct Device;

    void producer_loop();
    void reanchor();
    void flush();
    double position_locked() const;

    const int sample_rate_;
    const int block_frames_;
    Engine engine_;
    FrameRing ring_;
    mutable std::mutex mutex_;
    std::condition_variable wake_;
    std::thread producer_;
    std::unique_ptr<Device> device_;

    std::atomic<bool> quit_{false};
    std::atomic<bool> open_{false};
    std::atomic<bool> playing_{false};
    std::atomic<bool> finished_{false};
    std::atomic<uint64_t> played_frames_{0};
    std::atomic<int64_t> last_callback_ns_{0};

    double anchor_position_ = 0.0;
    uint64_t anchor_played_ = 0;
    std::vector<float> scratch_;
    std::string error_;
};

}  // namespace namioto::audio
