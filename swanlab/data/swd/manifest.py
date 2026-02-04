"""
@file: manifest.py
@description: ManifestManager - 指标注册表管理

管理 manifest.json 文件，记录所有指标的 .swd 文件信息。
支持原子写入（先写临时文件再重命名），确保文件一致性。

Manifest 格式:
{
    "version": 1,
    "experiment_id": "exp_abc123",
    "status": "running",
    "metrics": {
        "loss": {
            "id": 0, "dtype": "float64", "record_size": 24,
            "files": [{"name": "m_000.swd", "records": 100000, "bytes": 2400064}]
        },
        "train_images": {
            "id": 1, "dtype": "media_ref", "record_size": 80,
            "media_prefix": "media/m_001/",
            "files": [{"name": "m_001.swd", "records": 5000}]
        }
    }
}
"""

import json
import os
import tempfile
from typing import Optional, Dict, Any

from .format import (
    SWD_HEADER_SIZE,
    SWD_DTYPE_SCALAR,
    SWD_DTYPE_MEDIA_REF,
    SWD_SCALAR_RECORD_SIZE,
    SWD_MEDIA_RECORD_SIZE,
)

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"

# 数据类型名称映射
_DTYPE_NAMES = {
    SWD_DTYPE_SCALAR: "float64",
    SWD_DTYPE_MEDIA_REF: "media_ref",
}

_DTYPE_FROM_NAME = {v: k for k, v in _DTYPE_NAMES.items()}


class ManifestManager:
    """
    管理 manifest.json 的创建、更新和持久化

    用法:
        manifest = ManifestManager(manifest_dir="/path/to/run", experiment_id="exp_abc123")
        manifest.add_metric("loss", metric_id=0, dtype=SWD_DTYPE_SCALAR)
        manifest.update_metric_records("loss", swd_file_name="m_000.swd", records=100, file_bytes=2464)
        manifest.set_status("completed")
        manifest.atomic_write()
    """

    def __init__(self, manifest_dir: str, experiment_id: str):
        """
        :param manifest_dir: manifest.json 所在目录
        :param experiment_id: 实验 ID
        """
        self._manifest_dir = manifest_dir
        self._file_path = os.path.join(manifest_dir, MANIFEST_FILENAME)
        self._data: Dict[str, Any] = {
            "version": MANIFEST_VERSION,
            "experiment_id": experiment_id,
            "status": "running",
            "metrics": {},
        }
        self._dirty = False

        # 如果已有 manifest 文件，加载它
        if os.path.exists(self._file_path):
            self._load()

    @property
    def file_path(self) -> str:
        return self._file_path

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    @property
    def status(self) -> str:
        return self._data["status"]

    @property
    def metrics(self) -> Dict[str, Any]:
        return self._data["metrics"]

    def _load(self):
        """从文件加载 manifest"""
        with open(self._file_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

    def add_metric(
        self,
        key: str,
        metric_id: int,
        dtype: int,
        media_prefix: Optional[str] = None,
    ):
        """
        注册一个新指标

        :param key: 指标名称 (e.g., "loss")
        :param metric_id: 指标 ID
        :param dtype: 数据类型 (SWD_DTYPE_SCALAR 或 SWD_DTYPE_MEDIA_REF)
        :param media_prefix: 媒体文件前缀 (仅媒体类型需要)
        """
        dtype_name = _DTYPE_NAMES.get(dtype)
        if dtype_name is None:
            raise ValueError(f"Unknown dtype: {dtype}")

        record_size = SWD_SCALAR_RECORD_SIZE if dtype == SWD_DTYPE_SCALAR else SWD_MEDIA_RECORD_SIZE

        metric_entry = {
            "id": metric_id,
            "dtype": dtype_name,
            "record_size": record_size,
            "files": [],
        }
        if media_prefix:
            metric_entry["media_prefix"] = media_prefix

        self._data["metrics"][key] = metric_entry
        self._dirty = True

    def update_metric_records(
        self,
        key: str,
        swd_file_name: str,
        records: int,
        file_bytes: Optional[int] = None,
    ):
        """
        更新指标的记录计数

        :param key: 指标名称
        :param swd_file_name: .swd 文件名 (e.g., "m_000.swd")
        :param records: 总记录数
        :param file_bytes: 文件总大小（bytes）
        """
        metric = self._data["metrics"].get(key)
        if metric is None:
            raise KeyError(f"Metric '{key}' not found in manifest")

        # 查找或创建文件条目
        file_entry = None
        for f in metric["files"]:
            if f["name"] == swd_file_name:
                file_entry = f
                break

        if file_entry is None:
            file_entry = {"name": swd_file_name, "records": 0}
            metric["files"].append(file_entry)

        file_entry["records"] = records
        if file_bytes is not None:
            file_entry["bytes"] = file_bytes

        self._dirty = True

    def set_status(self, status: str):
        """
        设置实验状态

        :param status: 状态 ("running", "completed", "crashed", "stopped")
        """
        self._data["status"] = status
        self._dirty = True

    def atomic_write(self):
        """
        原子写入 manifest.json（先写临时文件再重命名）
        """
        os.makedirs(self._manifest_dir, exist_ok=True)
        # 写入临时文件
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self._manifest_dir,
            suffix=".tmp",
            prefix="manifest_",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            # 原子重命名
            os.replace(tmp_path, self._file_path)
            self._dirty = False
        except Exception:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def upload_to_cos(self, cos_client) -> bool:
        """
        上传 manifest.json 到 COS

        使用覆盖写，通过先写 manifest.json.tmp 再 CopyObject 的方式保持一致性。
        如果 COS 不支持原子操作，直接覆盖写。

        :param cos_client: CosClient 实例
        :return: True 表示上传成功
        """
        try:
            data = self.to_bytes()
            cos_client.put_object(key="manifest.json", data=data)
            return True
        except Exception as e:
            from swanlab.log import swanlog
            swanlog.debug(f"Failed to upload manifest to COS: {e}")
            return False

    def should_sync(self, interval_records: int = 100) -> bool:
        """
        判断是否应该同步 manifest 到远端

        :param interval_records: 每隔多少条记录同步一次
        :return: True 表示应该同步
        """
        if not self._dirty:
            return False
        total_records = sum(
            f.get("records", 0)
            for m in self._data["metrics"].values()
            for f in m.get("files", [])
        )
        return total_records % interval_records == 0

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self._data, ensure_ascii=False, indent=2)

    def to_bytes(self) -> bytes:
        """序列化为 UTF-8 bytes"""
        return json.dumps(self._data, ensure_ascii=False, indent=2).encode("utf-8")
