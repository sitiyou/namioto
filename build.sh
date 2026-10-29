#!/usr/bin/env bash
# Build artifacts for Namioto. All output goes to dist/, work files to build/.
#
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

cd "$(dirname "$0")"

usage() {
    cat <<'EOF'
Usage: ./build.sh <target>

  wheel            sdist + wheel via uv build          -> dist/*.whl, dist/*.tar.gz
  cli              frozen task CLI via PyInstaller      -> dist/namioto-cli/
  ctl              frozen IPC client via PyInstaller    -> dist/namioto-ctl/
  app              frozen GUI via PyInstaller           -> dist/namioto/
  all              wheel + cli + ctl + app
  clean            remove build/ and dist/
EOF
}

pyinstaller_common=(
    --noconfirm
    --clean
    --specpath build
    --additional-hooks-dir scripts/pyinstaller-hooks
)

build_wheel() {
    uv build
}

build_cli() {
    uv sync --extra cpu
    freeze namioto-cli namioto.cli \
        --collect-submodules namioto.analysis \
        --hidden-import namioto.ass_cli \
        --collect-all janome
}

freeze() {
    # The entry script lives outside the package: PyInstaller puts the directory it infers from the
    # script ahead of site-packages, so a script inside `namioto/` would be answered by the source
    # tree, which holds no compiled extension.
    local name="$1" module="$2" launcher="build/$1-entry.py"
    shift 2
    mkdir -p build
    printf 'from %s import main\n\nraise SystemExit(main())\n' "$module" > "$launcher"
    uv run --no-editable --with pyinstaller pyinstaller "${pyinstaller_common[@]}" \
        --name "$name" \
        --onedir \
        "$@" \
        "$launcher"
    copy_licenses "dist/$name"
}

build_app() {
    # a frozen app needs the compiled extension inside the package, so the project is installed as
    # a wheel rather than in the editable layout, which keeps it under build/
    uv sync --locked --extra cpu --no-editable --reinstall-package namioto
    freeze namioto namioto.ui.app \
        --windowed \
        --collect-data qtawesome \
        --collect-all janome
}

copy_licenses() {
    cp LICENSE NOTICE "$1"
}

case "${1:-}" in
    wheel) build_wheel ;;
    cli) build_cli ;;
    ctl) freeze namioto-ctl namioto.ipc ;;
    app) build_app ;;
    all)
        build_wheel
        build_cli
        freeze namioto-ctl namioto.ipc
        build_app
        ;;
    clean) rm -rf build dist ;;
    *)
        usage
        exit 1
        ;;
esac
