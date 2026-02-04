"""
@file: writer.py
@description: SwdWriter - .swd 文件写入器

将标量数据或媒体索引数据追加写入到本地 .swd 文件。
每次 append 操作返回写入的 bytes，供上层直接发送到 COS AppendObject。
"""

import os
import math
from typing import Optional, IO, Any

from .format import (
    SWD_HEADER_SIZE,
    SWD_DTYPE_SCALAR,
    SWD_DTYPE_MEDIA_REF,
    SWD_SCALAR_RECORD_SIZE,
    SWD_MEDIA_RECORD_SIZE,
    SWD_MEDIA_TYPE_IMAGE,
    SwdHeader,
    ScalarRecord,
    MediaIndexRecord,
    get_record_size,
)


class SwdWriter:
    """
    .swd 文件写入器

    用法:
        writer = SwdWriter(
            file_path="data/m_000.swd",
            data_type=SWD_DTYPE_SCALAR,
            experiment_id=b"\\x01\\x02...",
            metric_id=0,
        )
        writer.open()
        raw_bytes = writer.append_scalar(step=0, value=0.5, timestamp=1234567890)
        # raw_bytes 可以直接发送到 COS
        writer.close()
    """

    def __init__(
        self,
        file_path: str,
        data_type: int,
        experiment_id: bytes,
        metric_id: int,
        sequence: int = 0,
        start_index: int = 0,
    ):
        """
        :param file_path: 本地 .swd 文件路径
        :param data_type: 数据类型 (SWD_DTYPE_SCALAR 或 SWD_DTYPE_MEDIA_REF)
        :param experiment_id: 实验 UUID bytes (最多 16 bytes)
        :param metric_id: 指标 ID
        :param sequence: 分片序号 (>5GB 时递增)
        :param start_index: 本文件第一条记录的全局索引
        """
        self._file_path = file_path
        self._data_type = data_type
        self._experiment_id = experiment_id
        self._metric_id = metric_id
        self._sequence = sequence
        self._start_index = start_index
        self._record_size = get_record_size(data_type)
        self._fp: Optional[IO[Any]] = None
        self._record_count = 0
        self._header_written = False

    @property
    def file_path(self) -> str:
        return self._file_path

    @property
    def data_type(self) -> int:
        return self._data_type

    @property
    def record_size(self) -> int:
        return self._record_size

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def file_size(self) -> int:
        """当前文件大小（bytes）"""
        return SWD_HEADER_SIZE + self._record_count * self._record_size

    def open(self) -> bytes:
        """
        打开文件并写入 header。如果文件已存在，会追加到文件末尾。
        :return: header 的 bytes（仅新建文件时返回 64 字节，已有文件返回空 bytes）
        """
        if self._fp is not None:
            raise RuntimeError("Writer is already open")

        if os.path.exists(self._file_path):
            return self._open_existing()
        else:
            return self._create_new()

    def _create_new(self) -> bytes:
        """创建新文件并写入 header"""
        header_bytes = SwdHeader.pack(
            data_type=self._data_type,
            record_size=self._record_size,
            experiment_id=self._experiment_id,
            metric_id=self._metric_id,
            sequence=self._sequence,
            start_index=self._start_index,
        )
        self._fp = open(self._file_path, "xb")
        self._fp.write(header_bytes)
        self._fp.flush()
        self._header_written = True
        self._record_count = 0
        return header_bytes

    def _open_existing(self) -> bytes:
        """打开已有文件，验证 header 并定位到文件末尾"""
        file_size = os.path.getsize(self._file_path)
        if file_size < SWD_HEADER_SIZE:
            raise ValueError(f"File too small for valid .swd: {file_size} bytes")

        self._fp = open(self._file_path, "r+b")
        header_data = self._fp.read(SWD_HEADER_SIZE)
        header = SwdHeader.unpack(header_data)

        if header.data_type != self._data_type:
            self._fp.close()
            self._fp = None
            raise ValueError(
                f"Data type mismatch: file has {header.data_type}, expected {self._data_type}"
            )

        # 计算已有记录数，截断不完整的最后一条记录
        data_bytes = file_size - SWD_HEADER_SIZE
        valid_records = data_bytes // self._record_size
        remainder = data_bytes % self._record_size
        if remainder != 0:
            valid_size = SWD_HEADER_SIZE + valid_records * self._record_size
            self._fp.truncate(valid_size)

        self._fp.seek(0, 2)  # 跳到文件末尾
        self._record_count = valid_records
        self._header_written = True
        return b""

    def write_header(self) -> bytes:
        """
        单独写入 header（用于需要分步操作的场景）
        :return: header bytes
        """
        if self._header_written:
            raise RuntimeError("Header already written")
        return self.open()

    def append_scalar(self, step: int, value: float, timestamp: int) -> bytes:
        """
        追加一条标量记录

        :param step: 步数
        :param value: 标量值 (支持 NaN, Inf)
        :param timestamp: 时间戳 (epoch microseconds)
        :return: 写入的 24 字节 bytes，供上层发送到 COS
        """
        if self._fp is None:
            raise RuntimeError("Writer is not open, call open() first")
        if self._data_type != SWD_DTYPE_SCALAR:
            raise TypeError(f"Cannot append scalar to data_type={self._data_type}")

        record_bytes = ScalarRecord.pack(step, value, timestamp)
        self._fp.write(record_bytes)
        self._record_count += 1
        return record_bytes

    def append_media_index(
        self,
        step: int,
        timestamp: int,
        object_key: bytes,
        media_type: int = SWD_MEDIA_TYPE_IMAGE,
        width: int = 0,
        height: int = 0,
        file_size: int = 0,
    ) -> bytes:
        """
        追加一条媒体索引记录

        :param step: 步数
        :param timestamp: 时间戳 (epoch microseconds)
        :param object_key: 对象存储的 key (最多 48 bytes)
        :param media_type: 媒体类型
        :param width: 宽度 (图片/视频)
        :param height: 高度 (图片/视频)
        :param file_size: 文件大小 (bytes)
        :return: 写入的 80 字节 bytes，供上层发送到 COS
        """
        if self._fp is None:
            raise RuntimeError("Writer is not open, call open() first")
        if self._data_type != SWD_DTYPE_MEDIA_REF:
            raise TypeError(f"Cannot append media index to data_type={self._data_type}")

        record_bytes = MediaIndexRecord.pack(
            step=step,
            timestamp=timestamp,
            object_key=object_key,
            media_type=media_type,
            width=width,
            height=height,
            file_size=file_size,
        )
        self._fp.write(record_bytes)
        self._record_count += 1
        return record_bytes

    def flush(self):
        """刷新文件缓冲区到磁盘"""
        if self._fp is not None:
            self._fp.flush()
            try:
                os.fsync(self._fp.fileno())
            except OSError:
                pass

    def close(self):
        """关闭文件"""
        if self._fp is not None:
            self.flush()
            self._fp.close()
            self._fp = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
