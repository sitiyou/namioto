# SPDX-License-Identifier: AGPL-3.0-only
"""Combine a Namioto lyric hook and MIDI into a scrolling pitch-lane hook."""

import argparse
import colorsys
import math
from pathlib import Path
from struct import pack

from namioto import midi
from namioto.project import Note

MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)

# Krumhansl-Schmuckler pitch-class profiles.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def _correlate(histogram: list[float], profile: list[float]) -> float:
    mean_h = sum(histogram) / 12
    mean_p = sum(profile) / 12
    num = sum((h - mean_h) * (p - mean_p) for h, p in zip(histogram, profile, strict=True))
    dev_h = sum((h - mean_h) ** 2 for h in histogram) ** 0.5
    dev_p = sum((p - mean_p) ** 2 for p in profile) ** 0.5
    if dev_h == 0 or dev_p == 0:
        return 0.0
    return num / (dev_h * dev_p)


def detect_key(notes: list[Note]) -> tuple[int, tuple[int, ...]]:
    """Estimate the tonic and scale from duration-weighted pitch classes."""
    histogram = [0.0] * 12
    for note in notes:
        histogram[note.pitch % 12] += note.duration

    best_tonic, best_steps = 0, MAJOR_STEPS
    best_score = float("-inf")
    for tonic in range(12):
        for steps, profile in ((MAJOR_STEPS, MAJOR_PROFILE), (MINOR_STEPS, MINOR_PROFILE)):
            rotated = [profile[(pc - tonic) % 12] for pc in range(12)]
            score = _correlate(histogram, rotated)
            if score > best_score:
                best_score, best_tonic, best_steps = score, tonic, steps
    return best_tonic, best_steps


def note_palette(tonic: int, steps: tuple[int, ...]) -> list[str]:
    """Color scale degrees evenly and interpolate chromatic pitches."""
    palette: list[str] = []
    for pc in range(12):
        rel = (pc - tonic) % 12
        position = 0.0
        for i in range(7):
            start = steps[i]
            end = steps[i + 1] if i < 6 else 12
            if start <= rel < end:
                position = i + (rel - start) / (end - start)
                break
        r, g, b = colorsys.hsv_to_rgb((position / 7.0) % 1.0, 0.9, 1.0)
        palette.append(f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}")
    return palette


