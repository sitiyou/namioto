# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the ported GAME extractor: note assembly, the grid, and where the models come from."""

from __future__ import annotations

import io
import pathlib
import zipfile

import pytest

from namioto.game import (
    OnnxBackend,
    asset_url,
    collect_notes,
    download_model,
    estimate_grid_period,
    estimate_grid_phase,
    extract,
    install_zip,
    is_installed,
    main,
    merge_notes,
    model_dir,
    models_root,
    quantize_notes,
    resolve_model,
)


def test_collect_notes_turns_boundaries_and_scores_into_notes() -> None:
    # the estimator answers only for the notes it kept, so its arrays are the shorter ones
    notes = collect_notes(
        durations=[0.5, 0.5, 0.5],
        scores=[60.0, 62.0],
        presence=[True, False],
        offset=1.0,
        length=1.5,
    )
    assert notes == [(1.0, 1.5, 60.0)]  # the second note is absent, the third is past the chunk


def test_merge_notes_sorts_and_drops_the_overlaps() -> None:
    jumbled = [(0.5, 1.0, 62.0), (0.0, 0.5, 60.0), (0.4, 0.9, 64.0), (1.0, 1.0, 65.0)]
    assert merge_notes(jumbled) == [
        (0.0, 0.5, 60.0),
        (0.5, 0.9, 64.0),  # pushed to start where the one before it ended
        (0.9, 1.0, 62.0),
        # the last one has no length left and is dropped
    ]


def test_quantize_notes_lands_on_the_grid_and_keeps_a_cell() -> None:
    notes = [(0.51, 0.98, 60.0), (1.02, 1.03, 62.0)]
    assert quantize_notes(notes, 0.5) == [(0.5, 1.0, 60.0), (1.0, 1.5, 62.0)]


def test_the_grid_phase_is_the_one_that_fits_the_onsets() -> None:
    onsets = [0.02 + 0.5 * index for index in range(12)]  # notes a little late of the grid
    phase = estimate_grid_phase(onsets, 0.5)
    assert phase == pytest.approx(0.02, abs=0.01)
    assert estimate_grid_phase([], 0.5) == 0.0


def test_the_grid_period_follows_a_slightly_wrong_tempo() -> None:
    unit = 0.5
    onsets = [0.0 + index * unit * 1.03 for index in range(16)]  # 3% slower than the grid says
    assert estimate_grid_period(onsets, 0.0, unit) == pytest.approx(unit * 1.03, rel=0.01)
    assert estimate_grid_period([0.0, 0.5], 0.0, unit) == unit  # too few notes to say anything


def test_a_model_directory_without_a_config_is_refused(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="config.json"):
        OnnxBackend(tmp_path)


def test_extract_checks_the_language_against_the_config() -> None:
    class Backend:
        languages = {"zh": 4}
        sr = 44100

    with pytest.raises(ValueError, match="japanese"):
        extract(Backend(), "song.wav", language="japanese")


def test_the_models_live_in_the_data_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert models_root() == tmp_path / "namioto" / "models" / "game"
    assert model_dir("medium") == models_root() / "medium"
    assert not is_installed("medium")
    assert is_installed("small") is False


def test_the_asset_url_names_the_release_and_the_size() -> None:
    assert asset_url("small").endswith("/v1.0.3/GAME-1.0.3-small-onnx.zip")
    assert asset_url("large").startswith("https://github.com/openvpi/GAME/releases/download/")
    with pytest.raises(ValueError, match="unknown model size"):
        asset_url("tiny")


def make_zip(path: pathlib.Path) -> pathlib.Path:
    """A release zip the way GAME publishes them: one top-level folder holding the package."""
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("GAME-1.0.3-small-onnx/config.json", "{}")
        bundle.writestr("GAME-1.0.3-small-onnx/encoder.onnx", "not really a model")
    return path


def test_unpacking_a_release_zip_moves_the_package_into_place(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    target = install_zip(make_zip(tmp_path / "release.zip"), "small")

    assert target == model_dir("small")
    assert (target / "config.json").is_file()
    assert (target / "encoder.onnx").read_text() == "not really a model"
    assert is_installed("small")


def test_a_zip_without_a_package_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as bundle:
        bundle.writestr("readme.txt", "nothing here")
    with pytest.raises(ValueError, match="no config.json"):
        install_zip(empty, "small")


class FakeResponse(io.BytesIO):
    """What urlopen hands back, as far as the downloader is concerned."""

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.close()


def test_downloading_unpacks_the_release_into_the_data_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    make_zip(tmp_path / "release.zip")
    payload = (tmp_path / "release.zip").read_bytes()
    seen: list[str] = []

    def opener(url):
        seen.append(url)
        return FakeResponse(payload)

    steps: list[tuple[int, int]] = []
    target = download_model("small", lambda done, total: steps.append((done, total)), opener=opener)

    assert seen == [asset_url("small")]
    assert is_installed("small") and (target / "encoder.onnx").is_file()
    assert steps == [(len(payload), len(payload))]  # the whole file, in one block
    assert not list(target.parent.glob("*.zip"))  # the archive does not linger


def test_resolve_model_prefers_what_is_already_there(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("NAMIOTO_GAME_MODEL", raising=False)

    assert resolve_model(tmp_path / "handmade") == tmp_path / "handmade"  # an explicit path wins
    monkeypatch.setenv("NAMIOTO_GAME_MODEL", str(tmp_path / "from-env"))
    assert resolve_model() == tmp_path / "from-env"
    monkeypatch.delenv("NAMIOTO_GAME_MODEL")

    install_zip(make_zip(tmp_path / "release.zip"), "small")
    assert resolve_model(size="small") == model_dir("small")  # already installed: no download
    with pytest.raises(FileNotFoundError, match="no medium model"):
        resolve_model(size="medium", download=False)


def test_the_cli_reports_a_model_it_cannot_get(capsys, monkeypatch) -> None:
    def failing(*args, **kwargs):
        raise FileNotFoundError("no small model in /nowhere")

    monkeypatch.setattr("namioto.game.resolve_model", failing)
    assert main(["song.wav"]) == 2
    assert "no small model in /nowhere" in capsys.readouterr().err


def test_the_cli_can_only_fetch_a_model(capsys, monkeypatch) -> None:
    monkeypatch.setattr("namioto.game.resolve_model", lambda *args, **kwargs: pathlib.Path("/models/small"))
    assert main([]) == 0
    assert "small model in /models/small" in capsys.readouterr().out
