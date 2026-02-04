"""
@author: cunyue
@file: local.py
@time: 2025/2/13 14:59
@description: 本地模式下的回调
local模式（目前）将自动调用swanboard，如果不存在则报错
"""

import random

from swanlab.env import SwanLabEnv
from swanlab.log.type import LogData
from swanlab.toolkit import ColumnInfo
from . import utils
from .. import namer as N
from ..namer import generate_colors
from ..run import get_run
from ..store import get_run_store
from ...log import swanlog

try:
    # noinspection PyPackageRequirements
    import swanboard
except ImportError as e:
    raise ImportError("Please install swanboard to use 'local' mode: pip install 'swanlab[dashboard]'")

from importlib.metadata import version

package_version = version("swanboard")
if package_version != "0.1.9b1":
    raise ImportError(
        "Your swanboard version does not match, please use this command to install the matching version: pip install 'swanlab[dashboard]'"
    )


import json
import os
from datetime import datetime
from typing import Tuple, Optional, TextIO
from swanlab.swanlab_settings import get_settings
from swanlab.toolkit import RuntimeInfo, MetricInfo
from swanlab.data.callbacker.callback import SwanLabRunCallback


class LocalRunCallback(SwanLabRunCallback):

    def __init__(self) -> None:
        super().__init__()
        self.board = swanboard.SwanBoardCallback()
        # 当前日志写入文件的句柄
        self.file: Optional[TextIO] = None
        # .swd manifest 管理器（可选）
        self._manifest = None

    def __str__(self):
        return "SwanLabLocalRunCallback"

    def _terminal_handler(self, log_data: LogData):
        log_name = f"{datetime.now().strftime('%Y-%m-%d')}.log"
        if self.file is None:
            # 如果句柄不存在，则创建
            self.file = open(os.path.join(self.run_store.console_dir, log_name), "a", encoding="utf-8")
        elif os.path.basename(self.file.name) != log_name:
            # 如果句柄存在，但是文件名不一样，则关闭句柄，重新打开
            self.file.close()
            self.file = open(os.path.join(self.run_store.console_dir, log_name), "a", encoding="utf-8")
        # 写入日志
        for content in log_data["contents"]:
            self.file.write(content['message'] + '\n')
            self.file.flush()
        self.porter.trace_log(log_data)

    def on_init(self, proj_name: str, workspace: str, public: bool = None, logdir: str = None, *args, **kwargs):
        if logdir is not None:
            os.environ[SwanLabEnv.SWANLOG_FOLDER.value] = logdir
        self.board.on_init(proj_name)
        run_store = self.run_store
        run_store.project = proj_name
        run_store.workspace = workspace
        run_store.visibility = public
        run_store.tags = [] if run_store.tags is None else run_store.tags
        exp_count = random.randint(0, 20)
        run_store.run_name = N.generate_name(exp_count) if run_store.run_name is None else run_store.run_name
        run_store.run_colors = generate_colors(random.randint(0, 20))
        run_store.run_id = N.generate_run_id()
        run_store.new = True

    def before_init_experiment(
        self,
        run_id: str,
        exp_name: str,
        description: str,
        colors: Tuple[str, str],
        *args,
        **kwargs,
    ):
        #  FIXME num 在 dashboard 中被要求传递但是没用上 🤡
        self.board.before_init_experiment(
            os.path.basename(self.run_store.run_dir), exp_name, description, colors=colors, num=1
        )

    def on_run(self, *args, **kwargs):
        self.porter.open_for_trace(backend='none', sync=True)
        self._start_terminal_proxy(handler=self._terminal_handler)
        self._register_sys_callback()
        # 初始化 .swd manifest（如果启用）
        if get_settings().use_swd_format:
            from swanlab.data.swd.manifest import ManifestManager
            self._manifest = ManifestManager(
                manifest_dir=self.run_store.run_dir,
                experiment_id=self.run_store.run_id or "unknown",
            )
            self._manifest.atomic_write()
        utils.print_train_begin(self.run_store.run_dir)
        utils.print_watch(self.run_store.swanlog_dir)

    def on_runtime_info_update(self, r: RuntimeInfo, *args, **kwargs):
        # 更新运行时信息
        self.porter.trace_runtime_info(r)

    def on_log(self, *args, **kwargs):
        self.board.on_log(*args, **kwargs)

    def on_column_create(self, column_info: ColumnInfo, *args, **kwargs):
        self.porter.trace_column(column_info)
        # 注册 .swd manifest 中的指标
        if self._manifest is not None and column_info.error is None:
            from swanlab.data.swd.format import SWD_DTYPE_SCALAR, SWD_DTYPE_MEDIA_REF
            chart_type = column_info.chart_type.value.chart_type
            dtype = SWD_DTYPE_SCALAR if chart_type == "line" else SWD_DTYPE_MEDIA_REF
            media_prefix = f"media/m_{column_info.kid}/" if dtype == SWD_DTYPE_MEDIA_REF else None
            self._manifest.add_metric(
                key=column_info.key,
                metric_id=int(column_info.kid),
                dtype=dtype,
                media_prefix=media_prefix,
            )
            self._manifest.atomic_write()
        # 屏蔽 board 不支持的图表类型和列类型
        if column_info.chart_type.value.chart_type not in ["line", "image", "audio", "text"]:
            return
        if column_info.cls != "CUSTOM":
            return
        self.board.on_column_create(column_info)

    def on_metric_create(self, metric_info: MetricInfo, *args, **kwargs):
        # 对于指标保存，可以随意保存，因为这里与 dashboard 没有直接交互
        # 出现任何错误直接返回
        if metric_info.error:
            return
        # ---------------------------------- 保存指标数据（JSONL，现有格式） ----------------------------------
        os.makedirs(os.path.dirname(metric_info.metric_file_path), exist_ok=True)
        os.makedirs(os.path.dirname(metric_info.summary_file_path), exist_ok=True)
        with open(metric_info.summary_file_path, "w+", encoding="utf-8") as f:
            f.write(json.dumps(metric_info.metric_summary, ensure_ascii=False))
        with open(metric_info.metric_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(metric_info.metric, ensure_ascii=False) + "\n")
        # ---------------------------------- 更新 .swd manifest ----------------------------------
        if self._manifest is not None and metric_info.swd_bytes is not None:
            key = metric_info.column_info.key
            kid = metric_info.column_info.kid
            swd_file_name = f"m_{int(kid):03d}.swd"
            self._manifest.update_metric_records(
                key=key,
                swd_file_name=swd_file_name,
                records=metric_info.metric_epoch,
            )
            # 每 100 条记录写入一次 manifest，减少 IO
            if metric_info.metric_epoch % 100 == 0:
                self._manifest.atomic_write()
        # ---------------------------------- 保存媒体字节流数据 ----------------------------------
        self.porter.trace_metric(metric_info)

    def on_stop(self, error: str = None, *args, **kwargs):
        """
        训练结束，取消系统回调
        此函数被`run.finish`调用
        """

        # 写入错误信息
        if error is not None:
            with open(os.path.join(get_run_store().console_dir, "error.log"), "a") as fError:
                print(datetime.now(), file=fError)
                print(error, file=fError)
        # 最终写入 manifest
        if self._manifest is not None:
            self._manifest.set_status("crashed" if error else "completed")
            self._manifest.atomic_write()
        self.board.on_stop(error)
        # 打印信息
        utils.print_watch(self.run_store.swanlog_dir)
        self._unregister_sys_callback()
        self.porter.close_trace(success=get_run().success, error=error, epoch=swanlog.epoch + 1)
