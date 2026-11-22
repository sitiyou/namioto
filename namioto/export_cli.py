# SPDX-License-Identifier: AGPL-3.0-only
"""Export karaoke from files or an IPC export snapshot, optionally rebuilding after changes.

Session watching polls the connected editor's export revision; it never discovers a new endpoint
or reacts to playback position. File watching follows paths across atomic replacements.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path

from namioto import ipc, subtitle
from namioto import settings as store
from namioto.karaoke import ExportSettings
from namioto.karaoke.templates import load_templates, parameter_value, read_preferences
from namioto.utils import file_stamp

POLL_SECONDS = 0.1
DEBOUNCE_SECONDS = 0.3


def resolve_settings(arguments: argparse.Namespace, session: Mapping | None = None) -> ExportSettings:
    settings = ExportSettings(**session) if session is not None else ExportSettings()
    overrides = {}
    for field in subtitle.ASS_FIELDS:
        value = getattr(arguments, field.name, None)
        if value is None:
            value = os.environ.get(f"NAMIOTO_ASS_{field.name.upper()}")
            if value is not None:
                convert = partial(parameter_value, field)
                try:
                    value = convert(value)
                except ValueError as error:
                    raise ValueError(f"NAMIOTO_ASS_{field.name.upper()} must be {field.kind}") from error
        if value is not None:
            overrides[field.name] = value
    settings = replace(settings, **overrides)
    subtitle.validate_settings(settings)
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
    parser = argparse.ArgumentParser(
        prog="namioto-cli export", description="Export karaoke lyrics as ASS or a libplacebo hook"
    )
    parser.add_argument("--session", action="store_true", help="use the running editor's KRC and MIDI exports")
    parser.add_argument("--midi", type=Path, help="input MIDI file")
    parser.add_argument("--krc", type=Path, help="input KRC file")
    parser.add_argument("--out", type=Path, help="output .ass or .hook file")
    parser.add_argument("--format", choices=("ass", "hook"), help="default: infer from output suffix")
    parser.add_argument("--template", help="template ID (default: editor selection or default)")
    parser.add_argument("--script", type=Path, action="append", default=[], help="load a trusted Python template file")
    parser.add_argument("--set", dest="parameters", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--fps", type=int, help="hook frame rate (1..240)")
    parser.add_argument("--list-templates", action="store_true")
    parser.add_argument("--template-help", action="store_true")
    parser.add_argument("--watch", action="store_true", help="rebuild after changes; Ctrl+C stops watching")
    parser.add_argument("--save-krc", type=Path, help="persist the session's intermediate KRC export")
    parser.add_argument("--save-midi", type=Path, help="persist the session's intermediate MIDI export")
    parser.add_argument(
        "--wavetone",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="read file input with WaveTone's leading bar removed (default: enabled)",
    )
    parser.add_argument("--instance", help="editor instance ID (default: the first running editor)")
    parser.add_argument("-t", "--timeout", type=float, default=ipc.CONNECT_TIMEOUT, help="IPC timeout in seconds")
    for field in subtitle.ASS_FIELDS:
        convert = partial(parameter_value, field)
        parser.add_argument(
            "--" + field.name.replace("_", "-"),
            type=convert,
            help=f"{field.caption}; overrides $NAMIOTO_ASS_{field.name.upper()}",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_templates or args.template_help:
        try:
            registry = load_templates(args.script)
            if args.list_templates:
                for template in registry.templates.values():
                    print(template.id, template.title, ", ".join(template.formats))
            else:
                for field in registry.get(args.template or "default").parameters:
                    print(
                        f"{field.name} ({field.kind}, default={field.default!r})  {field.caption} {field.choices or ''}"
                    )
            return 0
        except ValueError as error:
            parser.error(str(error))
    if not args.out:
        parser.error("--out is required")
    format = args.format or ("hook" if args.out.suffix.lower() == ".hook" else "ass")
    parameters = {}
    for item in args.parameters:
        key, sep, value = item.partition("=")
        if not key or not sep:
            parser.error("--set expects KEY=VALUE")
        parameters[key] = value

    def selected_scripts(project, template):
        if args.script or not project or template == "default":
            return args.script
        saved = read_preferences(Path(project))
        if saved.get("id", "default") != "default" and template in (None, saved["id"]):
            return [Path(path) for path in saved["scripts"]]
        return []

    def export(krc, data, settings, wavetone, session=None):
        session = session or {}
        template = args.template or session.get("template", "default")
        scripts = selected_scripts(session.get("project"), template)
        inherited = session.get("parameters", {}) if template == session.get("template") else {}
        return subtitle.export_song(
            target,
            krc,
            data,
            settings=settings,
            project_colors=dict(enumerate(session.get("project_colors", []))),
            wavetone=wavetone,
            format=format,
            template=template,
            scripts=scripts,
            parameters={**inherited, **parameters},
            fps=args.fps if args.fps is not None else session.get("fps", 60),
            save_krc=args.save_krc,
            save_midi=args.save_midi,
        )

    def script_stamps(scripts):
        return tuple((path, file_stamp(path)) for path in scripts)

    if args.session:
        if args.midi or args.krc or args.wavetone is not None:
            parser.error("--session cannot be combined with --midi, --krc or --[no-]wavetone")
    else:
        if args.midi is None or args.krc is None:
            parser.error("provide both --midi and --krc, or use --session")
        if args.save_krc or args.save_midi or args.instance:
            parser.error("session connection and intermediate export options require --session")
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be a finite positive number")
    try:
        target = subtitle.export_path(args.out, format)
        outputs = [target, *([args.save_krc] if args.save_krc else []), *([args.save_midi] if args.save_midi else [])]
        subtitle.check_paths(outputs, args.script + ([] if args.session else [args.krc, args.midi]))
        if args.session:
            endpoint = ipc.resolve_endpoint(args.instance)
            with ipc.Client(endpoint, timeout=args.timeout, keep_events=False) as client:

                def build():
                    source = client.request("project.export_source")
                    settings = resolve_settings(args, source["settings"])
                    data = base64.b64decode(source["midi"], validate=True)
                    return export(source["krc"], data, settings, source["wavetone"], source)

                if args.watch:

                    def session_stamp():
                        state = client.request("state.get")
                        project = state.get("project", {}).get("path")
                        return state["subtitle"]["revision"], script_stamps(selected_scripts(project, args.template))

                    watch(build, session_stamp)
                else:
                    return 0 if _attempt(build) else 1
        else:
            settings = resolve_settings(args)
            wavetone = store.Settings().midi.wavetone if args.wavetone is None else args.wavetone

            def build():
                krc = args.krc.read_text(encoding="utf-8")
                data = args.midi.read_bytes()
                return export(krc, data, settings, wavetone)

            if args.watch:
                watch(build, lambda: (file_stamp(args.krc), file_stamp(args.midi), script_stamps(args.script)))
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
