"""
Docs: https://docs.swanlab.cn/zh/guide_cloud/integration/integration-ultralytics.html

For adaptation to the ultralytics framework. Detailed usage are as follows:
------train.py in ultralytics------
from ultralytics import YOLO
from swanlab.integration.ultralytics import add_swanlab_callback

model = YOLO("yolov5n.yaml")
add_swanlab_callback(model)

model.train(
    data="coco.yaml",
    epoch=50,
)
---------------------------------
"""

from ultralytics.models import YOLO
from ultralytics.utils.torch_utils import model_info_for_loggers
import swanlab

_processed_plots = {}


def _log_plots(plots: dict, step: int, tag: str):
    """记录指标绘图和推理图像"""
    image_list = []
    for (
        name,
        params,
    ) in plots.copy().items():
        timestamp = params["timestamp"]
        if _processed_plots.get(name) != timestamp:
            image_list.append(swanlab.Image(str(name), caption=name.stem))
            _processed_plots[name] = timestamp

    if image_list:
        _RUN.log({tag: image_list}, step=step)


def on_pretrain_routine_start(trainer):
    """初始化实验记录器"""
    if swanlab.get_run() is None:
        global _RUN
        _RUN = swanlab.init(
            project=trainer.args.project or "YOLOv8",
            experiment_name=trainer.args.name,
            config=vars(trainer.args),
        )
    else:
        _RUN = swanlab.get_run()
        _RUN.config.update(vars(trainer.args))


def on_fit_epoch_end(trainer):
    """每个epoch结束记录指标和绘图（含训练和验证）"""
    swanlab.log(trainer.metrics, step=trainer.epoch + 1)
    _log_plots(trainer.plots, step=trainer.epoch + 1, tag="Train/Plots")
    _log_plots(trainer.validator.plots, step=trainer.epoch + 1, tag="Train/ValPlots")

    if trainer.epoch == 0:
        _RUN.log(model_info_for_loggers(trainer), step=trainer.epoch + 1)


def on_train_epoch_end(trainer):
    """每个epoch结束记录指标（仅训练）"""
    swanlab.log(trainer.label_loss_items(trainer.tloss, prefix="train"), step=trainer.epoch + 1)
    swanlab.log(trainer.lr, step=trainer.epoch + 1)

    if trainer.epoch == 1:
        _log_plots(trainer.plots, step=trainer.epoch + 1, tag="Plots")


def on_train_end(trainer):
    """结束训练"""
    _log_plots(trainer.plots, step=trainer.epoch + 1, tag="TrainEnd/Plots")
    _log_plots(trainer.validator.plots, step=trainer.epoch + 1, tag="TrainEnd/ValPlots")
    _RUN.finish()


def add_swanlab_callback(
    model: YOLO,
):
    """给Ultralytics模型添加swanlab回调函数"""
    callbacks = {
        "on_pretrain_routine_start": on_pretrain_routine_start,
        "on_fit_epoch_end": on_fit_epoch_end,
        "on_train_epoch_end": on_train_epoch_end,
        "on_train_end": on_train_end,
    }

    for event, callback_fn in callbacks.items():
        model.add_callback(event, callback_fn)

    return model