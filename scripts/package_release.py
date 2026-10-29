"""Package the frozen GUI as a Linux AppImage or Windows portable ZIP."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appimagetool", type=Path)
    args = parser.parse_args()
    source = ROOT / "dist" / "namioto"
    output = ROOT / "dist" / "release"
    output.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        if not (source / "namioto.exe").is_file():
            parser.error("Build the frozen application first: bash build.sh app")
        shutil.make_archive(str(output / "namioto-windows-x86_64"), "zip", source.parent, source.name)
    elif sys.platform == "linux":
        if not (source / "namioto").is_file():
            parser.error("Build the frozen application first: bash build.sh app")
        if args.appimagetool is None:
            parser.error("--appimagetool is required on Linux")
        appdir = ROOT / "build" / "Namioto.AppDir"
        if appdir.exists():
            shutil.rmtree(appdir)
        shutil.copytree(ROOT / "scripts" / "appimage", appdir)
        shutil.copytree(source, appdir / "namioto")
        (appdir / "AppRun").chmod(0o755)
        subprocess.run(
            [
                str(args.appimagetool.resolve()),
                "--appimage-extract-and-run",
                "--no-appstream",
                str(appdir),
                str(output / "namioto-linux-x86_64.AppImage"),
            ],
            env={**os.environ, "ARCH": "x86_64"},
            check=True,
        )
    else:
        parser.error(f"Unsupported platform: {sys.platform}")


if __name__ == "__main__":
    main()
