import argparse
import os
import os.path as osp
from datetime import timedelta
import time
import sys
import random

import easydict
import numpy as np
import yaml


# import torch
# import torch.utils.data as data
# import torchvision.transforms as transforms
# from torch.backends import cudnn

import mindspore as ms
import mindspore.nn as nn
import mindspore.dataset as ds
import mindspore.dataset.vision as vision

from mindspore import context, load_checkpoint, load_param_into_net, save_checkpoint, DatasetHelper, Tensor
from mindspore.context import ParallelMode
from mindspore.communication import init, get_group_size, get_rank
from mindspore.dataset.transforms.transforms import Compose
from mindspore.nn import SGD, Adam

from train.train_2stage import do_train_stage2
from util.loss.make_loss import make_loss
from train.train_1stage import do_train_stage1
from util.make_optimizer import make_optimizer_1stage, make_optimizer_2stage
from util.optim.lr_scheduler import WarmupMultiStepLR
from util.optim.scheduler_factory import create_scheduler
from data.dataloader import Unlabeld_SYSUData_Pseudo, Unlabeld_SYSUData, Unlabeld_RegDBData
from model.make_model_clip import build_model
from util.utils import Logger

start_epoch = best_mAP = 0


def decode(img):
    return Image.fromarray(img)

def main(args):

    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)

    main_worker(args)


def main_worker(args):
    global start_epoch, best_mAP
    start_time = time.monotonic()
    # cudnn.benchmark = True

    logs_time = args.logs_time
    logs_file = str(args.logs_file)

    sys.stdout = Logger(osp.join(args.logs_dir, logs_time, logs_file + '.txt'))

    print("==========\nArgs:{}\n==========".format(args))
    # Load datasets
    transform_test = Compose(
        [
            decode,
            vision.Resize((args.img_h, args.img_w)),
            vision.ToTensor(),
            vision.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225], is_hwc=False),
        ]
    )
    best_acc = 0
    end = time.time()
    print("==> Load unlabeled dataset")
    if args.dataset == 'sysu':
        data_path = '/home/wyb/Dataset/SYSU-MM01/'
        unlabel_dataset = Unlabeld_SYSUData_Pseudo(data_dir=data_path, pseudo_dir=args.pseudo_labels_path, rgb_cluster=False, ir_cluster=False)
    else:
        data_path = 'D:/cclnet/cclnet/RegDB/'
        unlabel_dataset = Unlabeld_RegDBData(data_dir=data_path, rgb_cluster=False, ir_cluster = False)
    if -1 in unlabel_dataset.train_color_label:
        n_color_class = len(np.unique(unlabel_dataset.train_color_label)) - 1
    else:
        n_color_class = len(np.unique(unlabel_dataset.train_color_label)) 
    if -1 in unlabel_dataset.train_thermal_label:
        n_thermal_class = len(np.unique(unlabel_dataset.train_thermal_label)) - 1
    else:
        n_thermal_class = len(np.unique(unlabel_dataset.train_thermal_label))
    # num_classes = n_color_class + n_thermal_class

    print("Dataset {} Statistics:".format(args.dataset))
    print("  ----------------------------")
    print("  subset   | # ids | # images")
    print("  ----------------------------")
    print("  visible  | {:5d} | {:8d}".format(n_color_class, len(unlabel_dataset.train_color_image)))
    print("  thermal  | {:5d} | {:8d}".format(n_thermal_class, len(unlabel_dataset.train_thermal_image)))
    print("  ----------------------------")
    print("  ----------------------------")
    print("Data loading time:\t {:.3f}".format(time.time() - end))

    # Create model
    model = build_model(args, n_color_class, n_thermal_class)
    # model.cuda()

    # Optimizer
    optimizer_1stage = make_optimizer_1stage(args, model)
    scheduler_1stage = create_scheduler(optimizer_1stage, num_epochs=args.stage1_maxepochs, lr_min=args.stage1_lrmin,
                                           warmup_lr_init=args.stage1_warmup_lrinit, warmup_t=args.stage1_warmup_epoch, noise_range=None)
    do_train_stage1(args, unlabel_dataset, model, optimizer_1stage, scheduler_1stage)

    optimizer_2stage = make_optimizer_2stage(args, model)
    scheduler_2stage = WarmupMultiStepLR(optimizer_2stage, args.stage2_steps, args.stage2_gamma, args.stage2_warmup_factor,
                                         args.stage2_warmup_iters, args.stage2_warmup_method)

    loss_func_rgb = make_loss(num_classes=n_color_class)
    loss_func_ir = make_loss(num_classes=n_thermal_class)

    len_clusters_rgb, len_clusters_ir = do_train_stage2(args, unlabel_dataset, model, optimizer_2stage, scheduler_2stage, loss_func_rgb, loss_func_ir)

    end_time = time.monotonic()
    print('Total running time: ', timedelta(seconds=end_time - start_time))



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Contrastive learning on unsupervised Cross re-ID")
    args_main = parser.parse_args()

    args = yaml.load(open('config/config_sysu.yaml'), Loader=yaml.FullLoader)
    args = easydict.EasyDict(args)
    main(args)