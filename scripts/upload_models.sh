#!/usr/bin/env bash
# Publish the ONNX models the program downloads on demand as the assets of one release, so
# namioto/model_store.py can fetch them. Nothing here touches git: gh creates the tag on the remote.
#
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

cd "$(dirname "$0")/.."

# Defaults for this project's release; every one of them can be overridden on the command line.
REPO="sitiyou/namioto"
TAG="models"
TITLE="ONNX models"
NOTES="ONNX models, downloaded on demand by namioto.

- mms-onnx-int8.zip — Meta MMS forced aligner, Japanese (CC-BY-NC 4.0); conversion by
  scripts/export_align_model.py
- yohane-onnx-int8.zip — NextFire mms-300m forced aligner, Japanese (CC BY-NC-SA 4.0); conversion by
  scripts/export_align_model.py
- mms-onnx-fp16.zip, yohane-onnx-fp16.zip — those two graphs in half precision, which a GPU run
  loads in place of the quantised one
- mms-onnx.zip, yohane-onnx.zip — the int8 packages under the names releases before the rename ask
  for, kept so those installs can still fetch them
- tempocnn-onnx.zip — Essentia/TempoCNN deeptemp-k16-3 (CC BY-NC-SA 4.0)

All of them carry non-commercial licenses: they are redistributed for use with namioto only. See NOTICE."
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/namioto/models"
LANGUAGE="ja"
TEMPOCNN=""
OUT="dist/models"
ONLY="mms yohane tempocnn"
REUSE=0
CLOBBER=0
DRY=0

usage() {
    cat <<'EOF'
Usage: ./scripts/upload_models.sh [options]

Publishes mms-onnx-int8.zip, yohane-onnx-int8.zip and tempocnn-onnx.zip as the assets of one
GitHub release - one zip per ONNX model, with the aligners' half-precision packages beside them when
they have been exported - and prints the registry lines that point the program at them.
`gh` must be logged in with a token that carries the `repo` scope for the repository's owner.

  --tag TAG          the release tag (default: models). A re-export wants a new one: the program
                     caches a model by its path, so replacing an asset under a published tag would
                     change results under it
  --only mms,yohane,tempocnn
                     which models to publish (default: all three)
  --repo OWNER/NAME  default: sitiyou/namioto
  --data-dir DIR     where the aligners live (default: $XDG_DATA_HOME/namioto/models)
  --language CODE    the aligners' language directory (default: ja)
  --tempocnn FILE    the TempoCNN .onnx (default: the one in the data directory)
  --out DIR          where the zips are built (default: dist/models)
  --title TEXT       release title (default: "ONNX models")
  --notes TEXT       release notes (default: attribution and license summary)
  --reuse            upload into an existing release instead of refusing it; an asset that release
                     already holds is left as it is, so a package can be added beside it
  --clobber          replace assets the release already holds, instead of leaving them as they are
                     (breaks a published download)
  --dry-run          print what would run, touch nothing
EOF
}

die() {
    echo "$1" >&2
    exit 1
}

say() {
    echo "==> $1"
}

run() {
    if [ "$DRY" == 1 ]; then
        printf '    would run: %s\n' "$*"
    else
        "$@"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --tag)
            TAG="${2:?--tag needs a value}"
            shift
            ;;
        --repo)
            REPO="${2:?--repo needs a value}"
            shift
            ;;
        --data-dir)
            DATA_DIR="${2:?--data-dir needs a value}"
            shift
            ;;
        --language)
            LANGUAGE="${2:?--language needs a value}"
            shift
            ;;
        --tempocnn)
            TEMPOCNN="${2:?--tempocnn needs a value}"
            shift
            ;;
        --out)
            OUT="${2:?--out needs a value}"
            shift
            ;;
        --title)
            TITLE="${2:?--title needs a value}"
            shift
            ;;
        --notes)
            NOTES="${2:?--notes needs a value}"
            shift
            ;;
        --only)
            ONLY="${2//,/ }"
            shift
            ;;
        --reuse) REUSE=1 ;;
        --clobber) CLOBBER=1 ;;
        --dry-run) DRY=1 ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "Unknown option: $1"
            ;;
    esac
    shift
done

[ -n "$REPO" ] || die "--repo cannot be empty"
[ -n "$TAG" ] || die "--tag cannot be empty"

command -v zip >/dev/null || die "zip is needed and is not installed"
command -v sha256sum >/dev/null || die "sha256sum is needed and is not installed"
if [ "$DRY" == 0 ]; then
    command -v gh >/dev/null || die "gh is needed and is not installed"
    # the active account, not `gh auth status`: that one also fails on another account's dead token
    gh api user >/dev/null 2>&1 || die "gh is not logged in; run: gh auth login"
    gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 && {
        [ "$REUSE" == 1 ] || die "release $TAG already exists; a re-export wants a new tag, or --reuse to add to it"
    } || true
fi

# One zip per ONNX model, flat, and named the way GAME's own release assets are: the model, then
# -onnx. The language is not in the name, so a second language for one model would need its own
# tag. The aligner's package has to hold model.onnx and vocab.json together, because
# model_store.unpack takes the folder holding the first of a model's files and moves what is beside
# it into place.
assets=()
build_zip() {
    local asset="$1"
    shift
    for source in "$@"; do
        [ -f "$source" ] || die "$source is missing"
    done
    say "packing $asset"
    run rm -f "$OUT/$asset"
    run zip -j -q "$OUT/$asset" "$@"
    assets+=("$OUT/$asset")
}

