"""
@file: converter.py
@description: 格式转换工具

提供以下转换器:
- BackupToSwdConverter: 读 backup.swanlab → 提取 Scalar/Media → 写 .swd
- JsonlToSwdConverter: 读 JSONL 日志文件 → 写 .swd
- SwdExporter: .swd → CSV/Parquet/JSONL
"""

import csv
import json
import os
import time
from typing import Optional, List, Dict, Any

from .format import (
    SWD_HEADER_SIZE,
    SWD_DTYPE_SCALAR,
    SWD_DTYPE_MEDIA_REF,
    SWD_SCALAR_RECORD_SIZE,
    SWD_MEDIA_RECORD_SIZE,
    SwdHeader,
    ScalarRecord,
    MediaIndexRecord,
)
from .writer import SwdWriter
from .reader import SwdReader
from .manifest import ManifestManager

from swanlab.log import swanlog


class BackupToSwdConverter:
    """
    将 backup.swanlab 中的数据转换为 .swd 格式

    用法:
        converter = BackupToSwdConverter(run_dir="/path/to/run")
        converter.convert()
    """

    def __init__(self, run_dir: str):
        """
        :param run_dir: 运行目录（包含 backup.swanlab）
        """
        self._run_dir = run_dir
        self._backup_file = os.path.join(run_dir, "backup.swanlab")
        self._swd_dir = os.path.join(run_dir, "data")
        self._writers: Dict[str, SwdWriter] = {}
        self._metric_ids: Dict[str, int] = {}

    def convert(self) -> Dict[str, int]:
        """
        执行转换

        :return: {metric_key: record_count} 字典
        """
        from swanlab.data.porter.datastore import DataStore
        from swanlab.proto.v0 import BaseModel, Scalar, Media, Column

        if not os.path.exists(self._backup_file):
            raise FileNotFoundError(f"Backup file not found: {self._backup_file}")

        os.makedirs(self._swd_dir, exist_ok=True)

        ds = DataStore()
        ds.open_for_scan(self._backup_file)

        experiment_id = b'\x00' * 16
        results = {}

        try:
            for record_str in ds:
                try:
                    record = BaseModel.from_record(record_str)
                except Exception:
                    continue

                if isinstance(record, Column):
                    key = record.key
                    metric_id = len(self._metric_ids)
                    self._metric_ids[key] = metric_id
                elif isinstance(record, Scalar):
                    key = record.key
                    if key not in self._metric_ids:
                        metric_id = len(self._metric_ids)
                        self._metric_ids[key] = metric_id
                    metric_id = self._metric_ids[key]

                    if key not in self._writers:
                        swd_path = os.path.join(self._swd_dir, f"m_{metric_id:03d}.swd")
                        writer = SwdWriter(swd_path, SWD_DTYPE_SCALAR, experiment_id, metric_id)
                        writer.open()
                        self._writers[key] = writer

                    writer = self._writers[key]
                    ts = int(time.time() * 1_000_000)
                    try:
                        step = record.step
                        value = float(record.data)
                        writer.append_scalar(step, value, ts)
                    except (ValueError, TypeError):
                        pass
        finally:
            for key, writer in self._writers.items():
                results[key] = writer.record_count
                writer.close()
            ds.close()

        # 生成 manifest
        manifest = ManifestManager(self._run_dir, experiment_id="converted")
        for key, metric_id in self._metric_ids.items():
            manifest.add_metric(key, metric_id, SWD_DTYPE_SCALAR)
            if key in results:
                swd_file_name = f"m_{metric_id:03d}.swd"
                manifest.update_metric_records(key, swd_file_name, results[key])
        manifest.set_status("completed")
        manifest.atomic_write()

        return results


