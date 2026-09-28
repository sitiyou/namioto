#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Convert a FA-Kara forced-alignment model to the ONNX file and dictionary `namioto-align` loads.

A development tool, not a runtime dependency: it needs torch and, per kind, torchaudio or
transformers, plus ONNX, none of which the program itself imports. It writes `model.onnx` (int8,
from the quantised graph) and `vocab.json` into the aligner's data directory, or into `--out`.

    uv run --group export scripts/export_align_model.py --model mms
    uv run --group export scripts/export_align_model.py --model yohane

`mms` is Meta's MMS forced-alignment checkpoint (`torchaudio.pipelines.MMS_FA`), the default;
`yohane` is `NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`, a karaoke fine-tune. Both are
wav2vec2 CTC models trained on romanised kana. The graph bakes in the waveform normalisation
the models expect, takes `input_values` and returns `logits`; quantisation is limited to `MatMul`,
because the exporter writes the convolutions' bias outside the initializers and quantising those
fails. The attention's `where(isnan(softmax), 0, softmax)` guard is dropped: it is a dead branch for
the finite input the graph is handed, and the graph is smaller without it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

from namioto.analysis import align

HF_MODELS = {"yohane": "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"}
SECONDS = 16
OPSET = 18


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=align.MODELS, default=align.DEFAULT_MODEL)
    parser.add_argument("--language", choices=align.LANGUAGES, default="ja")
    parser.add_argument(
        "--hf-model", help=f"a HuggingFace model id for --model yohane (default: {HF_MODELS['yohane']})"
    )
    parser.add_argument("--out", type=pathlib.Path, help="where to write it (default: the data directory)")
    return parser.parse_args(argv)


def drop_nan_guards(graph) -> int:
    """Drop every `where(isnan(x), fallback, x)` guard in `graph` and return how many were removed."""
    guard = {node.output[0]: node for node in graph.node if node.op_type == "IsNaN"}
    replace, dropped = {}, set()
    for consumer in graph.node:
        if consumer.op_type != "Where" or consumer.input[0] not in guard:
            continue
        guarded = guard[consumer.input[0]].input[0]
        if consumer.input[2] == guarded:
            replace[consumer.output[0]] = guarded
            dropped.update({guard[consumer.input[0]].name, consumer.name})
    if not dropped:
        return 0
    for node in graph.node:
        for index, name in enumerate(node.input):
            if name in replace:
                node.input[index] = replace[name]
    kept = [node for node in graph.node if node.name not in dropped]
    graph.ClearField("node")
    graph.node.extend(kept)
    return len(dropped)


def export(module, destination: pathlib.Path) -> None:
    """Write the int8 ONNX graph of `module`, which takes raw audio and returns `logits`."""
    import onnx
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic

    with tempfile.TemporaryDirectory() as scratch:
        source = pathlib.Path(scratch) / "fp32.onnx"
        with torch.inference_mode():
            torch.onnx.export(
                module,
                (torch.randn(1, SECONDS * align.SAMPLE_RATE),),
                source,
                input_names=["input_values"],
                output_names=["logits"],
                dynamic_axes={
                    "input_values": {0: "batch_size", 1: "sequence_length"},
                    "logits": {0: "batch_size", 1: "sequence_length"},
                },
                opset_version=OPSET,
                do_constant_folding=True,
                # the dynamo exporter ignores dynamic_axes, which the variable-length windows need
                dynamo=False,
            )
        graph = onnx.load(str(source))
        drop_nan_guards(graph)
        onnx.save(graph, str(source))
        quantize_dynamic(str(source), str(destination), weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul"])


def export_mms(destination: pathlib.Path) -> dict[str, int]:
    """Meta's MMS checkpoint: layer norm folded in, log-softmax and the star token left off."""
    import torch
    import torchaudio

    model = torchaudio.pipelines.MMS_FA.get_model(with_star=False)

    class Mms(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, values):
            values = (values - values.mean()) / torch.sqrt(values.var(unbiased=False) + 1e-5)
            logits, _ = self.inner(values)
            return logits

    # the wrapper's own layer_norm takes its shape from the waveform, which the exporter cannot
    # bake into the graph; the manual normalisation above replaces it
    model.normalize_waveform = False
    model.apply_log_softmax = False
    export(Mms(model), destination)

    labels = torchaudio.pipelines.MMS_FA.get_labels(star=None)
    return {"[pad]": 0, **{label: index for index, label in enumerate(labels) if index}}


def export_yohane(destination: pathlib.Path, name: str) -> dict[str, int]:
    """The karaoke fine-tune: the feature extractor's layer norm folded into the graph."""
    import torch
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    processor = Wav2Vec2Processor.from_pretrained(name)
    model = Wav2Vec2ForCTC.from_pretrained(name).eval()

    class Yohane(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, values):
            values = (values - values.mean()) / torch.sqrt(values.var(unbiased=False) + 1e-7)
            return self.inner(values).logits

    export(Yohane(model), destination)
    return {char.lower(): int(code) for char, code in processor.tokenizer.get_vocab().items()}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    destination = args.out or align.model_dir(args.model, args.language)
    destination.mkdir(parents=True, exist_ok=True)
    if args.model == "yohane":
        vocab = export_yohane(destination / align.MODEL_FILE, args.hf_model or HF_MODELS["yohane"])
    else:
        vocab = export_mms(destination / align.MODEL_FILE)
    (destination / align.VOCAB_FILE).write_text(
        json.dumps(vocab, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"wrote {destination / align.MODEL_FILE} and {destination / align.VOCAB_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
