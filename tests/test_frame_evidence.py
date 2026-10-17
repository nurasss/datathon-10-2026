"""Evidence extraction must be source-frame correct in one shared decode."""
import cv2
import numpy as np

from vision.videoprobe import FrameReader, probe


def test_batch_and_single_source_frame_mapping_agree(tmp_path):
    path = tmp_path / "numbered.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    assert writer.isOpened()
    for i in range(30):
        writer.write(np.full((48, 64, 3), i*7, dtype=np.uint8))
    writer.release()
    meta = probe(path)
    times = [1.0, .2, .21, 1.0, 0, 2.8, 4.0]
    with FrameReader(path, meta) as reader:
        # Changing the analysis iterator must not change evidence frame offsets.
        next(reader.iter_sampled(1))
        frames = reader.read_frames_at(times)
        assert frames[4.0] is None
        for t in times[:-1]:
            assert np.array_equal(frames[t], reader.read_frame_at(t))
            assert abs(float(frames[t].mean()) - round(t*10)*7) <= 2
        assert np.array_equal(frames[.2], frames[.21])
