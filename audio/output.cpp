// SPDX-License-Identifier: AGPL-3.0-only
#include "output.h"

#include <algorithm>
#include <chrono>

#include "miniaudio.h"

namespace namioto::audio {

namespace {

int64_t steady_now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

/// The device callback: copy from the ring, pad what is missing with silence, count device frames.
void data_callback(ma_device* device, void* output, const void*, ma_uint32 frame_count) {
    static_cast<Output*>(device->pUserData)->render(static_cast<float*>(output), frame_count);
}

size_t ring_capacity_for(int block_frames) {
    size_t capacity = 1;
    const size_t target = static_cast<size_t>(block_frames) * 4;
    while (capacity < target) {
        capacity <<= 1;
    }
    return capacity;
}

}  // namespace

struct Output::Device {
    ma_device device{};
    bool initialized = false;
};

Output::Output(int sample_rate, int block_frames)
    : sample_rate_(sample_rate),
      block_frames_(std::max(1, block_frames)),
      engine_(sample_rate),
      ring_(ring_capacity_for(std::max(1, block_frames))) {}

Output::~Output() {
    close();
}

bool Output::open(bool null_backend) {
    std::lock_guard lock(mutex_);
    if (open_.load()) {
        return true;
    }

    device_ = std::make_unique<Device>();
    ma_device_config config = ma_device_config_init(ma_device_type_playback);
    config.playback.format = ma_format_f32;
    config.playback.channels = 1;
    config.sampleRate = static_cast<ma_uint32>(sample_rate_);
    config.periodSizeInFrames = static_cast<ma_uint32>(block_frames_);
    config.periods = 3;
    config.dataCallback = &data_callback;
    config.pUserData = this;

    ma_result result;
    if (null_backend) {
        const ma_backend backends[] = {ma_backend_null};
        result = ma_device_init_ex(backends, 1, nullptr, &config, &device_->device);
    } else {
        result = ma_device_init(nullptr, &config, &device_->device);
    }
    if (result != MA_SUCCESS) {
        error_ = ma_result_description(result);
        device_.reset();
        return false;
    }
    device_->initialized = true;

    result = ma_device_start(&device_->device);
    if (result != MA_SUCCESS) {
        error_ = ma_result_description(result);
        ma_device_uninit(&device_->device);
        device_.reset();
        return false;
    }

    quit_ = false;
    open_ = true;
    producer_ = std::thread(&Output::producer_loop, this);
    return true;
}

void Output::close() {
    {
        std::lock_guard lock(mutex_);
        if (!open_.load()) {
            return;
        }
        quit_ = true;
    }
    wake_.notify_all();
    if (producer_.joinable()) {
        producer_.join();
    }

    std::lock_guard lock(mutex_);
    playing_ = false;
    if (device_) {
        if (device_->initialized) {
            ma_device_stop(&device_->device);
            ma_device_uninit(&device_->device);
        }
        device_.reset();
    }
    open_ = false;
}

std::string Output::error() const {
    std::lock_guard lock(mutex_);
    return error_;
}

void Output::load(const float* samples, int64_t count) {
    std::lock_guard lock(mutex_);
    engine_.load(samples, count);
    anchor_position_ = 0.0;
    anchor_played_ = played_frames_.load(std::memory_order_relaxed);
    playing_ = false;
    finished_ = false;
    ring_.flush();
}

void Output::unload() {
    std::lock_guard lock(mutex_);
    engine_.unload();
    anchor_position_ = 0.0;
    anchor_played_ = played_frames_.load(std::memory_order_relaxed);
    playing_ = false;
    finished_ = false;
    ring_.flush();
}

bool Output::loaded() const {
    std::lock_guard lock(mutex_);
    return engine_.loaded();
}

double Output::duration() const {
    std::lock_guard lock(mutex_);
    return engine_.duration();
}

double Output::position_locked() const {
    const uint64_t played = played_frames_.load(std::memory_order_relaxed);
    double frames = double(played - anchor_played_);
    if (playing_.load(std::memory_order_relaxed)) {
        // the device clock is stepped, so wall time since its last tick fills the gap; it is capped
        // in case the callbacks stall, and the next tick re-anchors it
        const int64_t now = steady_now_ns();
        const int64_t last = last_callback_ns_.load(std::memory_order_relaxed);
        if (last > 0 && now > last) {
            const double extrapolated = double(now - last) * 1e-9 * sample_rate_;
            frames += std::min(extrapolated, double(sample_rate_) * 0.05);
        }
    }
    const double elapsed = frames / sample_rate_ * engine_.speed();
    const double position = anchor_position_ + elapsed;
    if (engine_.loaded()) {
        return std::clamp(position, 0.0, engine_.duration());
    }
    // without a song the note timeline is the whole timeline, so it is not held to a source length
    return std::max(0.0, position);
}

double Output::position() const {
    std::lock_guard lock(mutex_);
    return position_locked();
}

bool Output::playing() const {
    return playing_.load() && !finished();
}

bool Output::finished() const {
    // the source ending is not the song ending: the frames already in the ring still have to be
    // heard, so this is judged on the played position rather than on the producer's flag alone
    std::lock_guard lock(mutex_);
    return finished_.load() && position_locked() >= engine_.duration() - 1e-3;
}

void Output::reanchor() {
    anchor_position_ = position_locked();
    anchor_played_ = played_frames_.load(std::memory_order_relaxed);
}

void Output::flush() {
    ring_.flush();
}

void Output::play(double seconds) {
    std::lock_guard lock(mutex_);
    const double target = engine_.loaded() ? std::clamp(seconds, 0.0, engine_.duration()) : std::max(0.0, seconds);
    engine_.play(seconds);
    anchor_position_ = target;
    anchor_played_ = played_frames_.load(std::memory_order_relaxed);
    ring_.flush();
    playing_ = true;
    finished_ = false;
    wake_.notify_all();
}

void Output::pause() {
    std::lock_guard lock(mutex_);
    reanchor();
    engine_.pause();
    playing_ = false;
    ring_.flush();
}

void Output::seek(double seconds) {
    std::lock_guard lock(mutex_);
    const double target = engine_.loaded() ? std::clamp(seconds, 0.0, engine_.duration()) : std::max(0.0, seconds);
    engine_.seek(seconds);
    anchor_position_ = target;
    anchor_played_ = played_frames_.load(std::memory_order_relaxed);
    ring_.flush();
    finished_ = false;
    wake_.notify_all();
}

void Output::set_speed(double speed) {
    std::lock_guard lock(mutex_);
    if (speed == engine_.speed()) {
        return;
    }
    // the stretcher takes the new rate from the next block on, so nothing is reset: re-seeking would
    // replay its pre-roll and dropping the buffered frames would skip the source already read
    anchor_position_ = position_locked();
    anchor_played_ = played_frames_.load(std::memory_order_relaxed);
    engine_.set_speed(speed);
    wake_.notify_all();
}

double Output::speed() const {
    std::lock_guard lock(mutex_);
    return engine_.speed();
}

void Output::set_gain(double gain) {
    std::lock_guard lock(mutex_);
    engine_.set_gain(gain);
}

double Output::gain() const {
    std::lock_guard lock(mutex_);
    return engine_.gain();
}

void Output::submit_buffer(const float* data, int64_t count, double start_seconds, double gain, int id) {
    std::lock_guard lock(mutex_);
    engine_.submit_buffer(data, count, start_seconds, gain, id);
}

void Output::clear_buffers() {
    std::lock_guard lock(mutex_);
    engine_.clear_buffers();
}

void Output::render(float* out, size_t frames) {
    size_t done = 0;
    while (done < frames) {
        const size_t got = ring_.read(out + done, frames - done);
        if (got == 0) {
            break;
        }
        done += got;
    }
    std::fill(out + done, out + frames, 0.0f);
    last_callback_ns_.store(steady_now_ns(), std::memory_order_relaxed);
    if (playing_.load(std::memory_order_relaxed)) {
        played_frames_.fetch_add(frames, std::memory_order_relaxed);
    }
}

void Output::producer_loop() {
    while (true) {
        std::unique_lock lock(mutex_);
        wake_.wait_for(lock, std::chrono::milliseconds(20), [this] {
            return quit_.load() || (playing_.load() && !finished_.load() && ring_.space() > 0);
        });
        if (quit_.load()) {
            break;
        }
        if (!playing_.load() || finished_.load()) {
            continue;
        }
        const size_t space = ring_.space();
        if (space == 0) {
            continue;
        }
        const int block = static_cast<int>(std::min<size_t>(block_frames_, space));
        scratch_.resize(block);
        engine_.pull(scratch_.data(), block);
        if (engine_.finished()) {
            finished_ = true;
        }
        lock.unlock();
        ring_.write(scratch_.data(), block);
    }
}

}  // namespace namioto::audio
