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
  cli [tempo|wavetone|tempocnn|spectrum|ctl]
                   frozen CLI via PyInstaller (default: tempo)
                                                        -> dist/namioto-tempo/
                                                           dist/namioto-wavetone/
                                                           dist/namioto-tempocnn/
                                                           dist/namioto-spectrum/
                                                           dist/namioto-ctl/
  app              frozen GUI via PyInstaller           -> dist/namioto/
  all              wheel + both CLIs + app
  clean            remove build/ and dist/
EOF
}

pyinstaller_common=(
    --noconfirm
    --clean
    --paths .
    --specpath build
)

build_wheel() {
    uv build
}

build_cli() {
    case "${1:-tempo}" in
        tempo) freeze_cli namioto/analysis/beats.py namioto-tempo ;;
        wavetone) freeze_cli namioto/analysis/wavetone.py namioto-wavetone ;;
        tempocnn) freeze_cli namioto/analysis/tempo.py namioto-tempocnn cpu ;;
        spectrum) freeze_cli namioto/analysis/spectrum.py namioto-spectrum ;;
        ctl) freeze_cli namioto/ipc.py namioto-ctl ;;
        *)
            echo "Unknown CLI: ${1}. Expected tempo, wavetone, tempocnn, spectrum or ctl." >&2
            exit 1
            ;;
    esac
}

freeze_cli() {
    if [ -n "${3:-}" ]; then
        uv sync --extra "$3"
    fi
    uv run --with pyinstaller pyinstaller "${pyinstaller_common[@]}" \
        --name "$2" \
        --onedir \
        "$1"
    copy_licenses "dist/$2"
}

build_app() {
    # a frozen app needs the compiled extension inside the package, so the project is installed as
    # a wheel rather than in the editable layout, which keeps it under build/
    uv sync --extra cpu --no-editable --reinstall-package namioto
    uv run --no-editable --with pyinstaller pyinstaller "${pyinstaller_common[@]}" \
        --name namioto \
        --onedir \
        --windowed \
        --collect-data qtawesome \
        namioto/ui/app.py
    copy_licenses dist/namioto
}

copy_licenses() {
    cp LICENSE NOTICE "$1"
}

case "${1:-}" in
    wheel) build_wheel ;;
    cli) build_cli "${2:-tempo}" ;;
    app) build_app ;;
    all)
        build_wheel
        build_cli tempo
        build_cli spectrum
        build_app
        ;;
    clean) rm -rf build dist ;;
    *)
        usage
        exit 1
        ;;
esac
