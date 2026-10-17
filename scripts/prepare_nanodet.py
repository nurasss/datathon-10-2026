#!/usr/bin/env python3
"""Re-shape the same fully convolutional Zoo checkpoint to 320 (no training).

Only shape initializers and shape metadata change. Weights are unchanged.
onnx is a development-only dependency; production reads the saved ONNX file.
"""
from pathlib import Path
import argparse

import onnx
from onnx import numpy_helper


def reshape_model(source: Path, destination: Path, size: int = 320) -> None:
    model = onnx.load(source)
    replacements = {52: size // 8, 26: size // 16, 13: size // 32}
    weights = {i.name for i in model.graph.initializer}
    inputs = [i for i in model.graph.input if i.name not in weights]
    del model.graph.input[:]
    model.graph.input.extend(inputs)
    for init in model.graph.initializer:
        if init.data_type == onnx.TensorProto.INT64:
            a = numpy_helper.to_array(init).copy()
            for old, new in replacements.items():
                a[a == old] = new
            init.CopyFrom(numpy_helper.from_array(a, init.name))
    model.graph.input[0].type.tensor_type.shape.dim[2].dim_value = size
    model.graph.input[0].type.tensor_type.shape.dim[3].dim_value = size
    for output in model.graph.output:
        dims = output.type.tensor_type.shape.dim
        for old, new in replacements.items():
            if dims[1].dim_value == old * old:
                dims[1].dim_value = new * new
    del model.graph.value_info[:]
    model = onnx.shape_inference.infer_shapes(model)
    onnx.checker.check_model(model)
    onnx.save(model, destination)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "packages/vision/models"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "nanodet.onnx")
    parser.add_argument("--size", type=int, choices=[320, 416], default=320)
    parser.add_argument("--out", type=Path, default=root / "nanodet_320.onnx")
    args = parser.parse_args()
    reshape_model(args.source, args.out, args.size)
