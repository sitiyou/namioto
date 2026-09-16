#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Convert a wav2vec2 CTC aligner to the ONNX file and dictionary `namioto-align` loads.

A development tool, not a dependency of the program: it needs torch, transformers and ONNX Runtime's
quantizer, none of which namioto installs. It writes `model.onnx` (int8, from the quantised graph)
and `vocab.json` into the aligner's data directory, or into `--out`.

    uv run --with torch --with transformers --with onnx --with onnxruntime \
        scripts/export_align_model.py

The graph takes `input_values` and returns `logits`; quantisation is limited to `MatMul`, because
the dynamo exporter writes the convolutions' bias outside the initializers and quantising those
fails. The language's model is the one whisperx aligns with (`DEFAULT_ALIGN_MODELS_HF`).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

import namioto.align

MODELS = {
    "ja": "jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
}
SECONDS = 16
OPSET = 18


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=tuple(MODELS), default="ja", help="which aligner to convert")
    parser.add_argument("--model", help="a HuggingFace model id, instead of the language's own")
    parser.add_argument("--out", type=pathlib.Path, help="where to write it (default: the data directory)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    name = args.model or MODELS[args.language]
    out = args.out or namioto.align.model_dir(args.language)
    out.mkdir(parents=True, exist_ok=True)

    import torch
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    processor = Wav2Vec2Processor.from_pretrained(name)
    model = Wav2Vec2ForCTC.from_pretrained(name).eval()

    with tempfile.TemporaryDirectory() as scratch:
        source = pathlib.Path(scratch) / "fp32.onnx"
        with torch.inference_mode():
            torch.onnx.export(
                model,
                (torch.randn(1, SECONDS * namioto.align.SAMPLE_RATE),),
                source,
                input_names=["input_values"],
                output_names=["logits"],
                dynamic_axes={
                    "input_values": {0: "batch_size", 1: "sequence_length"},
                    "logits": {0: "batch_size", 1: "sequence_length"},
                },
                opset_version=OPSET,
                do_constant_folding=True,
            )

        from onnxruntime.quantization import QuantType, quantize_dynamic

        quantize_dynamic(
            str(source),
            str(out / namioto.align.MODEL_FILE),
            weight_type=QuantType.QInt8,
            op_types_to_quantize=["MatMul"],
        )

    vocab = {char.lower(): int(code) for char, code in processor.tokenizer.get_vocab().items()}
    (out / namioto.align.VOCAB_FILE).write_text(json.dumps(vocab, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out / namioto.align.MODEL_FILE} and {out / namioto.align.VOCAB_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
