# SPDX-License-Identifier: AGPL-3.0-only
"""Where a model runs: one registry of the devices the program can offer.

A device names the ONNX Runtime provider that puts a model on it, the runtime a person would have to
install, what to do when that runtime is missing and the model flavor it reads. The settings spec
reads the keys and labels, `model_store` its provider lists, the settings window asks `available` /
`missing` whether the runtime is there, `validate` says whether a run can open a model at all, and a
run's CPU/GPU choice is turned into a device key by `resolve`. ONNX Runtime is imported only inside
`installed` and `validate`, so the GUI's startup path stays free of it. The `flavor` and `arch`
fields say which model a device reads and whether that model is made per
GPU architecture; nothing downloads them yet - the per-device model integration is separate.
"""

from __future__ import annotations

from dataclasses import dataclass

# what a run asks for; the GPU one is turned into a device key by `resolve`
RUN_CHOICES = ("cpu", "gpu")
RUN_LABELS = ("CPU", "GPU")
# the settings value that lets a GPU run pick the first device whose runtime is installed
AUTO = ""


@dataclass(frozen=True)
class Device:
    key: str
    label: str
    kind: str  # cpu or gpu
    providers: tuple[str, ...]
    runtime: str = ""  # the runtime a person would install, named for one
    hint: str = ""  # what to do when that runtime is missing
    flavor: str = "onnx"  # which model the device reads
    arch: bool = False  # whether that model is made per GPU architecture


DEVICES: tuple[Device, ...] = (
    Device("cpu", "CPU", "cpu", ("CPUExecutionProvider",)),
    Device(
        "cuda",
        "CUDA",
        "gpu",
        ("CUDAExecutionProvider", "CPUExecutionProvider"),
        runtime="NVIDIA CUDA",
        hint="install an onnxruntime build with the CUDA provider",
    ),
    Device(
        "migraphx",
        "MIGraphX (ROCm)",
        "gpu",
        ("MIGraphXExecutionProvider", "CPUExecutionProvider"),
        runtime="AMD ROCm",
        hint="install an onnxruntime build with the MIGraphX provider",
        flavor="mxr",
        arch=True,
    ),
)
KEYS = tuple(device.key for device in DEVICES)
LABELS = tuple(device.label for device in DEVICES)
GPU_KEYS = tuple(device.key for device in DEVICES if device.kind == "gpu")
GPU_LABELS = tuple(device.label for device in DEVICES if device.kind == "gpu")
PROVIDERS = {device.key: device.providers for device in DEVICES}


def get(key: str) -> Device:
    for device in DEVICES:
        if device.key == key:
            return device
    raise ValueError(f"unknown device {key!r}, expected one of {', '.join(KEYS)}")


def providers(key: str) -> tuple[str, ...]:
    """The ONNX Runtime providers a device runs with, its own first and the CPU as the fallback."""
    return get(key).providers


def installed() -> tuple[str, ...]:
    """The providers the ONNX Runtime on this machine was built with; imports it, never at startup."""
    try:
        import onnxruntime as ort
    except ImportError:
        return ()
    return tuple(ort.get_available_providers())


# what the interface says when a run cannot open a model; kept as the English a catalog can key on,
# since the interface is the side that translates it
MISSING_RUNTIME = "ONNX Runtime is not installed; install namioto[cpu], namioto[cuda] or namioto[rocm] and restart"
MIXED_RUNTIME = (
    "two onnxruntime builds are installed; remove all but one and reinstall "
    "namioto[cpu], namioto[cuda] or namioto[rocm]"
)


def validate() -> str | None:
    """What is wrong with the runtime a run needs, or None when one can go ahead.

    The CPU build is an optional dependency, so a machine may hold no ONNX Runtime at all; and two
    builds that provide the same `onnxruntime` module may have been installed over each other, which
    installs cleanly yet leaves one of them half-overwritten. Either is said before a model is loaded.
    """
    try:
        import onnxruntime  # noqa: F401
    except (ImportError, OSError):
        return MISSING_RUNTIME
    if len(_owners()) > 1:
        return MIXED_RUNTIME
    return None


def _owners() -> tuple[str, ...]:
    """The distributions providing the `onnxruntime` module; more than one is a broken install."""
    from importlib.metadata import packages_distributions

    return tuple(packages_distributions().get("onnxruntime", ()))


def missing(key: str) -> tuple[str, ...]:
    """The providers a device needs that this ONNX Runtime does not have."""
    found = installed()
    return tuple(name for name in get(key).providers if name not in found)


def available(key: str) -> bool:
    """Whether a device can be used at all: its own provider decides, not the CPU fallback."""
    return get(key).providers[0] in installed()


def first_available_gpu() -> str:
    """The first GPU device whose runtime is installed, or the first GPU there is when none is."""
    for key in GPU_KEYS:
        if available(key):
            return key
    return GPU_KEYS[0]


def configured_gpu() -> str:
    """The GPU device a GPU run stands for: the one settings name, or the first available for auto."""
    from namioto import settings as store

    key = store.load().hardware.gpu
    return key if key in GPU_KEYS else first_available_gpu()


def resolve(choice: str) -> str:
    """The device key a run's CPU/GPU choice stands for; an unavailable GPU falls back to the CPU."""
    if choice != "gpu":
        return "cpu"
    key = configured_gpu()
    return key if available(key) else "cpu"
