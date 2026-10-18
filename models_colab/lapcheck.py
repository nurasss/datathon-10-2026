import sys, glob, cv2, numpy as np, onnxruntime as ort
so = ort.SessionOptions(); so.intra_op_num_threads = 1
s = ort.InferenceSession(sys.argv[1], so, providers=["CPUExecutionProvider"])
names = ["cash","cash_bag","phone","cup_or_food","clothing_or_bag","safe_open","safe_closed","laptop_document","laptop_lockscreen"]
for vid in sys.argv[2:]:
    fs = sorted(glob.glob(f"ds/frames/{vid}_*.jpg"))[-3:]
    for f in fs:
        img = cv2.imread(f); r = min(576/img.shape[0], 320/img.shape[1])
        p = np.full((576,320,3),114,np.uint8); rs = cv2.resize(img,(int(img.shape[1]*r),int(img.shape[0]*r))); p[:rs.shape[0],:rs.shape[1]] = rs
        o = s.run(None, {"images": p.transpose(2,0,1)[None].astype(np.float32)})[0][0]
        sc = o[:,4:5]*o[:,5:]; top = {}
        for c in range(9):
            m = sc[:,c].max()
            if m > 0.3: top[names[c]] = round(float(m),2)
        print(f.split("/")[-1], top)
