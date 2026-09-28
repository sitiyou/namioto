# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the device registry: what a device is, and how a CPU/GPU choice reaches one."""

from __future__ import annotations

import sys
import types

import pytest

from namioto import settings as store
from namioto.analysis import devices


def test_every_device_names_the_provider_it_runs_with() -> None:
    assert devices.providers("cpu") == ("CPUExecutionProvider",)
    assert devices.providers("cuda") == ("CUDAExecutionProvider", "CPUExecutionProvider")
    assert devices.providers("webgpu") == ("WebGpuExecutionProvider", "CPUExecutionProvider")
    with pytest.raises(ValueError, match="unknown device"):
        devices.providers("gpu")


def test_a_gpu_device_is_told_apart_from_the_cpu() -> None:
    assert tuple(device.key for device in devices.DEVICES if device.kind == "gpu") == devices.GPU_KEYS
    assert devices.get("cpu").kind == "cpu"
    assert devices.AUTO not in devices.KEYS


def test_availability_reads_what_the_runtime_was_built_with(monkeypatch) -> None:
    monkeypatch.setattr(devices, "installed", lambda: ("CPUExecutionProvider",))
    assert devices.available("cpu") is True
    assert devices.available("cuda") is False
    assert devices.missing("cuda") == ("CUDAExecutionProvider",)

    monkeypatch.setattr(devices, "installed", lambda: ("CUDAExecutionProvider", "CPUExecutionProvider"))
    assert devices.available("cuda") is True
    assert devices.missing("cuda") == ()


def test_the_first_available_gpu_skips_what_is_not_there(monkeypatch) -> None:
    monkeypatch.setattr(devices, "available", lambda key: key == "webgpu")
    assert devices.first_available_gpu() == "webgpu"

    monkeypatch.setattr(devices, "available", lambda key: False)
    assert devices.first_available_gpu() == devices.GPU_KEYS[0]  # nothing to pick from, so the first


def test_the_setting_names_the_gpu(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NAMIOTO_SETTINGS", str(tmp_path / "settings.json"))
    saved = store.Settings()
    store.set_value(saved, "hardware", "gpu", "webgpu")
    store.save(saved)

    assert devices.configured_gpu() == "webgpu"


def test_an_auto_setting_takes_the_first_available_gpu(monkeypatch) -> None:
    monkeypatch.setattr(devices, "available", lambda key: key == "webgpu")
    assert devices.configured_gpu() == "webgpu"


def test_a_run_choice_resolves_through_the_setting(monkeypatch) -> None:
    monkeypatch.setattr(devices, "configured_gpu", lambda: "webgpu")
    monkeypatch.setattr(devices, "available", lambda key: True)
    assert devices.resolve("cpu") == "cpu"
    assert devices.resolve("gpu") == "webgpu"


def test_the_webgpu_provider_is_told_which_gpu_to_prefer(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NAMIOTO_SETTINGS", str(tmp_path / "settings.json"))
    saved = store.Settings()
    store.set_value(saved, "hardware", "power", "low-power")
    store.save(saved)

    assert devices.provider_options("WebGpuExecutionProvider") == {"powerPreference": "low-power"}
    assert devices.provider_options("CPUExecutionProvider") == {}


def test_a_gpu_that_is_not_there_falls_back_to_the_cpu(monkeypatch) -> None:
    monkeypatch.setattr(devices, "configured_gpu", lambda: "cuda")
    monkeypatch.setattr(devices, "available", lambda key: False)
    assert devices.resolve("gpu") == "cpu"


def test_a_working_runtime_has_nothing_to_report() -> None:
    assert devices.validate() is None


def test_a_missing_runtime_is_named(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    assert devices.validate() == devices.MISSING_RUNTIME


def test_a_mixed_runtime_is_named(monkeypatch) -> None:
    monkeypatch.setattr(devices, "_owners", lambda: ("onnxruntime", "onnxruntime-gpu"))
    assert devices.validate() == devices.MIXED_RUNTIME


def test_a_plugin_provider_is_registered_before_it_is_listed(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    plugin = types.ModuleType("onnxruntime_ep_webgpu")
    plugin.get_library_path = lambda: "/lib/plugin.so"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime_ep_webgpu", plugin)
    monkeypatch.setattr(devices, "_REGISTERED", set())

    class Runtime:
        @staticmethod
        def get_available_providers():
            return ("WebGpuExecutionProvider", "CPUExecutionProvider")

        @staticmethod
        def register_execution_provider_library(name, path):
            calls.append((name, path))

    monkeypatch.setitem(sys.modules, "onnxruntime", Runtime)
    assert "WebGpuExecutionProvider" in devices.installed()
    devices.installed()  # a second check must not register the same library again
    assert calls == [("webgpu", "/lib/plugin.so")]


def test_a_plugin_that_is_not_installed_is_simply_missing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "onnxruntime_ep_webgpu", None)
    monkeypatch.setattr(devices, "_REGISTERED", set())

    class Runtime:
        @staticmethod
        def get_available_providers():
            return ("CPUExecutionProvider",)

    monkeypatch.setitem(sys.modules, "onnxruntime", Runtime)
    assert "WebGpuExecutionProvider" not in devices.installed()
    assert devices.available("webgpu") is False
