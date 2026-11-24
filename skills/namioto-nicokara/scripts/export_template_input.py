"""Write a verified timing directory from one project.export_source JSON response."""

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path

from namioto.karaoke.export_settings import ExportSettings
from namioto.karaoke.song_input import load_song_input


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new handoff directory")
    parser.add_argument("--offset-ms", type=int, default=0, help="signed delivery offset (default: 0)")
    args = parser.parse_args()
    dest = args.destination
    if dest.exists():
        parser.error(f"{dest} already exists")
    snapshot = json.load(sys.stdin)
    settings = snapshot["settings"]
    roles = ExportSettings(
        track_style=settings.get("track_style", {}),
        harmony_tracks=settings.get("harmony_tracks", ""),
    ).vocal_roles()
    metadata = {
        "wavetone": snapshot["wavetone"],
        "offset_ms": args.offset_ms,
        "track_style": roles,
        "colors": dict(enumerate(snapshot.get("project_colors", []))),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        bundle = Path(tmp) / "input"
        bundle.mkdir()
        (bundle / "lyrics.krc").write_text(snapshot["krc"], encoding="utf-8")
        (bundle / "vocal.mid").write_bytes(base64.b64decode(snapshot["midi"], validate=True))
        (bundle / "song.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        song = load_song_input(bundle)
        bundle.rename(dest)
    print(f"{dest}: {len(song.sources)} lines at {song.bpm:g} BPM")


if __name__ == "__main__":
    main()
