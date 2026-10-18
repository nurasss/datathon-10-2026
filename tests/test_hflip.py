import cv2
import numpy as np

from vision.videoprobe import FrameReader, probe


def test_hflip_mirrors_all_decode_paths_without_retiming(tmp_path):
    path = tmp_path / 'asymmetric.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 5, (64, 48))
    assert writer.isOpened()
    for i in range(10):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        frame[:, :20] = (10+i, 80, 220)
        writer.write(frame)
    writer.release()
    meta = probe(path)
    for method, args in [('iter_sampled', (2,)), ('iter_window', (.2, 1.2, 5))]:
        with FrameReader(path, meta) as normal, FrameReader(path, meta, hflip=True) as flipped:
            a, b = list(getattr(normal, method)(*args)), list(getattr(flipped, method)(*args))
        assert [t for t, _ in a] == [t for t, _ in b]
        assert a
        for (_, original), (_, mirrored) in zip(a, b):
            np.testing.assert_array_equal(mirrored, cv2.flip(original, 1))
    with FrameReader(path, meta) as normal, FrameReader(path, meta, hflip=True) as flipped:
        a = normal.read_frames_at([0, .2, .4])
        b = flipped.read_frames_at([0, .2, .4])
        for t in a:
            np.testing.assert_array_equal(b[t], cv2.flip(a[t], 1))
