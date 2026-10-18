# Local model provenance

No training or weight replacement was performed. The service ships exactly **one**
neural network: NanoDet-Plus-m-1.5x 416 (COCO, Apache-2.0, OpenCV Zoo) executed by
ONNX Runtime CPU (MIT). Everything else in the pipeline is classical CV (two-pass
sampling, motion and static-object analysis, zones, IoU/Kalman tracking) plus the
typed rules engine and the curator-approved norm registry. No cloud or LLM models
are used; faces and audio are not analyzed.

| File | Upstream artifact | Bytes | SHA256 | License | Use |
|---|---|---:|---|---|---|
| `nanodet_416.onnx` | same checkpoint, input 416 | 3796745 | `d891b8c939ce062b13fc7440195d427eeecbec307c2417564d6908309b263e19` | Apache-2.0 | **Production** — both passes (`configs/inference.default.json` → `detector`) |
| `nanodet_320.onnx` | same checkpoint, input 320 | 3796741 | `b81fb3258ccfa65adedf45be7a31454520ae7bd7b9c3d5c8b605fe0896386d72` | Apache-2.0 | Speed/count comparison export; not selected in production |
| `nanodet.onnx` | `models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx` | 3800954 | `4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186` | Apache-2.0 | Original upstream file (Git LFS), kept for the paired OpenCV baseline audit |

`nanodet.onnx` matches the OpenCV Zoo Git LFS pointer byte-for-byte, so its byte
size and SHA256 above refer to the upstream artifact. `nanodet_320.onnx` and
`nanodet_416.onnx` are task-3 exports of the same checkpoint:
`scripts/prepare_nanodet.py` removes initializer graph inputs so ORT can
constant-fold/fuse; the 320 export also changes spatial shape initializers. Learned
tensors are unchanged; export requires the development-only `onnx` package. The
baseline `person_mediapipe.onnx` asset is no longer in the repository and was never
used by the pipeline.

License sources, verified 2026-10-17:

- OpenCV Zoo NanoDet (Apache-2.0):
  <https://github.com/opencv/opencv_zoo/blob/main/models/object_detection_nanodet/LICENSE>
- ONNX Runtime CPU (MIT): pip package metadata; local copy —
  `LICENSE-ONNXRUNTIME-MIT.txt`
- Local copy of the model license: `LICENSE-APACHE-2.0.txt`

The detector's only class is *person*: the platform detects people and
static-object changes; it never recognizes faces, money, bags or safes directly,
and it is not a face recognition model. The installed OpenCV wheel may contain
unused cascade XML files; the application does not load them.
