"""
@DATE: 2024/5/5 20:22
@File: callback_cloud.py
@IDE: pycharm
@Description:
    云端回调
"""

import shutil
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Optional

from rich.status import Status
from rich.text import Text

from swanlab.core_python import auth
from swanlab.data.callbacker.callback import SwanLabRunCallback
from swanlab.env import in_jupyter
from swanlab.log import swanlog
from swanlab.toolkit import (
    RuntimeInfo,
    MetricInfo,
    ColumnInfo,
)
from . import utils as U
from ..porter import Mounter
from ..run import get_run
from ...core_python import *
from ...core_python.api.experiment import update_experiment_state
from ...core_python.utils.timer import Timer
from ...log.type import LogData


class CloudPyCallback(SwanLabRunCallback):
    login_info: Optional[auth.LoginInfo] = None

    def __init__(self):
        super().__init__()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.heartbeat: Optional[Timer] = None
        # COS 直接上传相关（Phase 3）
        self._cos_client = None
        self._cos_appenders: dict = {}
        self._manifest = None
        self._manifest_sync_timer: Optional[Timer] = None

    def __str__(self):
        return "SwanLabCloudPyCallback"

    @staticmethod
    def _create_client():
        try:
            http = get_client()
        except ValueError:
            swanlog.debug("Login info is None, get login info.")
            login_info = CloudPyCallback.login_info
            if login_info is not None:
                # 如果有登录信息，则使用该信息创建客户端
                http = create_client(login_info)
                CloudPyCallback.login_info = None
            else:
                # 如果没有登录信息，则需要用户登录
                # 但是不保存登录信息到本地
                http = create_client(auth.create_login_info(save=False))
        return http

    @staticmethod
    def _converter_summarise_metric():
        pass

    def on_init(self, *args, **kwargs):
        self._create_client()
        # 检测是否有最新的版本
        U.check_latest_version()
        # 挂载项目、实验
        with Status("Creating experiment...", spinner="dots"):
            with Mounter() as mounter:
                mounter.execute()
        # 创建客户端心跳
        self.heartbeat = create_client_heartbeat()

    def _terminal_handler(self, log_data: LogData):
        self.porter.trace_log(log_data)

    def _init_cos_direct(self):
        """初始化 COS 直接上传（当 use_cos_direct 和 use_swd_format 同时启用时）"""
        if not self.user_settings.use_cos_direct or not self.user_settings.use_swd_format:
            return
        try:
            from swanlab.core_python.cos import CosClient, CosConfig
            from swanlab.data.swd.manifest import ManifestManager

            cos_config = self.run_store.cos_config
            if cos_config is None:
                swanlog.debug("COS config not available, skipping COS direct upload initialization")
                return

            self._cos_client = CosClient(CosConfig(**cos_config))
            self._manifest = ManifestManager(
                manifest_dir=self.run_store.run_dir,
                experiment_id=self.run_store.run_id or "unknown",
            )
            self._manifest.atomic_write()
            # 定期同步 manifest 到 COS（每 30 秒）
            self._manifest_sync_timer = Timer(
                task=self._sync_manifest_to_cos,
                interval=30,
            )
            self._manifest_sync_timer.start()
            swanlog.debug("COS direct upload initialized")
        except Exception as e:
            swanlog.debug(f"Failed to initialize COS direct upload: {e}")

    def _sync_manifest_to_cos(self):
        """定期将 manifest 同步到 COS"""
        if self._manifest is None or self._cos_client is None:
            return
        try:
            self._manifest.atomic_write()
            self._manifest.upload_to_cos(self._cos_client)
        except Exception as e:
            swanlog.debug(f"Periodic manifest sync failed: {e}")

    def on_run(self, *args, **kwargs):
        self.porter.open_for_trace(sync=False)
        # 初始化 COS 直接上传
        self._init_cos_direct()
        # 注册终端代理和系统回调
        self._start_terminal_proxy()
        self._register_sys_callback()
        # 打印实验开始信息，在 cloud 模式下如果没有开启 backup 的话不打印"数据保存在 xxx"的信息
        U.print_train_begin(run_dir=self.run_store.run_dir)
        http = get_client()
        swanlog.info("👋 Hi ", Text(http.username, "bold default"), ",welcome to swanlab!", sep="")
        swanlog.info("Syncing run", Text(self.run_store.run_name, "yellow"), "to the cloud")
        experiment_url = U.print_cloud_web()
        # 在Jupyter Notebook环境下，显示按钮
        if in_jupyter():
            U.show_button_html(experiment_url)

    def on_runtime_info_update(self, r: RuntimeInfo, *args, **kwargs):
        self.porter.trace_runtime_info(r)

    def on_column_create(self, column_info: ColumnInfo, *args, **kwargs):
        self.porter.trace_column(column_info)
        # COS 模式：为新指标创建 CosAppender + 更新 manifest
        if self._cos_client is not None and column_info.error is None:
            try:
                from swanlab.core_python.cos import CosAppender
                from swanlab.data.swd.format import SWD_DTYPE_SCALAR, SWD_DTYPE_MEDIA_REF, SWD_HEADER_SIZE
                from swanlab.data.swd import SwdHeader, SWD_SCALAR_RECORD_SIZE, SWD_MEDIA_RECORD_SIZE

                kid = column_info.kid
                chart_type = column_info.chart_type.value.chart_type
                dtype = SWD_DTYPE_SCALAR if chart_type == "line" else SWD_DTYPE_MEDIA_REF
                record_size = SWD_SCALAR_RECORD_SIZE if dtype == SWD_DTYPE_SCALAR else SWD_MEDIA_RECORD_SIZE
                swd_file_name = f"m_{int(kid):03d}.swd"

                # 创建 CosAppender
                appender = CosAppender(self._cos_client, object_key=swd_file_name)
                # 创建远端 .swd 文件头
                experiment_id = (self.run_store.run_id or "unknown").encode("utf-8")[:16]
                header_bytes = SwdHeader.pack(
                    data_type=dtype,
                    record_size=record_size,
                    experiment_id=experiment_id,
                    metric_id=int(kid),
                )
                appender.create_with_header(header_bytes)
                self._cos_appenders[kid] = appender

                # 更新 manifest
                if self._manifest is not None:
                    media_prefix = f"media/m_{kid}/" if dtype == SWD_DTYPE_MEDIA_REF else None
                    self._manifest.add_metric(
                        key=column_info.key,
                        metric_id=int(kid),
                        dtype=dtype,
                        media_prefix=media_prefix,
                    )
            except Exception as e:
                swanlog.debug(f"Failed to create COS appender for metric {kid}: {e}")

    def on_metric_create(self, metric_info: MetricInfo, *args, **kwargs):
        # 有错误就不上传
        if metric_info.error:
            return
        # COS 直接上传 .swd 数据
        if self._cos_appenders and metric_info.swd_bytes:
            self.porter.trace_metric_swd(metric_info, self._cos_appenders)
        # 传统 REST API 上传（保持兼容）
        self.porter.trace_metric(metric_info)

    def on_stop(self, error: str = None, *args, **kwargs):
        # 删除心跳
        if self.heartbeat:
            self.heartbeat.cancel()
            self.heartbeat.join()
        # 删除终端代理和系统回调
        success = get_run().success
        # FIXME 等合并 swankit 以后优化一下 interrupt 的传递问题
        interrupt = kwargs.get("interrupt", False)
        http = get_client()
        if http.pending:
            swanlog.warning("This run was destroyed but it is pending!")
        # 停止 manifest 定期同步
        if self._manifest_sync_timer is not None:
            self._manifest_sync_timer.cancel()
            self._manifest_sync_timer.join()
        # 最终 manifest 上传到 COS
        if self._manifest is not None:
            try:
                self._manifest.set_status("crashed" if error else "completed")
                self._manifest.atomic_write()
                if self._cos_client is not None:
                    self._cos_client.put_object(
                        key="manifest.json",
                        data=self._manifest.to_bytes(),
                    )
            except Exception as e:
                swanlog.debug(f"Failed to upload final manifest: {e}")
        # 关闭 COS 客户端
        if self._cos_client is not None:
            self._cos_client.close()
            self._cos_client = None
        # 打印信息
        U.print_cloud_web()
        error_epoch = swanlog.epoch + 1
        self._unregister_sys_callback()
        self.porter.close_trace(success, error=error, epoch=error_epoch)
        # 更新实验状态，在此之后实验会话关闭
        update_experiment_state(
            http,
            username=http.groupname,
            projname=http.projname,
            cuid=http.exp_id,
            state="FINISHED" if success else "ABORTED" if interrupt else "CRASHED",
        )
        reset_client()
        if not self.user_settings.backup:
            shutil.rmtree(self.run_store.run_dir, ignore_errors=True)
