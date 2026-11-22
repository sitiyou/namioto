"""Write an importable timing handoff from one project.export_source JSON response."""

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path

from namioto.karaoke.export_settings import ExportSettings
from namioto.subtitle import build_song


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new handoff directory")
    parser.add_argument("--offset-ms", type=int, required=True, help="signed delivery offset")
    args = parser.parse_args()
    dest = args.destination
    if dest.exists():
        parser.error(f"{dest} already exists")
    snapshot = json.load(sys.stdin)
    krc = snapshot["krc"]
    midi = base64.b64decode(snapshot["midi"], validate=True)
    wavetone = snapshot["wavetone"]
    settings = snapshot["settings"]
    roles = ExportSettings(track_style=settings.get("track_style", {}),
                           harmony_tracks=settings.get("harmony_tracks", "")).vocal_roles()
    colors = dict(enumerate(snapshot.get("project_colors", [])))
    song = build_song(krc, midi, wavetone=wavetone, semantic=True,
                      settings=ExportSettings(offset_ms=args.offset_ms, track_style=roles, channel_colors=colors))
    source = (Path(__file__).resolve().parents[1] / "assets/song_input.py").read_text()
    facts = {"OFFSET_MS": args.offset_ms, "WAVETONE": wavetone, "TRACK_STYLE": roles, "CHANNEL_COLORS": colors}
    lines = []
    for line in source.splitlines():
        name = line.partition(" = ")[0]
        lines.append(f"{name} = {facts[name]!r}" if name in facts else line)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        bundle = Path(tmp) / "input"
        bundle.mkdir()
        (bundle / "lyrics.krc").write_text(krc, encoding="utf-8")
        (bundle / "vocal.mid").write_bytes(midi)
        (bundle / "song_input.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
        bundle.rename(dest)
    print(f"{dest}: {len(song.sources)} lines at {song.bpm:g} BPM")


if __name__ == "__main__":
    main()
