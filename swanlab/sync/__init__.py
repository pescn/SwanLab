import os.path
from sys import stdout
from typing import Literal, Union

from rich.status import Status

from .mlflow import sync_mlflow
from .tensorboard import sync_tensorboardX, sync_tensorboard_torch
from .wandb import sync_wandb

__all__ = ["sync_wandb", "sync_tensorboardX", "sync_tensorboard_torch", "sync_mlflow", "sync"]

from .sync_utils import set_run_store
from ..core_python import create_client, get_client
from ..core_python.auth.providers.api_key import code_login
from ..data.porter import DataPorter, Mounter
from ..formatter import check_proj_name_format, check_run_id_format
from ..log import swanlog


def _has_swd_files(dir_path: str) -> bool:
    """检查目录下是否存在 .swd 文件"""
    data_dir = os.path.join(dir_path, "data")
    if not os.path.isdir(data_dir):
        return False
    return any(f.endswith(".swd") for f in os.listdir(data_dir))


def _sync_swd(dir_path: str, cos_client):
    """
    同步 .swd 文件到 COS（增量续传）

    读取本地 data/ 目录下的所有 .swd 文件，
    HEAD 获取远端大小，从差异处续传。
    """
    from swanlab.core_python.cos import CosAppender

    data_dir = os.path.join(dir_path, "data")
    manifest_path = os.path.join(dir_path, "manifest.json")

    swd_files = [f for f in os.listdir(data_dir) if f.endswith(".swd")]
    total_transferred = 0

    for swd_file in sorted(swd_files):
        local_path = os.path.join(data_dir, swd_file)
        appender = CosAppender(cos_client, object_key=swd_file)
        transferred = appender.sync_from_local(local_path)
        total_transferred += transferred
        swanlog.debug(f"Synced {swd_file}: {transferred} bytes")

    # 上传 manifest
    if os.path.exists(manifest_path):
        with open(manifest_path, "rb") as f:
            cos_client.put_object(key="manifest.json", data=f.read())

    return total_transferred


def sync(
    dir_path: str,
    workspace: str = None,
    project: str = None,
    id: Union[str, Literal['auto', 'new']] = None,
    api_key: str = None,
    raise_error: bool = True,
):
    """
    Syncs backup files to the cloud. Before syncing, you must log in.
    :param dir_path: The directory path to sync.
    :param id: The ID of the backup to sync. Use cases:
        - None: Equal to 'new'
        - new: Create a new experiment with a new ID.
        - auto: Create (Resume) the experiment with the ID from the backup file.
        - str: Use the specified ID to sync the logs.
    :param workspace: The workspace to sync the logs to. If not specified, it will use the default workspace.
    :param project: The project to sync the logs to. If not specified, it will use the default project.
    :param raise_error: Whether to raise an error if error occurs when syncing.
    :param api_key: If provided, swanlab will sync using this API key. Or you need login first before run this function.
        Attention: If you not provide api-key, you need login every time you run this function.
    """
    # 0. 参数检查
    # 0.1 检查项目名称
    project and check_proj_name_format(project)
    # 0.2 检查实验 ID
    if id is None:
        id = "new"
    if id not in ['new', 'auto']:
        check_run_id_format(id)
    # 1. 根据 api key 登录
    try:
        client = get_client()
    except ValueError:
        client = None
    # api key 存在，则尝试创建客户端
    if api_key:
        client = create_client(code_login(api_key, save_key=False))
    # 2. 开始同步
    try:
        assert os.path.exists(dir_path), f"Directory {dir_path} does not exist."
        stdout.flush()
        with Status("🔁 Syncing...", spinner="dots"):
            with DataPorter().open_for_sync(run_dir=dir_path) as porter:
                proj, exp = porter.parse()
                assert client is not None, "Please log in first before using sync."
                with Mounter() as mounter:
                    run_store = mounter.run_store
                    # 设置运行存储相关信息
                    set_run_store(run_store, proj, exp, project, workspace, id)
                    # 创建实验会话
                    mounter.execute()
                    # 检测是否存在 .swd 文件
                    if _has_swd_files(dir_path) and run_store.cos_config:
                        # .swd 模式：直接 COS 上传
                        from swanlab.core_python.cos import CosClient, CosConfig
                        cos_client = CosClient(CosConfig(**run_store.cos_config))
                        try:
                            _sync_swd(dir_path, cos_client)
                        finally:
                            cos_client.close()
                    # 始终走现有 backup.swanlab 同步流程（保持兼容）
                    porter.synchronize()
        swanlog.info("🚀 Sync completed, View run at ", client.web_exp_url)
    except Exception as e:
        if raise_error:
            raise e
        else:
            return swanlog.error(f"😢 Error syncing: {e}")
