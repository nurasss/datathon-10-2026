"""Benchmark a YOLOX ONNX model on CPU with onnxruntime.

Usage: python -I bench.py model.onnx image.jpg [threads]
Prints ms/frame (median over 30 runs after warmup) and top detections.
"""
import statistics
import sys
import time

import cv2
import numpy as np
import onnxruntime as ort

model, img_path = sys.argv[1], sys.argv[2]
threads = int(sys.argv[3]) if len(sys.argv) > 3 else 2
so = ort.SessionOptions()
so.intra_op_num_threads = threads
sess = ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])
inp = sess.get_inputs()[0]
sh, sw = inp.shape[2], inp.shape[3]

img = cv2.imread(img_path)
r = min(sh / img.shape[0], sw / img.shape[1])
padded = np.full((sh, sw, 3), 114, np.uint8)
rs = cv2.resize(img, (int(img.shape[1] * r), int(img.shape[0] * r)))
padded[: rs.shape[0], : rs.shape[1]] = rs
x = padded.transpose(2, 0, 1)[None].astype(np.float32)

for _ in range(3):
    sess.run(None, {inp.name: x})
times = []
for _ in range(30):
    t0 = time.perf_counter()
    out = sess.run(None, {inp.name: x})[0]
    times.append((time.perf_counter() - t0) * 1000)
print(f"input {sh}x{sw} threads={threads} median {statistics.median(times):.1f} ms, p90 {sorted(times)[26]:.1f} ms")
pred = out[0]
scores = pred[:, 4:5] * pred[:, 5:]
best = scores.max(1)
idx = best.argsort()[::-1][:5]
for i in idx:
    print(f"cls={scores[i].argmax()} score={best[i]:.3f} box(cx,cy,w,h)/r={(pred[i,:4]/r).round(1).tolist()}")
