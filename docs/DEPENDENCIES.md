# Dependency and model license inventory

Recorded from the actual `.venv` on 2026-10-17 using `importlib.metadata`.
Versions in `apps/api/requirements.txt` now match the tested direct dependencies.
`pydantic-settings`, Pillow, telemetry and some uvicorn extras were already installed
at baseline; the application does not require them. They were not removed from the
user's environment. No AGPL or CC-BY-NC package/model was found in this inventory.

| Installed pip package | Version | License (installed metadata) |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| click | 8.5.0 | BSD-3-Clause |
| et_xmlfile | 2.0.0 | MIT |
| fastapi | 0.143.3 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| numpy | 2.2.6 | BSD-3-Clause; bundled components have their own notices |
| opencv-python-headless | 4.12.0.88 | Apache-2.0; bundled components below |
| openpyxl | 3.1.5 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 (unused by application code) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow | 13.0.0 | MIT-CMU (unused by application code) |
| pip | 26.2.1 | MIT |
| pluggy | 1.7.0 | MIT |
| pydantic | 2.14.1 | MIT |
| pydantic-settings | 2.16.0 | MIT (unused) |
| pydantic_core | 2.50.1 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 9.1.1 | MIT |
| python-dotenv | 1.2.4 | BSD-3-Clause (not imported by application) |
| python-multipart | 0.0.32 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| starlette | 1.8.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop | 0.23.0 | MIT / Apache-2.0 (metadata classifiers) |
| watchfiles | 1.3.0 | MIT |
| websockets | 17.2 | BSD-3-Clause |

MPL-2.0 and PSF-2.0 also permit commercial use; they must not be misreported as
MIT/Apache/BSD. `certifi` comes from developer/test HTTP clients, not AI inference.
This is an inventory, not a substitute for checking redistribution obligations.

## Model weights (`packages/vision/models`)

| File | License | Bytes | SHA256 | Use |
|---|---|---:|---|---|
| nanodet.onnx | Apache-2.0 | 3800954 | `4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186` | Active NanoDet CPU / person class |
| person_mediapipe.onnx | Apache-2.0 | 11990159 | `47fd5599d6fa17608f03e0eb0ae230baa6e597d7e8a2c8199fe00abea55a701f` | Unused baseline asset |

Both byte sizes/hashes were verified against OpenCV Zoo's Git LFS pointers:

- <https://github.com/opencv/opencv_zoo/blob/main/models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx>
- <https://github.com/opencv/opencv_zoo/blob/main/models/person_detection_mediapipe/person_detection_mediapipe_2023mar.onnx>

License texts: the `LICENSE` in each upstream model folder (Apache-2.0).
Provenance and local license copy: `packages/vision/models/README.md`,
`packages/vision/models/LICENSE-APACHE-2.0.txt`. No weights exceed 50 MiB.

## Non-pip components and third-party notices

- Python 3.12: PSF-2.0.
- System `ffprobe` / `ffmpeg` here: **9.0.2-tessus**, built with
  `--enable-gpl --enable-version3`, therefore **GPL-3.0-or-later**, not Apache/MIT/BSD.
  Called as separate local executables (metadata / video integrity only, no audio decoding).
  GPL permits commercial use, but redistribution obligations differ. No system binary
  was copied into the repo. Docker's distro FFmpeg is a separate build and was not tested.
- OpenCV wheels bundle FFmpeg under LGPL-2.1-or-later, as well as image libraries and
  Intel components under their own licenses. See the **actual installed**
  `.venv/lib/python3.12/site-packages/cv2/LICENSE-3RD-PARTY.txt` and `LICENSE.txt`.
  The NumPy wheel's bundled licenses are in
  `.venv/lib/python3.12/site-packages/numpy-2.2.6.dist-info/LICENSE.txt`.
- If the organisers mean **only** Apache/MIT/BSD (rather than all commercial-use
  licenses), the native FFmpeg / transitive MPL/LGPL/PSF components need a separate
  allowlist decision/build. This has not been silently declared compliant with
  an Apache/MIT/BSD-only allowlist. AGPL and noncommercial model weights are absent.
