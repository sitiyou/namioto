// SPDX-License-Identifier: AGPL-3.0-only
#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#include "audio.h"

namespace namioto::audio {

std::string miniaudio_version() {
    return ma_version_string();
}

}  // namespace namioto::audio
