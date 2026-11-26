# SPDX-License-Identifier: AGPL-3.0-only
"""Export plain ASS from a project, matched files, or the running editor.

Session watching follows the connected editor's revision. File watching follows
paths across atomic replacements.
"""

import argparse
import base64
import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from namioto import ipc, subtitle
from namioto import settings as store
from namioto.karaoke.export_settings import ExportSettings, validate_settings
from namioto.utils import file_stamp

POLL_SECONDS = 0.1
DEBOUNCE_SECONDS = 0.3


def resolve_settings(arguments: argparse.Namespace, session: Mapping | None = None) -> ExportSettings:
    offset = arguments.offset_ms
    if offset is None:
        environment = os.environ.get("NAMIOTO_ASS_OFFSET_MS")
        if environment is not None:
            try:
                offset = int(environment)
            except ValueError as error:
                raise ValueError("NAMIOTO_ASS_OFFSET_MS must be an integer") from error
        else:
            offset = (session or {}).get("offset_ms", 0)
    settings = ExportSettings(offset_ms=offset)
    validate_settings(settings)
    return settings


def _attempt(build: Callable[[], Path]) -> bool:
    try:
        target = build()
    except ipc.IpcError as error:
        if error.code != "failed":
            raise
        print(f"namioto-cli export: {error.message}", file=sys.stderr, flush=True)
        return False
    except (OSError, ValueError, EOFError) as error:
        print(f"namioto-cli export: {error}", file=sys.stderr, flush=True)
        return False
    print(f"Exported {target}", flush=True)
    return True


def watch(build: Callable[[], Path], changed: Callable[[], object]) -> None:
    previous = changed()
    _attempt(build)
    due = None
    while True:
        time.sleep(POLL_SECONDS)
        current = changed()
        now = time.monotonic()
        if current != previous:
            previous = current
            due = now + DEBOUNCE_SECONDS
        if due is not None and now >= due:
            _attempt(build)
            due = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="namioto-cli export", description="Export line-timed ASS subtitles")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--session", action="store_true", help="read the running editor's lyric mapping")
    source.add_argument("--project", type=Path, help="input .nto project")
    source.add_argument("--midi", type=Path, help="input MIDI file (with --krc)")
    parser.add_argument("--krc", type=Path, help="input KRC file (with --midi)")
    parser.add_argument("--out", type=Path, required=True, help="output .ass file")
    parser.add_argument("--format", choices=("ass",), help=argparse.SUPPRESS)
    parser.add_argument("--offset-ms", type=int, help="signed subtitle offset; overrides NAMIOTO_ASS_OFFSET_MS")
    parser.add_argument("--watch", action="store_true", help="rebuild after changes; Ctrl+C stops watching")
    parser.add_argument("--save-krc", type=Path, help="persist the session's KRC export")
    parser.add_argument("--save-midi", type=Path, help="persist the session's MIDI export")
    parser.add_argument(
        "--wavetone",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="read file input with WaveTone's leading bar removed (default: enabled)",
    )
    parser.add_argument("--instance", help="editor instance ID (default: the first running editor)")
    parser.add_argument("--timeout", type=float, default=5, help="IPC request timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.session or args.project:
        if args.krc or args.wavetone is not None:
            parser.error("--krc and --[no-]wavetone require MIDI input")
    elif args.midi is None or args.krc is None:
        parser.error("provide --project, --session, or both --midi and --krc")
    if not args.session and (args.save_krc or args.save_midi or args.instance):
        parser.error("session connection and intermediate export options require --session")
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be a finite positive number")
    try:
        target = subtitle.export_path(args.out)
        if args.session:
            endpoint = ipc.resolve_endpoint(args.instance)
            with ipc.Client(endpoint, timeout=args.timeout, keep_events=False) as client:

                def build():
                    source = client.request("project.export_source")
                    return subtitle.export_song(
                        target,
                        source["krc"],
                        base64.b64decode(source["midi"], validate=True),
                        settings=resolve_settings(args, source["settings"]),
                        wavetone=source["wavetone"],
                        save_krc=args.save_krc,
                        save_midi=args.save_midi,
                    )

                if args.watch:
                    watch(build, lambda: client.request("state.get")["subtitle"]["revision"])
                else:
                    return 0 if _attempt(build) else 1
        else:
            settings = resolve_settings(args)
            if args.project:

                def build():
                    return subtitle.export_project(target, args.project, settings=settings)

                inputs = (args.project,)
            else:
                wavetone = store.Settings().midi.wavetone if args.wavetone is None else args.wavetone

                def build():
                    return subtitle.export_song(
                        target,
                        args.krc.read_text(encoding="utf-8"),
                        args.midi.read_bytes(),
                        settings=settings,
                        wavetone=wavetone,
                    )

                inputs = (args.krc, args.midi)
            subtitle.check_paths([target], inputs)
            if args.watch:
                watch(build, lambda: tuple(file_stamp(path) for path in inputs))
            else:
                return 0 if _attempt(build) else 1
    except KeyboardInterrupt:
        return 0
    except (ipc.IpcError, OSError, ValueError) as error:
        print(f"namioto-cli export: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
