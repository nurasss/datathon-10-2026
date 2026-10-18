import os, sys, torch
sys.path.insert(0, "/content/YOLOX")
from yolox.exp import get_exp
from yolox.models.network_blocks import SiLU
from yolox.utils import replace_module
name = os.environ.get("FV_EXP_NAME", "smoke"); ck = os.environ.get("CKPT", "best_ckpt.pth")
os.environ["FV_EXP_NAME"] = name
exp = get_exp("/content/YOLOX/exps/" + os.environ.get("EXP_FILE", "fv_yolox_nano.py"), None)
model = exp.get_model()
state = torch.load(f"/content/YOLOX/YOLOX_outputs/{name}/{ck}", map_location="cpu", weights_only=False)
model.load_state_dict(state["model"]); model.eval()
model = replace_module(model, torch.nn.SiLU, SiLU)
model.head.decode_in_inference = True
x = torch.zeros(1, 3, *exp.test_size)
out = f"/content/{name}.onnx"
torch.onnx.export(model, x, out, input_names=["images"], output_names=["output"], opset_version=17, dynamo=False)
import onnx; m = onnx.load(out); onnx.checker.check_model(m)
print("ok", out, os.path.getsize(out), "epoch", state.get("start_epoch"), "best_ap", state.get("best_ap"))
