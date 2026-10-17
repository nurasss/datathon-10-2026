# Local model provenance

Both files were present at baseline. No training or weight replacement was performed.
The SHA256 and byte sizes match the Git LFS pointers in the corresponding
[OpenCV Zoo](https://github.com/opencv/opencv_zoo) model folders.

| File | Upstream artifact | Bytes | SHA256 | License | Runtime |
|---|---|---:|---|---|---|
| `nanodet.onnx` | `models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx` | 3800954 | `4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186` | Apache-2.0 | Active, person class only |
| `person_mediapipe.onnx` | `models/person_detection_mediapipe/person_detection_mediapipe_2023mar.onnx` | 11990159 | `47fd5599d6fa17608f03e0eb0ae230baa6e597d7e8a2c8199fe00abea55a701f` | Apache-2.0 | Unused in current pipeline |

License sources, verified 2026-10-17:

- <https://github.com/opencv/opencv_zoo/blob/main/models/object_detection_nanodet/LICENSE>
- <https://github.com/opencv/opencv_zoo/blob/main/models/person_detection_mediapipe/LICENSE>

These are person/object detectors, not face recognition models. The installed OpenCV
wheel may contain unused cascade XML files; the application does not load them.
Local copy of the model license: `LICENSE-APACHE-2.0.txt`.
