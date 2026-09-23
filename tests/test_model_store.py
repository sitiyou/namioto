# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the model registry: where a variant is filed, and the session opened on it."""

from __future__ import annotations

import zipfile

import pytest

from namioto import model_store


def test_each_family_keeps_the_directory_it_already_uses(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert model_store.path("game", "small") == tmp_path / "namioto" / "models" / "game" / "small"
    assert model_store.path("aligner", "mms", "ja") == tmp_path / "namioto" / "models" / "mms" / "ja"


def test_the_providers_a_name_stands_for() -> None:
    assert model_store.providers("cpu") == ("CPUExecutionProvider",)
    assert model_store.providers("cuda") == ("CUDAExecutionProvider", "CPUExecutionProvider")
    with pytest.raises(ValueError, match="unknown provider"):
        model_store.providers("gpu")


def test_a_session_is_built_with_the_providers_that_were_asked_for(monkeypatch) -> None:
    made: list[tuple[str, list[str]]] = []

    class Session:
        def __init__(self, path, providers=None, **options):
            made.append((path, providers))

    monkeypatch.setattr(model_store.ort, "InferenceSession", Session)
    model_store.session("/models/x.onnx", "cuda")
    model_store.session("/models/x.onnx")

    assert made == [
        ("/models/x.onnx", ["CUDAExecutionProvider", "CPUExecutionProvider"]),
        ("/models/x.onnx", ["CPUExecutionProvider"]),
    ]


def test_unpacking_takes_the_folder_holding_the_first_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("model.onnx", "weights")
        bundle.writestr("vocab.json", "{}")

    target = model_store.unpack("aligner", archive, ("mms", "ja"))

    assert target == model_store.path("aligner", "mms", "ja")
    assert (target / "model.onnx").read_text() == "weights"
    assert model_store.installed("aligner", "mms", "ja")


def test_nothing_installed_and_no_download_says_what_to_do(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv(model_store.MODELS["aligner"].env, raising=False)

    with pytest.raises(FileNotFoundError) as error:
        model_store.resolve("aligner", parts=("mms", "ja"), download=False)

    message = str(error.value)
    assert "no mms/ja model" in message
    assert model_store.MODELS["aligner"].env in message
    assert model_store.MODELS["aligner"].hint in message


def test_a_model_with_nothing_published_says_what_to_do(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    unpublished = model_store.Model(name="unpublished", env="NAMIOTO_UNPUBLISHED_MODEL", files=("model.onnx",))
    monkeypatch.setitem(model_store.MODELS, "unpublished", unpublished)

    with pytest.raises(FileNotFoundError) as error:
        model_store.resolve("unpublished")

    message = str(error.value)
    assert "no unpublished model" in message
    assert unpublished.env in message
