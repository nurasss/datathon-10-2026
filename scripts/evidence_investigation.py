import cv2
import os
import numpy as np

VIDEO_DIR = "/Users/nuras/Desktop/datathon/data_raw/open/videos"
OUT_DIR = "/Users/nuras/Desktop/datathon/fv-scene/artifacts/evidence_check"
os.makedirs(OUT_DIR, exist_ok=True)

def get_video_path(name):
    for f in os.listdir(VIDEO_DIR):
        base, ext = os.path.splitext(f)
        norm_base = base.replace('X', 'Х').replace('x', 'Х')
        norm_name = name.replace('X', 'Х').replace('x', 'Х')
        if norm_base.lower() == norm_name.lower():
            return os.path.join(VIDEO_DIR, f)
    raise FileNotFoundError(f"Video {name} not found")

def extract_frame_at_t(cap, t_sec):
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
    ret, frame = cap.read()
    if not ret:
        return None
    return frame

print("Investigating items...")
# 1. X-13: Safe crops at 45, 50, 55, 60, 65s
v13_path = get_video_path("X-13")
cap = cv2.VideoCapture(v13_path)
h, w = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
# Safe is in top-right / right background in X-13 (Group G04 / camera looking at desk & safe)
# Let's save full frames and safe crops
safe_times = [45.0, 50.0, 55.0, 60.0, 65.0]
safe_crops_13 = []
for t in safe_times:
    frame = extract_frame_at_t(cap, t)
    if frame is not None:
        cv2.imwrite(os.path.join(OUT_DIR, f"x13_frame_{int(t)}s.jpg"), frame)
        # Let's crop around safe (let's locate safe roughly or crop several quadrants)
        # In G04, safe is roughly x: 0.6 to 0.95, y: 0.1 to 0.7
        safe_crop = frame[int(h*0.05):int(h*0.75), int(w*0.55):int(w*0.98)]
        cv2.putText(safe_crop, f"{t}s", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
        safe_crops_13.append(safe_crop)
cap.release()

if safe_crops_13:
    # Resize to same height
    target_h = 400
    resized = [cv2.resize(c, (int(c.shape[1] * target_h / c.shape[0]), target_h)) for c in safe_crops_13]
    strip_13 = np.hstack(resized)
    cv2.imwrite(os.path.join(OUT_DIR, "x13_safe_progression_45_65s.jpg"), strip_13)
    print("Saved X-13 safe progression")

# 2. X-22 vs X-23: Safe and end of video
v22_path = get_video_path("X-22")
cap22 = cv2.VideoCapture(v22_path)
h22, w22 = int(cap22.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap22.get(cv2.CAP_PROP_FRAME_WIDTH))
for t in [4.0, 8.0, 10.0, 12.0, 14.0, 15.0, 16.0, 17.0]:
    frame = extract_frame_at_t(cap22, t)
    if frame is not None:
        cv2.imwrite(os.path.join(OUT_DIR, f"x22_frame_{int(t)}s.jpg"), frame)
        # Safe crop in G05: safe is at background right / wall
        safe_crop22 = frame[int(h22*0.05):int(h22*0.65), int(w22*0.55):int(w22*0.95)]
        cv2.putText(safe_crop22, f"X-22 {t}s", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        cv2.imwrite(os.path.join(OUT_DIR, f"x22_safe_{int(t)}s.jpg"), safe_crop22)
cap22.release()

v23_path = get_video_path("X-23")
cap23 = cv2.VideoCapture(v23_path)
for t in [4.0, 8.0, 10.0, 12.0, 14.0, 16.0]:
    frame = extract_frame_at_t(cap23, t)
    if frame is not None:
        cv2.imwrite(os.path.join(OUT_DIR, f"x23_frame_{int(t)}s.jpg"), frame)
cap23.release()

# 3. X-20: phone / hands
v20_path = get_video_path("X-20")
cap20 = cv2.VideoCapture(v20_path)
h20, w20 = int(cap20.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap20.get(cv2.CAP_PROP_FRAME_WIDTH))
crops_20 = []
for t in [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]:
    frame = extract_frame_at_t(cap20, t)
    if frame is not None:
        cv2.imwrite(os.path.join(OUT_DIR, f"x20_frame_{int(t)}s.jpg"), frame)
        # In G05 / G06, cashier sits at desk: desk area y: 0.35 to 0.85, x: 0.2 to 0.75
        hands_crop = frame[int(h20*0.35):int(h20*0.85), int(w20*0.25):int(w20*0.75)]
        cv2.putText(hands_crop, f"{t}s", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        crops_20.append(hands_crop)
cap20.release()

if crops_20:
    target_h = 350
    resized = [cv2.resize(c, (int(c.shape[1] * target_h / c.shape[0]), target_h)) for c in crops_20]
    strip_20 = np.hstack(resized)
    cv2.imwrite(os.path.join(OUT_DIR, "x20_hands_phone_progression.jpg"), strip_20)
    print("Saved X-20 hands progression")

# 4. Check X-1, X-2, X-4@68, X-44
for name, times in [("X-1", [20.0, 22.0]), ("X-2", [8.0, 10.0]), ("X-4", [66.0, 68.0, 70.0]), ("X-44", [14.0, 16.0, 18.0])]:
    try:
        vp = get_video_path(name)
        c = cv2.VideoCapture(vp)
        for t in times:
            f = extract_frame_at_t(c, t)
            if f is not None:
                cv2.imwrite(os.path.join(OUT_DIR, f"{name.lower()}_frame_{int(t)}s.jpg"), f)
        c.release()
    except Exception as e:
        print(f"Error {name}: {e}")

# 5. X-34
vp34 = get_video_path("X-34")
c34 = cv2.VideoCapture(vp34)
for t in [9.0, 10.0, 11.0, 12.0, 13.0, 14.0]:
    f = extract_frame_at_t(c34, t)
    if f is not None:
        cv2.imwrite(os.path.join(OUT_DIR, f"x34_frame_{int(t)}s.jpg"), f)
c34.release()

print("Evidence generation complete.")