class JsonlToSwdConverter:
    """
    将 JSONL 日志文件转换为 .swd 格式

    JSONL 格式（每行一个 JSON 对象）:
    {"index": 0, "data": 0.5, "create_time": "..."}
    {"index": 1, "data": 0.3, "create_time": "..."}

    用法:
        converter = JsonlToSwdConverter(
            input_path="logs/0/1000.log",
            output_path="data/m_000.swd",
            experiment_id=b"\\x00" * 16,
            metric_id=0,
        )
        count = converter.convert()
    """

    def __init__(
        self,
        input_path: str,
        output_path: str,
        experiment_id: bytes,
        metric_id: int,
    ):
        self._input_path = input_path
        self._output_path = output_path
        self._experiment_id = experiment_id
        self._metric_id = metric_id

    def convert(self) -> int:
        """
        执行转换

        :return: 转换的记录数
        """
        writer = SwdWriter(self._output_path, SWD_DTYPE_SCALAR, self._experiment_id, self._metric_id)
        writer.open()
        count = 0

        try:
            with open(self._input_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        # 支持两种 key 格式：
                        # - 原始 JSONL 日志: {"index": ..., "data": ...}
                        # - SwdExporter 导出: {"step": ..., "value": ..., "timestamp": ...}
                        if "step" in record:
                            step = int(record["step"])
                        else:
                            step = int(record.get("index", count))
                        data = record.get("value", record.get("data"))
                        if isinstance(data, (int, float)):
                            value = float(data)
                        else:
                            continue
                        # 优先使用记录中的 timestamp，否则使用当前时间
                        ts = int(record.get("timestamp", time.time() * 1_000_000))
                        writer.append_scalar(step, value, ts)
                        count += 1
                    except (json.JSONDecodeError, ValueError, TypeError):
                        continue
        finally:
            writer.close()

        return count


class SwdExporter:
    """
    将 .swd 文件导出为其他格式

    支持:
    - CSV
    - JSONL
    - Parquet (需要 pyarrow)

    用法:
        exporter = SwdExporter("data/m_000.swd")
        exporter.to_csv("output/loss.csv")
        exporter.to_jsonl("output/loss.jsonl")
    """

    def __init__(self, swd_path: str):
        self._swd_path = swd_path

    def to_csv(self, output_path: str, include_header: bool = True):
        """
        导出为 CSV 格式

        :param output_path: 输出文件路径
        :param include_header: 是否包含表头
        """
        reader = SwdReader.from_file(self._swd_path)
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if reader.data_type == SWD_DTYPE_SCALAR:
                    if include_header:
                        writer.writerow(["step", "value", "timestamp"])
                    for record in reader.read_all():
                        writer.writerow([record.step, record.value, record.timestamp])
                elif reader.data_type == SWD_DTYPE_MEDIA_REF:
                    if include_header:
                        writer.writerow(["step", "timestamp", "object_key", "media_type", "width", "height", "file_size"])
                    for record in reader.read_all():
                        writer.writerow([
                            record.step, record.timestamp, record.object_key_str,
                            record.media_type, record.width, record.height, record.file_size,
                        ])
        finally:
            reader.close()

    def to_jsonl(self, output_path: str):
        """
        导出为 JSONL 格式

        :param output_path: 输出文件路径
        """
        reader = SwdReader.from_file(self._swd_path)
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                if reader.data_type == SWD_DTYPE_SCALAR:
                    for record in reader.read_all():
                        line = json.dumps({
                            "step": record.step,
                            "value": record.value,
                            "timestamp": record.timestamp,
                        })
                        f.write(line + "\n")
                elif reader.data_type == SWD_DTYPE_MEDIA_REF:
                    for record in reader.read_all():
                        line = json.dumps({
                            "step": record.step,
                            "timestamp": record.timestamp,
                            "object_key": record.object_key_str,
                            "media_type": record.media_type,
                            "width": record.width,
                            "height": record.height,
                            "file_size": record.file_size,
                        })
                        f.write(line + "\n")
        finally:
            reader.close()

    def to_parquet(self, output_path: str):
        """
        导出为 Parquet 格式（需要 pyarrow）

        :param output_path: 输出文件路径
        """
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            raise ImportError("pyarrow is required for Parquet export: pip install pyarrow")

        reader = SwdReader.from_file(self._swd_path)
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            records = reader.read_all()

            if reader.data_type == SWD_DTYPE_SCALAR:
                table = pa.table({
                    "step": pa.array([r.step for r in records], type=pa.int64()),
                    "value": pa.array([r.value for r in records], type=pa.float64()),
                    "timestamp": pa.array([r.timestamp for r in records], type=pa.int64()),
                })
            elif reader.data_type == SWD_DTYPE_MEDIA_REF:
                table = pa.table({
                    "step": pa.array([r.step for r in records], type=pa.int64()),
                    "timestamp": pa.array([r.timestamp for r in records], type=pa.int64()),
                    "object_key": pa.array([r.object_key_str for r in records]),
                    "media_type": pa.array([r.media_type for r in records], type=pa.uint8()),
                    "width": pa.array([r.width for r in records], type=pa.uint16()),
                    "height": pa.array([r.height for r in records], type=pa.uint16()),
                    "file_size": pa.array([r.file_size for r in records], type=pa.uint32()),
                })
            else:
                raise ValueError(f"Unknown data type: {reader.data_type}")

            pq.write_table(table, output_path)
        finally:
            reader.close()
