import os

import torch.nn as nn

from yolox.exp import Exp as MyExp


class Exp(MyExp):
    def __init__(self):
        super().__init__()
        self.depth = 0.33
        self.width = 0.25
        # (height, width): portrait frames 464x848 -> 320x576 keeps detail; landscape export swaps it
        h, w = (int(v) for v in os.environ.get("FV_SIZE", "576,320").split(","))
        self.input_size = (h, w)
        self.test_size = (h, w)
        self.multiscale_range = 0
        self.mosaic_scale = (0.5, 1.5)
        self.mosaic_prob = 0.5
        self.enable_mixup = False
        self.num_classes = 9
        self.data_dir = os.environ.get("FV_COCO", "/content/coco")
        self.train_ann = os.environ.get("FV_TRAIN_ANN", "train.json")
        self.val_ann = os.environ.get("FV_VAL_ANN", "val.json")
        self.max_epoch = int(os.environ.get("FV_EPOCHS", "60"))
        self.no_aug_epochs = 8
        self.warmup_epochs = 2
        self.eval_interval = 5
        self.print_interval = 10
        self.data_num_workers = 2
        self.exp_name = os.environ.get("FV_EXP_NAME", "fv_yolox_nano")

    def get_model(self, sublinear=False):
        def init_yolo(M):
            for m in M.modules():
                if isinstance(m, nn.BatchNorm2d):
                    m.eps = 1e-3
                    m.momentum = 0.03

        if "model" not in self.__dict__:
            from yolox.models import YOLOX, YOLOPAFPN, YOLOXHead
            in_channels = [256, 512, 1024]
            backbone = YOLOPAFPN(self.depth, self.width, in_channels=in_channels, act=self.act, depthwise=True)
            head = YOLOXHead(self.num_classes, self.width, in_channels=in_channels, act=self.act, depthwise=True)
            self.model = YOLOX(backbone, head)
        self.model.apply(init_yolo)
        self.model.head.initialize_biases(1e-2)
        return self.model
