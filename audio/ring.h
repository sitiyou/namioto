// SPDX-License-Identifier: AGPL-3.0-only
#pragma once

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace namioto::audio {

/// Single-producer/single-consumer ring of mono float frames, indexed by monotonic counters.
///
/// The producer writes, the audio callback reads, and neither blocks the other. `flush` lets the
/// producer discard everything unplayed so a seek does not replay the buffer behind it; the
/// consumer notices the drop marker and skips to it, which is the only shared value either side
/// writes the other's field for.
class FrameRing {
public:
    explicit FrameRing(size_t capacity)
        : capacity_(capacity), mask_(capacity - 1), buffer_(capacity) {}

    size_t capacity() const { return capacity_; }

    size_t available() const {
        const uint64_t written = write_.load(std::memory_order_acquire);
        const uint64_t read = read_.load(std::memory_order_acquire);
        return static_cast<size_t>(written - read);
    }

    size_t space() const { return capacity_ - available(); }

    /// Producer: appends up to `count` frames, returning how many were written.
    size_t write(const float* frames, size_t count) {
        const uint64_t written = write_.load(std::memory_order_relaxed);
        const uint64_t read = read_.load(std::memory_order_acquire);
        const size_t free = capacity_ - static_cast<size_t>(written - read);
        const size_t count_to_write = std::min(count, free);
        for (size_t i = 0; i < count_to_write; ++i) {
            buffer_[(written + i) & mask_] = frames[i];
        }
        write_.store(written + count_to_write, std::memory_order_release);
        return count_to_write;
    }

    /// Producer: discards everything written so far.
    void flush() { dropped_.store(write_.load(std::memory_order_acquire), std::memory_order_release); }

    /// Consumer: reads up to `count` frames, returning how many were read.
    size_t read(float* frames, size_t count) {
        uint64_t read = read_.load(std::memory_order_relaxed);
        const uint64_t dropped = dropped_.load(std::memory_order_acquire);
        if (read < dropped) {
            read = dropped;
        }
        const uint64_t written = write_.load(std::memory_order_acquire);
        const size_t count_to_read = std::min(count, static_cast<size_t>(written - read));
        for (size_t i = 0; i < count_to_read; ++i) {
            frames[i] = buffer_[(read + i) & mask_];
        }
        read_.store(read + count_to_read, std::memory_order_release);
        return count_to_read;
    }

private:
    const size_t capacity_;
    const size_t mask_;
    std::vector<float> buffer_;
    std::atomic<uint64_t> write_{0};
    std::atomic<uint64_t> read_{0};
    std::atomic<uint64_t> dropped_{0};
};

}  // namespace namioto::audio