if [ "$DRY" == 0 ]; then
    mkdir -p "$OUT"
fi
for model in $ONLY; do
    case "$model" in
        mms | yohane)
            directory="$DATA_DIR/$model/$LANGUAGE"
            build_zip "$model-onnx-int8.zip" "$directory/model.onnx" "$directory/vocab.json"
            # the half-precision copy a GPU run loads: published apart, because it is twice the size
            # and a CPU-only install would pay for it
            if [ -f "$directory/model.fp16.onnx" ]; then
                build_zip "$model-onnx-fp16.zip" "$directory/model.fp16.onnx"
            else
                say "no $directory/model.fp16.onnx: no half-precision package for $model"
            fi
            ;;
        tempocnn)
            source="$TEMPOCNN"
            [ -n "$source" ] || source="$DATA_DIR/tempocnn/deeptemp-k16-3.onnx"
            license="LICENSE-CC-BY-NC-SA-4.0.txt"
            if [ -f "$license" ]; then
                build_zip "tempocnn-onnx.zip" "$source" "$license"
            else
                build_zip "tempocnn-onnx.zip" "$source"
            fi
            ;;
        *)
            die "Unknown model: $model (expected mms, yohane or tempocnn)"
            ;;
    esac
done

say "recording the digests"
if [ "$DRY" == 0 ]; then
    (cd "$OUT" && sha256sum ./*.zip >SHA256SUMS)
    cat "$OUT/SHA256SUMS"
fi

say "publishing release $TAG on $REPO"
if [ "$DRY" == 1 ]; then
    printf '    would run: gh release create %s --repo %s --title %s --notes-file - --latest=false\n' \
        "$TAG" "$REPO" "$TITLE"
else
    if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
        say "release $TAG is already there, adding to it"
    else
        printf '%s\n' "$NOTES" | gh release create "$TAG" --repo "$REPO" --title "$TITLE" --notes-file - --latest=false
    fi
fi

upload=(gh release upload "$TAG" --repo "$REPO")
if [ "$CLOBBER" == 1 ]; then
    upload+=(--clobber)
fi

# an asset the release already holds is left alone, so a package can be added beside the ones it was
# published with; --clobber is how a published one is replaced instead
published=""
if [ "$DRY" == 0 ]; then
    published="$(gh api "repos/$REPO/releases/tags/$TAG" --jq '.assets[].name')"
fi
fresh=()
for asset in "${assets[@]}"; do
    name="$(basename "$asset")"
    if [ "$CLOBBER" == 0 ] && printf '%s\n' "$published" | grep -qxF -- "$name"; then
        say "$TAG already holds $name; leaving it as it is"
    else
        fresh+=("$asset")
    fi
done
if [ "${#fresh[@]}" -gt 0 ]; then
    run "${upload[@]}" "${fresh[@]}"
fi

registry_hint() {
    cat <<EOF

Point the registry at the release (namioto/model_store.py, MODELS):

  "aligner": Model(
      name="aligner",
      env="NAMIOTO_ALIGN_MODEL",
      files=("model.onnx", "vocab.json"),
      hint="convert one with scripts/export_align_model.py",
      asset="https://github.com/$REPO/releases/download/$TAG/{model}-onnx-int8.zip",
  ),
  "aligner_fp16": Model(
      name="aligner_fp16",
      env="NAMIOTO_ALIGN_MODEL",
      files=("model.fp16.onnx",),
      asset="https://github.com/$REPO/releases/download/$TAG/{model}-onnx-fp16.zip",
  ),
  "tempocnn": Model(
      name="tempocnn",
      env="NAMIOTO_TEMPOCNN_MODEL",
      files=("deeptemp-k16-3.onnx",),
      asset="https://github.com/$REPO/releases/download/$TAG/tempocnn-onnx.zip",
  ),

model_store.Model.url() fills {model} with the first key of the entry - the size for game, the
model for the aligner - so it needs that key name today instead of {variant}, and the aligner needs
its asset filled in. One key is enough while there is one language per model.
EOF
}

if [ "$DRY" == 1 ]; then
    say "dry run: nothing was published"
    registry_hint
    exit 0
fi

say "checking what the release holds"
gh api "repos/$REPO/releases/tags/$TAG" \
    --jq '.assets[] | "\(.name)\t\(.size)\t\(.browser_download_url)"' |
    tee "$OUT/assets.txt"

for asset in "${fresh[@]}"; do
    name="$(basename "$asset")"
    local_size="$(stat -c%s "$asset")"
    remote_size="$(awk -v name="$name" '$1 == name { print $2 }' "$OUT/assets.txt")"
    [ "$remote_size" == "$local_size" ] || die "$name is $remote_size bytes on the release, not $local_size"
done
if [ "${#fresh[@]}" -gt 0 ]; then
    say "every asset this run uploaded matches its local zip"
else
    say "nothing to upload; the release already holds every asset"
fi
registry_hint
