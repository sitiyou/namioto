# SPDX-License-Identifier: AGPL-3.0-only
"""Compatibility imports for the WaveTone backend's former module path."""

from scipy.signal import oaconvolve

from namioto.analysis.spectrum_backends import wavetone as _wavetone

decimate = _wavetone.decimate
correlation_kernel = _wavetone.correlation_kernel


def amplitudes(*args, **kwargs):
    # Preserve the old module's monkeypatch point used by downstream callers.
    original = _wavetone.oaconvolve
    _wavetone.oaconvolve = oaconvolve
    try:
        return _wavetone.amplitudes(*args, **kwargs)
    finally:
        _wavetone.oaconvolve = original