def build_hook(lyrics: str, notes: list[Note], fps: int, offset_ms: int) -> str:
    if not notes:
        raise ValueError("The MIDI file contains no notes")
    notes = sorted(notes, key=lambda n: n.start)
    highest = max(n.pitch for n in notes)
    lowest = min(n.pitch for n in notes)
    palette = note_palette(*detect_key(notes))
    note_height = 432 * 0.92 / (1 + (highest - lowest) * 0.75)
    lane_step = note_height * 0.75
    sprites = []
    for note in notes:
        raw_start = (round(note.start * 1000) + offset_ms) / 1000
        raw_end = (round((note.start + note.duration) * 1000) + offset_ms) / 1000
        display_start = min(raw_start + 0.015, (raw_start + raw_end) / 2)
        display_end = max(raw_end - 0.015, (raw_start + raw_end) / 2)
        note_width = max(2, round((display_end - display_start) * 500))
        enter = display_start - (1920 - 672) / 500
        leave = display_end + 672 / 500
        center = 432 * 0.04 + note_height / 2 + (highest - note.pitch) * lane_step
        top = round(center - note_height / 2)
        h = max(1, round(note_height))
        r = max(1, min(round(min(note_width, h) * 0.48), note_width // 2, h // 2))
        rgb = tuple(c / 255.0 for c in bytes.fromhex(palette[note.pitch % 12].lstrip("#")))
        sprites.append((enter, leave, display_start, top, note_width, h, r, *rgb, raw_start, raw_end))

    sprites.sort(key=lambda sprite: sprite[0])
    frame_data = []
    first = 0
    stop = 0
    tail = max(note[1] for note in sprites) + 1 / fps
    frame_count = max(1, math.ceil(tail * fps))
    for frame in range(frame_count):
        t = frame / fps
        while first < len(sprites) and sprites[first][1] <= t:
            first += 1
        stop = max(first, stop)
        while stop < len(sprites) and sprites[stop][0] <= t:
            stop += 1
        hit = any(note[-2] <= t < note[-1] for note in sprites[first:stop])
        frame_data.extend((first, stop, int(hit), int(t < tail)))

    note_data = []
    for enter, leave, display_start, top, w, h, radius, red, green, blue, _, _ in sprites:
        note_data.extend((enter, leave, display_start, 0, top, w, h, radius, red, green, blue, 0))

    data = frame_data + note_data
    texture_width = 512
    data.extend([0] * (-len(data) % (texture_width * 4)))
    texture_height = len(data) // (texture_width * 4)
    header = f"""//!TEXTURE MIDIDATA
//!SIZE {texture_width} {texture_height}
//!FORMAT rgba32f
//!FILTER NEAREST
{pack(f"<{len(data)}f", *data).hex()}

    """
    shade = (
        f"#define MIDI_FRAME_COUNT {frame_count}\n#define MIDI_FPS {fps}.0\n"
        + """vec4 midi_read(int i) {
        return texelFetch(MIDIDATA, ivec2(i % 512, i / 512), 0);
    }
    float midi_round_box(vec2 pixel, vec2 origin, vec2 size, float radius) {
        vec2 local = abs(pixel - origin - size * 0.5) - (size * 0.5 - vec2(radius));
        return length(max(local, vec2(0.0))) + min(max(local.x, local.y), 0.0) - radius;
    }
    float midi_aa(float dist) {
        return 1.0 - smoothstep(-0.55, 0.55, dist);
    }
    vec4 midi_layer(vec4 canvas) {
        vec2 p = HOOKED_pos * vec2(1920.0, 1080.0);
        const float judge = 672.0;
        int idx = frame - 1;
        if (idx < 0 || idx >= MIDI_FRAME_COUNT || p.y >= 432.0) return canvas;
        vec4 flags = midi_read(idx);
        if (flags.w == 0.0) return canvas;

        canvas.rgb = mix(canvas.rgb, vec3(122.0 / 255.0), 0.35);
        float t = float(idx) / MIDI_FPS;
        for (int layer = 0; layer < (p.x >= judge ? 2 : 1); ++layer) {
            for (int i = int(flags.x); i < int(flags.y); ++i) {
                int addr = MIDI_FRAME_COUNT + i * 3;
                vec4 timing = midi_read(addr);
                if (t < timing.x || t >= timing.y) continue;
                vec4 geom = midi_read(addr+1);
                if (p.y < geom.x-4.0 || p.y > geom.x+geom.z+7.0) continue;
                vec2 origin = vec2(judge + (timing.z - t) * 500.0, geom.x);
                if (p.x < origin.x-4.0 || p.x > origin.x+geom.y+7.0) continue;
                vec3 fill = (layer == 0) ? vec3(120.0 / 255.0) : midi_read(addr+2).rgb;
                vec3 edge = mix(fill, vec3(1.0), 0.8);
                float distance = midi_round_box(p, origin, geom.yz, geom.w);
                float shadow = midi_round_box(p, origin+vec2(2.0), geom.yz, geom.w);
                canvas.rgb = mix(canvas.rgb, vec3(16.0/255.0), midi_aa(shadow-3.0)/3.0);
                float inside = midi_aa(distance);
                float coverage = 0.7 * midi_aa(distance - 3.0);
                canvas.rgb = mix(canvas.rgb, mix(edge, fill, inside), coverage);
            }
        }

        if (p.x >= judge-2.0 && p.x < judge+2.0) {
            canvas.rgb = flags.z > 0.0 ? vec3(1.0, 210.0/255.0, 0.0) : vec3(84.0/255.0);
        }
        return canvas;
    }
    """
    )
    anchor = "//!BIND KARADATA\n"
    entry = "vec4 hook() {\n    vec4 bg=HOOKED_tex(HOOKED_pos);"
    if lyrics.count("//!HOOK MAIN") != 1 or lyrics.count(anchor) != 1 or lyrics.count(entry) != 1:
        raise RuntimeError("The Namioto hook layout changed. Use a supported single-pass Namioto lyric hook.")
    lyrics = lyrics.replace(anchor, anchor + "//!BIND MIDIDATA\n")
    lyrics = lyrics.replace(entry, "vec4 lyric_layer(vec4 bg) {")
    return (
        header.rstrip()
        + "\n\n"
        + lyrics
        + "\n"
        + shade
        + """
    vec4 hook() {
        return midi_layer(lyric_layer(HOOKED_tex(HOOKED_pos)));
    }
    """
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("midi", type=Path)
    parser.add_argument("lyrics", type=Path, help="Namioto single-pass lyric hook")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fps", type=int, required=True, help="Same integer frame rate as the lyric hook and renderer")
    parser.add_argument("--wavetone", action="store_true", help="Remove WaveTone’s four-beat MIDI lead-in")
    parser.add_argument("--offset-ms", type=int, default=0, help="Pitch display offset; positive delays the notes")
    args = parser.parse_args()
    if args.fps < 1 or args.out.resolve() in (args.lyrics.resolve(), args.midi.resolve()):
        parser.error("Use a positive frame rate and a separate output file")
    try:
        notes = midi.read(args.midi, wavetone=args.wavetone).notes
        hook = build_hook(args.lyrics.read_text(encoding="utf-8"), list(notes), args.fps, args.offset_ms)
        args.out.write_text(hook, encoding="utf-8")
    except (OSError, EOFError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
